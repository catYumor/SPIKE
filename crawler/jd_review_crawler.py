#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SPIKE 评论采集器（对应规格 §5 数据采集模块）。

设计要点：provider 可切换，输出的 JSONL 结构统一（规格 §5.3），
所以下游 Job 01–04 不关心数据从哪来。

    provider="jd"    真实京东接口（需 cookie / 签名，见 docs/实现偏差记录.md D2）
    provider="local" 读取现成 JSONL 或公开数据集
    provider="mock"  按 config/aspects.yaml 合成评论，条数可控，用于打通流水线

用法：
    python crawler/jd_review_crawler.py --provider mock
    python crawler/jd_review_crawler.py --provider mock --count 520
    python crawler/jd_review_crawler.py --provider local --input-dir data/raw_in
    python crawler/jd_review_crawler.py --provider jd --cookie-file crawler/.secrets/cookies.txt
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (  # noqa: E402
    CRAWLER_DIR,
    load_aspects,
    load_crawl_config,
    load_products,
    load_sentiment_rules,
)

# 规格 §5.3 规定的 10 个字段，顺序即输出顺序
FIELDS = (
    "review_id",
    "product_id",
    "brand",
    "model",
    "is_target",
    "score",
    "content",
    "create_time",
    "source",
    "crawl_time",
)

DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"


# ============================================================ providers


