#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JSONL 评论文件格式校验器（规格 §5.3）。

用于交付前自检，或接收别人给的数据时先验一遍。

用法:
  python crawler/validate_jsonl.py 100215674663.jsonl 100300314335.jsonl
  python crawler/validate_jsonl.py data/raw/*.jsonl
  python crawler/validate_jsonl.py --quiet data/raw/*.jsonl

退出码: 0 全部通过, 1 存在错误
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from config.settings import load_products
except Exception:  # noqa: BLE001 —— 单独拿脚本去用时不强依赖项目
    load_products = None

FIELDS = (
    "review_id", "product_id", "brand", "model", "is_target",
    "score", "content", "create_time", "source", "crawl_time",
)

TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class Problem:
    __slots__ = ("line", "msg")

    def __init__(self, line: int, msg: str):
        self.line = line
        self.msg = msg

    def __str__(self) -> str:
        return f"    第 {self.line} 行: {self.msg}"


def expected_products() -> dict[str, dict]:
    """从 config/products.yaml 读期望的 SKU -> 元数据映射。"""
    if load_products is None:
        return {}
    try:
        return {p["product_id"]: p for p in load_products()}
    except Exception:  # noqa: BLE001
        return {}


def check_file(path: Path, expected: dict[str, dict], quiet: bool) -> int:
    problems: list[Problem] = []
    ids: dict[str, int] = {}
    dup = 0
    n = 0
    scores = {i: 0 for i in range(1, 6)}
    empty_content = 0
    by_product: dict[str, int] = {}

    try:
        fh = path.open(encoding="utf-8-sig")  # utf-8-sig 兼容带 BOM 的文件
    except OSError as e:
        print(f"  第 0 行: 打不开文件: {e}")
        return 1

    with fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            n += 1

            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                problems.append(Problem(lineno, f"不是合法 JSON ({e.msg}, 列 {e.colno})"))
                continue

            if not isinstance(d, dict):
                problems.append(Problem(lineno, f"应该是 JSON 对象，实际是 {type(d).__name__}"))
                continue

            missing = [k for k in FIELDS if k not in d]
            extra = [k for k in d if k not in FIELDS]
            if missing:
                problems.append(Problem(lineno, f"缺少字段 {missing}"))
            if extra:
                problems.append(Problem(lineno, f"多余字段 {extra}（应该只有 10 个）"))
            if missing:
                continue

            # ---- 类型检查 ----
            for k in ("review_id", "product_id", "brand", "model", "content",
                      "create_time", "source", "crawl_time"):
                if not isinstance(d[k], str):
                    problems.append(
                        Problem(lineno, f"{k} 应为字符串，实际 {type(d[k]).__name__}: {d[k]!r}")
                    )

            if not isinstance(d["is_target"], bool):
                problems.append(
                    Problem(lineno, f"is_target 应为布尔 true/false，实际 {d['is_target']!r}")
                )

            if isinstance(d["score"], bool) or not isinstance(d["score"], int):
                problems.append(
                    Problem(lineno, f"score 应为数字 1-5，实际 {type(d['score']).__name__}: {d['score']!r}")
                )
            elif not 1 <= d["score"] <= 5:
                problems.append(Problem(lineno, f"score 超出 1-5: {d['score']}"))

            for k in ("create_time", "crawl_time"):
                if isinstance(d[k], str) and d[k] and not TIME_RE.match(d[k]):
                    problems.append(
                        Problem(lineno, f"{k} 时间格式应为 YYYY-MM-DD HH:MM:SS，实际 {d[k]!r}")
                    )

            # ---- 与 products.yaml 一致性 ----
            pid = d["product_id"]
            if expected:
                if pid not in expected:
                    problems.append(
                        Problem(lineno, f"product_id {pid!r} 不在项目配置里，期望 {list(expected)}")
                    )
                else:
                    exp = expected[pid]
                    if d["brand"] != exp["brand"]:
                        problems.append(
                            Problem(lineno, f"brand 应为 {exp['brand']!r}，实际 {d['brand']!r}")
                        )
                    if d["model"] != exp["model"]:
                        problems.append(
                            Problem(lineno, f"model 应为 {exp['model']!r}，实际 {d['model']!r}")
                        )
                    if d["is_target"] != bool(exp["is_target"]):
                        problems.append(
                            Problem(lineno, f"is_target 应为 {bool(exp['is_target'])}，实际 {d['is_target']}")
                        )

            # ---- 统计 ----
            by_product[pid] = by_product.get(pid, 0) + 1
            if isinstance(d["score"], int) and 1 <= d["score"] <= 5:
                scores[d["score"]] += 1
            if not str(d["content"]).strip():
                empty_content += 1

            rid = d["review_id"]
            if rid in ids:
                dup += 1
                if dup <= 5:
                    problems.append(
                        Problem(lineno, f"review_id 重复: {rid!r}（首次出现在第 {ids[rid]} 行）")
                    )
            else:
                ids[rid] = lineno

    # ---- 报告 ----
    name = path.name
    ok = not problems and n > 0
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}")

    if n == 0:
        print("    文件为空或没有有效行")
        return 1

    print(f"    条数 {n} | 唯一 review_id {len(ids)} | 重复 {dup} | 空内容 {empty_content}")
    print(f"    星级分布 " + " / ".join(f"{k}星 {v}" for k, v in scores.items()))
    print(f"    按 product_id: {by_product}")

    if problems:
        print(f"    发现 {len(problems)} 个问题（最多显示前 20 个）:")
        for p in problems[:20]:
            print(p)

    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="校验评论 JSONL 格式")
    ap.add_argument("files", nargs="*", help="要校验的 JSONL 文件（支持通配符）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论")
    args = ap.parse_args()

    paths: list[Path] = []
    for pattern in args.files or ["data/raw/*.jsonl"]:
        matched = [Path(p) for p in glob.glob(pattern)]
        paths.extend(matched if matched else [Path(pattern)])

    if not paths:
        print("没找到要校验的文件", file=sys.stderr)
        return 1

    expected = expected_products()
    if expected and not args.quiet:
        print(f"期望的商品: {[(k, v['brand'], v['model']) for k, v in expected.items()]}\n")

    rc = 0
    total = 0
    for p in sorted(set(paths)):
        if not p.exists():
            print(f"❌ {p} 不存在")
            rc = 1
            continue
        total += 1
        if check_file(p, expected, args.quiet) != 0:
            rc = 1
        print()

    if rc == 0:
        print(f"✅ 校验通过（{total} 个文件）")
    else:
        print(f"❌ 校验失败（{total} 个文件中存在错误）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
