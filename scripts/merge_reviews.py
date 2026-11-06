#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把补抓的差评/中评合并进主数据文件，按 review_id 去重。

典型用法：

    # 自动扫描 data/raw/*.diff.jsonl 和 SPIKE_京东评论_交付包/*.jsonl
    python scripts/merge_reviews.py --dry-run     # 先看会合并多少
    python scripts/merge_reviews.py

    # 指定文件
    python scripts/merge_reviews.py data/raw/100215674663.diff.jsonl

合并规则：
  - 按 `product_id` 归到对应商品的主文件 `data/raw/<sku>.jsonl`
  - `review_id` 已存在则跳过（所以重复跑同一个文件是安全的）
  - 字段做一次规范化和校验，不合格的行会被列出来但不写入
  - 写文件用「临时文件 + 原子替换」，中途失败不会破坏原数据

合并完接着跑：
    bash scripts/refresh_all.sh
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import load_products  # noqa: E402

DATA_RAW = PROJECT_ROOT / "data" / "raw"
PACKAGE_DIR = PROJECT_ROOT / "SPIKE_京东评论_交付包"
# 外部交付（微信/邮件收来的补充数据）按日期放这里，会自动扫描
INCOMING_DIR = PROJECT_ROOT / "data" / "incoming"

FIELDS = (
    "review_id", "product_id", "brand", "model", "is_target",
    "score", "content", "create_time", "source", "crawl_time",
)


# ------------------------------------------------------------------ 读取与校验


