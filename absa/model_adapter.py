# -*- coding: utf-8 -*-
"""ABSA 统一适配层（对应规格 §8.2 / §9.2）。

对上层只暴露一个函数：

    extract_aspects(text) -> [
        {"aspect": "...", "sentiment": "positive|negative|neutral",
         "confidence": 0.0, "evidence": "..."}
    ]

内部两条路径：
  1. **模型路径**（主路径）：SiameseAOE / SiameseUIE 中文属性情感抽取模型。
     需要 torch + transformers，且模型已下载到本地目录。
     通过环境变量 SPIKE_ABSA_MODEL 指定模型目录；未设置或目录不存在则不启用。
  2. **规则路径**（回退）：absa/rule_fallback.py，纯词典 + jieba，永远可用。

模型加载必须放在 mapPartitions 内、每个分区只加载一次 —— 见
spark_jobs/02_absa_extract.py 的 process_partition()。
本模块用模块级缓存保证同一进程内只加载一次。
"""

from __future__ import annotations

import os
from pathlib import Path

from .rule_fallback import extract_aspects_rule

MODEL_DIR_ENV = "SPIKE_ABSA_MODEL"
LOW_CONFIDENCE = 0.45  # 低于此值的模型结果再叠加规则结果（规格 §8.2）

_model = None
_model_state = "uninitialized"  # uninitialized | unavailable | ready
_model_reason = ""


def _try_load_model():
    """尝试加载本地模型。任何失败都静默降级，不抛出。"""
    global _model, _model_state, _model_reason

    if _model_state != "uninitialized":
        return _model

    model_dir = os.environ.get(MODEL_DIR_ENV, "").strip()
    if not model_dir:
        _model_state, _model_reason = "unavailable", f"未设置 {MODEL_DIR_ENV}"
        return None
    if not Path(model_dir).exists():
        _model_state, _model_reason = "unavailable", f"目录不存在: {model_dir}"
        return None

    try:
        import torch  # noqa: F401
        from transformers import AutoModelForTokenClassification, AutoTokenizer
    except Exception as e:  # noqa: BLE001
        _model_state = "unavailable"
        _model_reason = f"缺少依赖: {type(e).__name__}: {e}"
        return None

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_dir)
        model = AutoModelForTokenClassification.from_pretrained(model_dir)
        model.eval()
        _model = {"tokenizer": tokenizer, "model": model, "dir": model_dir}
        _model_state, _model_reason = "ready", f"已加载 {model_dir}"
        return _model
    except Exception as e:  # noqa: BLE001
        _model_state = "unavailable"
        _model_reason = f"加载失败: {type(e).__name__}: {e}"
        return None


def _model_extract(bundle: dict, text: str) -> list[dict]:
    """调用 SiameseAOE 模型抽取。输出按 §9.2 统一成 aspect/sentiment 结构。

    注意：SiameseAOE 原始输出是「属性词-情感词」对，需要再做一次属性归并
    才能落到标准 aspect_name（那一步在 Job 03 做）。这里先原样给出属性词。
    """
    import torch

    tokenizer = bundle["tokenizer"]
    model = bundle["model"]
    max_len = 256

    enc = tokenizer(text, truncation=True, max_length=max_len, return_tensors="pt")
    with torch.no_grad():
        logits = model(**enc).logits[0]
    preds = logits.argmax(dim=-1).tolist()
    tokens = tokenizer.convert_ids_to_tokens(enc["input_ids"][0])

    # 用 BIO 标签把「属性片」聚成词。标签体系因模型而异，这里做通用兜底：
    # 任何非 O 标签的连续片段都视为候选，情感由标签名包含的 pos/neg 推断。
    pairs: list[dict] = []
    buf: list[str] = []
    buf_tags: list[str] = []
    id2label = getattr(model.config, "id2label", {})

    def flush():
        if not buf:
            return
        word = tokenizer.convert_tokens_to_string(buf).strip()
        tags = " ".join(buf_tags).lower()
        if word:
            if "pos" in tags:
                sentiment = "positive"
            elif "neg" in tags:
                sentiment = "negative"
            else:
                sentiment = "neutral"
            pairs.append(
                {
                    "aspect": word,
                    "sentiment": sentiment,
                    "confidence": 0.80,
                    "evidence": text[:200],
                }
            )
        buf.clear()
        buf_tags.clear()

    for tok, pid in zip(tokens, preds):
        tag = id2label.get(pid, str(pid))
        if tok in ("[CLS]", "[SEP]", "[PAD]"):
            flush()
            continue
        if str(tag).upper().startswith("O"):
            flush()
        else:
            buf.append(tok)
            buf_tags.append(str(tag))
    flush()
    return pairs[:5]


def extract_aspects(text: str) -> list[dict]:
    """统一抽取入口。模型可用走模型，否则走规则；模型结果置信度低时用规则补。"""
    if not text or not text.strip():
        return []

    bundle = _try_load_model()
    if bundle is not None:
        try:
            items = _model_extract(bundle, text)
            if items and all(i["confidence"] >= LOW_CONFIDENCE for i in items):
                return items[:5]
            # 低置信度：规则结果补齐（按属性名去重）
            seen = {i["aspect"] for i in items}
            for r in extract_aspects_rule(text):
                if r["aspect"] not in seen:
                    items.append(r)
            return items[:5]
        except Exception:  # noqa: BLE001
            pass

    return extract_aspects_rule(text)


def model_status() -> dict:
    """返回当前使用的路径，供作业日志和自检脚本展示。"""
    _try_load_model()
    return {
        "mode": "model" if _model_state == "ready" else "rule",
        "state": _model_state,
        "reason": _model_reason,
    }
