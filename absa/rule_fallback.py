# -*- coding: utf-8 -*-
"""规则回退抽取器（对应规格 §8.2 的 fallback 路径）。

不依赖任何深度学习模型，只用：
  - jieba 分词（切分句子、定位邻近词）
  - config/aspects.yaml 的同义词表（定位属性）
  - config/aspects.yaml 的情感词典 + 否定词 + 程度词（判断极性）

设计目标是「可解释、可复现、离线可跑」，因此结果是确定性的。

对外接口与 model_adapter 完全一致：
    extract_aspects_rule(text) -> [{"aspect","sentiment","confidence","evidence"}]
"""

from __future__ import annotations

import re
from functools import lru_cache

import jieba

from config.settings import load_aspects, load_sentiment_rules

# 句子切分：句号、问号、感叹号、分号、换行
_SENT_RE = re.compile(r"[。！？；;!?\n]+")
# 分句切分：逗号、顿号。切到这一层才能把「物流很快，但是客服态度差」分开判断
_SEG_RE = re.compile(r"[，,、]+")
# 证据文本长度上限（规格 §9.3：100—200 字）
_EVIDENCE_MAX = 200
# 每条评论最多保留的属性数（规格 §8.2）
MAX_ASPECTS_PER_REVIEW = 5


@lru_cache(maxsize=1)
def _dictionary():
    """构建 (同义词 -> 标准属性名) 映射与情感词典。只加载一次。"""
    aspects = load_aspects()
    rules = load_sentiment_rules()

    syn2aspect: dict[str, str] = {}
    for a in aspects:
        name = a["aspect_name"]
        syn2aspect[name] = name
        for s in a.get("synonyms", []):
            syn2aspect[s] = name
    # 长词优先匹配，避免「价格」被「价」抢先之类的问题
    keys = sorted(syn2aspect, key=len, reverse=True)

    return {
        "syn2aspect": syn2aspect,
        "keys": keys,
        "pos": list(rules.get("positive_words", [])),
        "neg": list(rules.get("negative_words", [])),
        "negation": list(rules.get("negation_words", [])),
        "degree": dict(rules.get("degree_words", {})),
    }


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENT_RE.split(text or "") if len(p.strip()) >= 2]


def _segments(sentence: str) -> list[str]:
    return [p.strip() for p in _SEG_RE.split(sentence) if len(p.strip()) >= 2]


def _score_clause(clause: str, d: dict) -> tuple[float, int]:
    """对单个短句打情感分。返回 (分数, 命中的情感词个数)。

    分数 > 0 偏正面，< 0 偏负面，0 为中性。
    程度词放大权重，否定词翻转极性。
    """
    score = 0.0
    hits = 0

    # 先按「词」扫描，长词优先，避免短词重复计入
    for word, polarity in [(w, 1.0) for w in d["pos"]] + [(w, -1.0) for w in d["neg"]]:
        start = 0
        while True:
            i = clause.find(word, start)
            if i < 0:
                break
            start = i + len(word)
            hits += 1

            # 程度词：紧邻前置
            mult = 1.0
            for deg, w in d["degree"].items():
                if clause[max(0, i - len(deg)): i] == deg:
                    mult = float(w)
                    break

            # 否定词：前 3 个字符内出现则翻转
            prefix = clause[max(0, i - 3): i]
            if any(neg in prefix for neg in d["negation"]):
                mult *= -1.0

            score += polarity * mult

    return score, hits


def _find_aspects(clause: str, d: dict) -> list[str]:
    """在短句中找出现的标准属性名，同一属性只算一次。"""
    found: list[str] = []
    for key in d["keys"]:
        if key in clause:
            name = d["syn2aspect"][key]
            if name not in found:
                found.append(name)
    return found


def extract_aspects_rule(text: str) -> list[dict]:
    """规则抽取主函数。

    返回: [{"aspect": str, "sentiment": "positive|negative|neutral",
            "confidence": float, "evidence": str}]
    """
    d = _dictionary()
    if not text or not text.strip():
        return []

    # 按属性聚合：同一属性在多个分句出现时，取证据最强的那一次
    best: dict[str, dict] = {}

    for sentence in _sentences(text):
        for seg in _segments(sentence):
            aspects = _find_aspects(seg, d)
            if not aspects:
                continue

            score, hits = _score_clause(seg, d)
            evidence = seg
            # 分句里没有情感词（如「价格便宜」）时，退一步用整句判断，
            # 免得把「价格便宜，性价比高，很划算」判成中性
            if score == 0:
                score, hits = _score_clause(sentence, d)
                if score != 0:
                    evidence = sentence

            if score > 0:
                sentiment, strength = "positive", score
            elif score < 0:
                sentiment, strength = "negative", -score
            else:
                sentiment, strength = "neutral", 0.0

            # 置信度：有情感词且分数高 => 高；纯中性 => 低
            if sentiment == "neutral":
                confidence = 0.30
            else:
                confidence = min(0.95, 0.55 + 0.10 * min(3, strength))

            evidence = evidence[:_EVIDENCE_MAX]

            for name in aspects:
                prev = best.get(name)
                # 证据强度更高（或首次出现）就替换
                key = (strength, len(evidence))
                if prev is None or key > prev["_key"]:
                    best[name] = {
                        "aspect": name,
                        "sentiment": sentiment,
                        "confidence": round(confidence, 3),
                        "evidence": evidence,
                        "_key": key,
                        "_hits": hits,
                    }

    # 按强度排序，截断到最多 5 个
    out = sorted(best.values(), key=lambda x: x["_key"], reverse=True)
    for item in out:
        item.pop("_key", None)
        item.pop("_hits", None)
    return out[:MAX_ASPECTS_PER_REVIEW]


def segment(text: str) -> list[str]:
    """jieba 分词，供调试/统计用（规格 §8.2 要求使用 jieba 词性标注）。"""
    return list(jieba.cut(text))