def normalize(row: dict) -> tuple[dict | None, str]:
    """规范化一行；返回 (行, 错误信息)。"""
    missing = [f for f in FIELDS if f not in row]
    if missing:
        return None, f"缺字段 {missing}"

    try:
        score = int(row["score"])
    except (TypeError, ValueError):
        return None, f"score 不是整数: {row.get('score')!r}"
    if not 1 <= score <= 5:
        return None, f"score 越界: {score}"

    content = str(row.get("content") or "").strip()
    if not content:
        return None, "content 为空"

    out = {
        "review_id": str(row["review_id"]).strip(),
        "product_id": str(row["product_id"]).strip(),
        "brand": str(row.get("brand") or "").strip(),
        "model": str(row.get("model") or "").strip(),
        "is_target": bool(row["is_target"]),
        "score": score,
        "content": content,
        "create_time": str(row.get("create_time") or "").strip(),
        "source": str(row.get("source") or "jd").strip(),
        "crawl_time": str(row.get("crawl_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    }
    if not out["review_id"]:
        return None, "review_id 为空"
    return out, ""


def read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    problems: list[str] = []
    with path.open(encoding="utf-8", errors="ignore") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                problems.append(f"{path.name}:{lineno} JSON 解析失败 ({e})")
                continue
            row, err = normalize(raw)
            if row is None:
                problems.append(f"{path.name}:{lineno} {err}")
                continue
            rows.append(row)
    return rows, problems


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def score_dist(rows: list[dict]) -> dict[int, int]:
    c = Counter(r["score"] for r in rows)
    return dict(sorted(c.items()))


def fmt_dist(d: dict[int, int]) -> str:
    return " / ".join(f"{k}星:{v}" for k, v in d.items()) or "空"


# ------------------------------------------------------------------ 主流程


def discover_inputs(explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(x).expanduser().resolve() for x in explicit]

    found: list[Path] = []
    # 0) data/incoming/** 下按日期存放的外部交付（推荐放这里，补充数据都自动扫）
    if INCOMING_DIR.exists():
        found += sorted(INCOMING_DIR.rglob("*.jsonl"))
    # 1) 交付目录里对方另外给的补充文件
    for pat in ("*.diff.jsonl", "*.补.jsonl", "*.diff"):
        found += sorted(PACKAGE_DIR.glob(pat))
    # 2) data/raw 下带 .diff 的文件
    for pat in ("*.diff.jsonl", "*.diff"):
        found += sorted(DATA_RAW.glob(pat))
    # 3) 交付目录里任何 jsonl（主文件重复合并也安全，会被全部去重跳过）
    found += sorted(PACKAGE_DIR.glob("*.jsonl"))
    # 4) data/raw 里其它副本（如采集器备份之外的手工文件）
    for p in sorted(DATA_RAW.glob("*.jsonl")):
        if ".mock-bak" in p.name or p.name.endswith(".tmp"):
            continue
        found.append(p)
    # 去掉已退役/备份文件
    found = [
        p for p in found
        if not p.name.startswith("_retired") and ".bak-" not in p.name
    ]
    # 去重 + 排除主数据文件本身
    main_files = {DATA_RAW / f"{pid}.jsonl" for pid in {r["product_id"] for r in load_products()}}
    uniq: list[Path] = []
    seen: set[Path] = set()
    for p in found:
        if p in seen or not p.is_file() or p in main_files:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(description="合并补抓的评论数据（按 review_id 去重）")
    ap.add_argument("files", nargs="*", help="要合并的 JSONL 文件；不写则自动扫描")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = ap.parse_args()

    products = load_products()
    by_pid = {p["product_id"]: p for p in products}

    inputs = discover_inputs(args.files)
    if not inputs:
        print("没有找到要合并的文件。")
        print(f"  已查找: {PACKAGE_DIR}/*.jsonl、{DATA_RAW}/*.diff.jsonl")
        print("  也可以直接指定: python scripts/merge_reviews.py 路径.jsonl")
        return 1

    print("=" * 66)
    print("  待合并文件")
    print("=" * 66)
    incoming: dict[str, list[dict]] = {pid: [] for pid in by_pid}
    bad_lines: list[str] = []
    unknown_pid: Counter = Counter()

    for path in inputs:
        rows, problems = read_jsonl(path)
        bad_lines += problems
        keep = 0
        for r in rows:
            pid = r["product_id"]
            if pid not in by_pid:
                unknown_pid[pid] += 1
                continue
            # 品牌/型号/身份以 products.yaml 为准，防止交付方填错
            meta = by_pid[pid]
            r["brand"] = meta["brand"]
            r["model"] = meta["model"]
            r["is_target"] = bool(meta["is_target"])
            incoming[pid].append(r)
            keep += 1
        extra = f"，跳过未知 product_id {sum(unknown_pid.values())}" if unknown_pid else ""
        print(f"  {path.name:<44} 有效 {keep:>4} 条{extra}")

    if bad_lines:
        print(f"\n  ⚠️ {len(bad_lines)} 行格式不合格，已忽略（最多显示 5 条）：")
        for b in bad_lines[:5]:
            print(f"     {b}")

    print()
    print("=" * 66)
    print("  合并结果")
    print("=" * 66)

    total_added = 0
    for pid, meta in by_pid.items():
        main_path = DATA_RAW / f"{pid}.jsonl"
        existing, _ = read_jsonl(main_path) if main_path.exists() else ([], [])
        merged = {r["review_id"]: r for r in existing}
        before = len(merged)

        added = dup = 0
        for r in incoming.get(pid, []):
            if r["review_id"] in merged:
                dup += 1
                continue
            merged[r["review_id"]] = r
            added += 1

        final_rows = list(merged.values())
        # 保持时间顺序，方便人工看
        final_rows.sort(key=lambda r: (r["create_time"] or "", r["review_id"]))
        after = len(final_rows)

        print(f"\n  {pid}  {meta['brand']} {meta['model']}"
              f"（{'目标' if meta['is_target'] else '竞品'}）")
        print(f"    原有 {before} 条 → 新增 {added} 条（重复跳过 {dup}）→ 合计 {after} 条")
        print(f"    星级分布  前: {fmt_dist(score_dist(existing))}")
        print(f"              后: {fmt_dist(score_dist(final_rows))}")

        neg_before = sum(1 for r in existing if r["score"] <= 2)
        neg_after = sum(1 for r in final_rows if r["score"] <= 2)
        print(f"    1–2 星    前: {neg_before} 条  →  后: {neg_after} 条")

        if added == 0:
            print(f"    没有新数据，不写文件")
            continue
        if args.dry_run:
            print(f"    [dry-run] 本应写入 {main_path.relative_to(PROJECT_ROOT)}")
        else:
            if main_path.exists():
                bak = main_path.with_suffix(
                    f".jsonl.bak-{datetime.now():%Y%m%d-%H%M%S}"
                )
                # 注意：/mnt/d 是 DrvFs，copy2 会尝试复制时间戳而报
                # PermissionError，所以只用 copyfile 复制内容。
                shutil.copyfile(main_path, bak)
                print(f"    已备份旧文件 → {bak.name}")
            write_jsonl(main_path, final_rows)
            print(f"    已写入 {main_path.relative_to(PROJECT_ROOT)}")
        total_added += added

    print()
    print("=" * 66)
    if args.dry_run:
        print(f"  [dry-run] 总共会新增 {total_added} 条，未写任何文件")
    elif total_added:
        print(f"  合并完成，共新增 {total_added} 条")
        print("  下一步： bash scripts/refresh_all.sh")
    else:
        print("  没有新增数据（全部重复）")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