def _mock_reviews(product: dict, count: int, seed: int) -> list[dict]:
    """按属性字典合成评论。sentiment 与星级保持一致，便于下游校验逻辑。"""
    rng = random.Random(seed)
    aspects = load_aspects()
    rules = load_sentiment_rules()

    pos_tpl = [
        "{w}很好，用着挺满意",
        "{w}表现不错，超出预期",
        "{w}是我最满意的一点",
        "整体不错，{w}尤其好",
        "{w}给力，推荐",
    ]
    neg_tpl = [
        "{w}太差了，有点失望",
        "{w}一般般，不如宣传的",
        "{w}让我很后悔买这个",
        "别的还行，就是{w}不行",
        "{w}拉胯，考虑退货",
    ]
    neu_tpl = [
        "{w}中规中矩吧",
        "{w}就那样，谈不上好坏",
        "{w}没什么特别感觉",
    ]

    out = []
    now = datetime.now()
    for i in range(count):
        asp = rng.choice(aspects)
        word = rng.choice(asp["synonyms"])
        roll = rng.random()
        if roll < 0.62:
            tpl, score, _ = rng.choice(pos_tpl), rng.choice([4, 5]), "positive"
        elif roll < 0.85:
            tpl, score, _ = rng.choice(neg_tpl), rng.choice([1, 2]), "negative"
        else:
            tpl, score, _ = rng.choice(neu_tpl), 3, "neutral"

        # 偶尔拼两句，让长度分布更真实
        content = tpl.format(w=word)
        if rng.random() < 0.35:
            extra = rng.choice(aspects)
            content += "，" + rng.choice(pos_tpl + neg_tpl).format(w=rng.choice(extra["synonyms"]))

        created = now - timedelta(days=rng.randint(1, 900), seconds=rng.randint(0, 86400))
        out.append(
            {
                "review_id": f"jd_{product['product_id']}_{100000000 + i}",
                "product_id": product["product_id"],
                "brand": product["brand"],
                "model": product["model"],
                "is_target": bool(product["is_target"]),
                "score": score,
                "content": content,
                "create_time": created.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "mock",
                "crawl_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return out


def _local_reviews(product: dict, input_dir: Path) -> list[dict]:
    """从本地 JSONL 读取，补齐 product_id 等元数据。"""
    path = input_dir / f"{product['product_id']}.jsonl"
    if not path.exists():
        print(f"  [跳过] 找不到 {path}")
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            d.setdefault("review_id", f"local_{product['product_id']}_{len(out)}")
            d.setdefault("product_id", product["product_id"])
            d.setdefault("brand", product["brand"])
            d.setdefault("model", product["model"])
            d.setdefault("is_target", bool(product["is_target"]))
            d.setdefault("score", 3)
            d.setdefault("content", "")
            d.setdefault("create_time", "")
            d.setdefault("source", "local")
            d.setdefault("crawl_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            out.append(d)
    return out


def _load_cookies(cookie_file: Path | None) -> dict[str, str]:
    """读取 F12 复制出来的 Cookie 头，转成 dict。

    文件内容形如 `__jda=xxx; thor=xxx; pin=xxx; ...`，
    也兼容整段 "Copy as cURL"（自动从 -H 'Cookie: ...' 里提取）。
    """
    if cookie_file is None or not cookie_file.exists():
        return {}
    raw = cookie_file.read_text(encoding="utf-8", errors="ignore")

    m = re.search(r"-H\s+['\"]Cookie:\s*([^'\"]+)['\"]", raw)
    if m:
        raw = m.group(1)
    if raw.strip().lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1]

    out: dict[str, str] = {}
    for part in raw.replace("\n", " ").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def _jd_reviews(product: dict, cfg: dict, cookie_file: Path | None) -> list[dict]:
    """真实京东接口采集（规格 §5）。

    端点：https://club.jd.com/comment/skuProductPageComments.action
      —— 注意是 skuProductPageComments，不是 productPageComments
         （后者现在恒返回 {}，前者每页 10 条、maxPage 一般 100）

    需要登录 cookie：不登录会返回 601「活动太火爆了」。
    签名由京东前端算，但这个接口本身不走签名网关，纯 HTTP 即可。

    参数（均来自 config/products.yaml 的 crawl 段）：
      page_size / min_reviews_per_product / request_interval_sec / retry
    """
    import requests

    cookies = _load_cookies(cookie_file)
    if not cookies:
        print("  [警告] 没有读到 cookie，大概率会被风控拒绝（601）")
        print(f"         请把浏览器 Cookie 存到 {cookie_file}")
    elif not ({"thor", "pin"} & set(cookies)):
        print("  [警告] cookie 里没有 thor/pin，可能不是登录态")

    sku = product["product_id"]
    page_size = int(cfg.get("page_size", 10))
    target = int(cfg.get("min_reviews_per_product", 500))
    interval = float(cfg.get("request_interval_sec", 1.5))
    retries = int(cfg.get("retry", 3))
    backoff = float(cfg.get("retry_backoff_sec", 5))
    sort_type = int(cfg.get("sort_type", 6))  # 6 = 按时间排序

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://item.jd.com/{sku}.html",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )
    session.cookies.update(cookies)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out: list[dict] = []
    page = 0
    max_page = None

    while True:
        url = (
            "https://club.jd.com/comment/skuProductPageComments.action"
            f"?callback=fetchJSON_comment98&productId={sku}"
            f"&score=0&sortType={sort_type}&page={page}&pageSize={page_size}"
            f"&isShadowSku=0&fold=1"
        )

        # 单页请求：网络错误、非 200、以及「活动太火爆」这类纯文本限流
        # 都要退避重试，间隔逐次增大
        data = None
        for attempt in range(1, retries + 1):
            try:
                resp = session.get(url, timeout=25)
            except Exception as e:  # noqa: BLE001
                print(f"    [重试 {attempt}/{retries}] {type(e).__name__}: {e}")
                time.sleep(backoff * attempt)
                continue

            if resp.status_code != 200:
                print(f"    [重试 {attempt}/{retries}] HTTP {resp.status_code}")
                time.sleep(backoff * attempt)
                continue

            body = resp.text.strip()
            m = re.match(r"^[\w$.]+\((.*)\)\s*;?\s*$", body, re.S)
            payload = m.group(1) if m else body
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                # 京东限流时会直接返回纯文本「活动太火爆，请稍微再试」
                print(
                    f"    [限流 {attempt}/{retries}] {body[:30]}，"
                    f"等待 {backoff * attempt:.0f}s"
                )
                data = None
                time.sleep(backoff * attempt)
                continue
            break

        if data is None:
            print(f"    [失败] 第 {page} 页重试 {retries} 次仍不通，停止（已得 {len(out)} 条）")
            break

        # 风控 / 校验失败
        code = str(data.get("code", ""))
        if code in {"601", "605"}:
            print(f"    [风控] code={code} {data.get('msg') or data.get('echo')}")
            print("           等一段时间再试，或更换 cookie / 网络")
            break
        if code and code != "0" and not data.get("comments"):
            print(f"    [异常] code={code} {data.get('msg') or data.get('echo')}")
            break

        if max_page is None:
            max_page = int(data.get("maxPage") or 0)
            summary = data.get("productCommentSummary") or {}
            print(
                f"    总评价 {summary.get('commentCountStr', '?')}，"
                f"好评率 {summary.get('goodRateShow', '?')}%，"
                f"maxPage={max_page}"
            )

        comments = data.get("comments") or []
        if not comments:
            print(f"    第 {page} 页无数据，结束")
            break

        for c in comments:
            out.append(
                {
                    "review_id": str(c.get("id") or c.get("idStr") or ""),
                    # 注意：评论里的 referenceId 也是 SKU，但统一用查询用的 SKU，
                    # 避免因子 SKU 造成统计口径不一致
                    "product_id": sku,
                    "brand": product["brand"],
                    "model": product["model"],
                    "is_target": bool(product["is_target"]),
                    "score": c.get("score"),
                    "content": c.get("content") or "",
                    "create_time": c.get("creationTime") or "",
                    "source": "jd",
                    "crawl_time": now,
                }
            )

        print(f"    第 {page + 1}/{max_page or '?'} 页 +{len(comments)} 条，累计 {len(out)}")
        page += 1

        if len(out) >= target:
            print(f"    已达目标 {target} 条，停止")
            break
        if max_page and page >= max_page:
            print("    已到最后一页")
            break
        time.sleep(interval)

    return out[:target] if target else out


# ============================================================ 输出


def write_jsonl(reviews: list[dict], path: Path) -> int:
    """按 FIELDS 顺序写 JSONL，返回写入条数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in reviews:
            f.write(json.dumps({k: r.get(k) for k in FIELDS}, ensure_ascii=False) + "\n")
    return len(reviews)


def dedupe(reviews: list[dict]) -> tuple[list[dict], int]:
    seen = set()
    out = []
    for r in reviews:
        rid = r.get("review_id")
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out, len(reviews) - len(out)


def print_stats(product: dict, reviews: list[dict], dup: int, raw_count: int) -> None:
    tag = "目标产品" if product["is_target"] else "竞品"
    dist = {i: 0 for i in range(1, 6)}
    empty = 0
    for r in reviews:
        try:
            dist[int(r.get("score"))] += 1
        except (TypeError, ValueError):
            pass
        if not str(r.get("content") or "").strip():
            empty += 1

    print(f"  产品 {product['product_id']}（{product['brand']} {product['model']}，{tag}）")
    print(
        f"    抓到 {raw_count} 条 | "
        + " / ".join(f"{k}星 {v}" for k, v in dist.items())
    )
    print(f"    去重后 {len(reviews)} 条（重复 {dup}）| 空内容 {empty} 条")
    if reviews:
        times = sorted(str(r.get("create_time") or "") for r in reviews if r.get("create_time"))
        if times:
            print(f"    时间范围 {times[0][:7]} ~ {times[-1][:7]}")


# ============================================================ 主流程


def collect(provider: str, args) -> None:
    products = load_products()
    cfg = load_crawl_config()
    DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)

    # --count 对所有 provider 都生效，便于先小批量试跑
    if args.count:
        cfg["min_reviews_per_product"] = args.count

    print(f"=== SPIKE 采集（provider={provider}）===")
    print(f"  产品数: {len(products)}  每产品目标: {cfg['min_reviews_per_product']} 条")
    print()

    total = 0
    for product in products:
        if provider == "mock":
            count = args.count or cfg["min_reviews_per_product"]
            raw = _mock_reviews(product, count, seed=abs(hash(product["product_id"])) % (2**31))
        elif provider == "local":
            raw = _local_reviews(product, Path(args.input_dir))
        else:
            cookie = Path(args.cookie_file) if args.cookie_file else (CRAWLER_DIR / ".secrets" / "cookies.txt")
            raw = _jd_reviews(product, cfg, cookie)

        clean, dup = dedupe(raw)
        path = DATA_RAW_DIR / f"{product['product_id']}.jsonl"
        n = write_jsonl(clean, path)
        total += n
        print_stats(product, clean, dup, len(raw))
        print(f"    -> {path.relative_to(PROJECT_ROOT)}")
        print()

        if provider == "jd":
            time.sleep(cfg["request_interval_sec"])

    print(f"=== 完成：共 {total} 条 ===")
    print(f"  下一步: bash scripts/upload_to_hdfs.sh")


def main() -> int:
    ap = argparse.ArgumentParser(description="SPIKE 评论采集器")
    ap.add_argument("--provider", choices=["jd", "local", "mock"], default="mock")
    ap.add_argument("--count", type=int, help="mock provider 每产品生成条数")
    ap.add_argument("--input-dir", default="data/raw_in", help="local provider 读取目录")
    ap.add_argument("--cookie-file", help="jd provider 的 cookie 文件")
    args = ap.parse_args()

    try:
        collect(args.provider, args)
    except NotImplementedError as e:
        print(f"\n[未实现] {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
