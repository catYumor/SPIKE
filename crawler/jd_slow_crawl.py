#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""京东评论「慢速可续传」采集器。

背景
----
`crawler/jd_review_crawler.py --provider jd` 用的是正常节奏（3 秒一页）。
但京东 `skuProductPageComments` 接口的限流非常激进：
实测连续请求 2 页就会被封，返回纯文本「活动太火爆，请稍微再试」，
之后即使静默 90 秒、间隔 30 秒重试，仍然 8/8 被拒（响应 0.1 秒，是 WAF 直接拒绝）。

所以真实数据的唯一可行方式是：**极慢速 + 长时间运行 + 断点续传**。
这个脚本就是干这个的：

  - 每页之间间隔默认 30 秒
  - 被限流就等 10 分钟再试同一页，不退出
  - 每条评论立即追加写入 JSONL，进程被杀也不丢进度
  - 用状态文件记录下一页页码，重跑自动续传
  - 默认最多跑 6 小时，到点自动收工

用法
----
  # 后台常驻（推荐）
  nohup python crawler/jd_slow_crawl.py > logs/crawl.log 2>&1 &

  # 前台小批量试
  python crawler/jd_slow_crawl.py --max-minutes 5

  # 只看进度
  python crawler/jd_slow_crawl.py --status
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import CRAWLER_DIR, load_crawl_config, load_products  # noqa: E402

DATA_RAW = PROJECT_ROOT / "data" / "raw"
STATE_DIR = DATA_RAW / ".state"
COOKIE_FILE = CRAWLER_DIR / ".secrets" / "cookies.txt"

FIELDS = (
    "review_id", "product_id", "brand", "model", "is_target",
    "score", "content", "create_time", "source", "crawl_time",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_cookies() -> dict[str, str]:
    if not COOKIE_FILE.exists():
        return {}
    raw = COOKIE_FILE.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"-H\s+['\"]Cookie:\s*([^'\"]+)['\"]", raw)
    if m:
        raw = m.group(1)
    if raw.strip().lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1]
    out = {}
    for part in raw.replace("\n", " ").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def make_session(cookies: dict[str, str], sku: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Referer": f"https://item.jd.com/{sku}.html",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
    })
    s.cookies.update(cookies)
    return s


def jsonl_path(sku: str) -> Path:
    return DATA_RAW / f"{sku}.jsonl"


def count_rows(path: Path) -> tuple[int, int]:
    """统计文件里 jd 真实数据和其它来源（mock/local）的行数。

    返回 (jd 行数, 其它行数)。用于进度显示和数据隔离检查——
    mock 数据千万不能和真实数据混在一个文件里。
    """
    jd = other = 0
    if not path.exists():
        return 0, 0
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                src = json.loads(line).get("source")
            except json.JSONDecodeError:
                continue
            if src == "jd":
                jd += 1
            else:
                other += 1
    return jd, other


def quarantine_non_jd(sku: str) -> Path | None:
    """把非 jd 来源的数据（mock 等）挪到 .bak，返回备份路径。

    这样真实采集可以干净地开始，同时不丢已有 mock 数据。
    """
    path = jsonl_path(sku)
    jd, other = count_rows(path)
    if other <= 0:
        return None
    if jd == 0:
        # 整个文件都是 mock，直接改名备份
        backup = path.with_suffix(
            f".jsonl.mock-bak-{datetime.now():%Y%m%d-%H%M%S}"
        )
        path.rename(backup)
        return backup
    # 真的混了：只保留 jd 行，其余备份
    backup = path.with_suffix(f".jsonl.mixed-bak-{datetime.now():%Y%m%d-%H%M%S}")
    lines = []
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                if json.loads(line).get("source") != "jd":
                    lines.append(line if line.endswith("\n") else line + "\n")
            except json.JSONDecodeError:
                pass
    backup.write_text("".join(lines), encoding="utf-8")
    keep = []
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                if json.loads(line).get("source") == "jd":
                    keep.append(line if line.endswith("\n") else line + "\n")
            except json.JSONDecodeError:
                pass
    path.write_text("".join(keep), encoding="utf-8")
    return backup


def state_path(sku: str, score: int = 0) -> Path:
    """进度文件按 sku + 星级过滤分开存，避免补抓差评时和主进度互相覆盖。"""
    suffix = "" if not score else f".s{score}"
    return STATE_DIR / f"{sku}{suffix}.json"


def load_state(sku: str, score: int = 0) -> dict:
    p = state_path(sku, score)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"next_page": 0, "max_page": None, "ids": []}


def save_state(sku: str, state: dict, score: int = 0) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(sku, score).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )


def append_rows(sku: str, rows: list[dict]) -> None:
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    with jsonl_path(sku).open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: r.get(k) for k in FIELDS}, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def fetch_page(session: requests.Session, sku: str, page: int, page_size: int,
               sort_type: int, score: int = 0) -> tuple[str, object]:
    """返回 (状态, 数据)。状态: ok / limit / empty / bad / exc

    score=0 全部；1 差评；2 中评；3 一般；4 好评；5 全部好评。
    """
    url = ("https://club.jd.com/comment/skuProductPageComments.action"
           f"?callback=fetchJSON_comment98&productId={sku}&score={score}&sortType={sort_type}"
           f"&page={page}&pageSize={page_size}&isShadowSku=0&fold=1")
    try:
        r = session.get(url, timeout=30)
    except Exception as e:  # noqa: BLE001
        return "exc", f"{type(e).__name__}: {e}"

    if r.status_code != 200:
        return "exc", f"HTTP {r.status_code}"

    body = r.text.strip()
    if "活动太火爆" in body:
        return "limit", body[:30]

    m = re.match(r"^[\w$.]+\((.*)\)\s*;?\s*$", body, re.S)
    try:
        data = json.loads(m.group(1) if m else body)
    except json.JSONDecodeError:
        return "bad", body[:80]

    if not data.get("comments"):
        return "empty", data
    return "ok", data


def _ids_from_file(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rid = json.loads(line).get("review_id")
            except json.JSONDecodeError:
                continue
            if rid:
                ids.add(str(rid))
    return ids


def crawl_product(product: dict, cookies: dict[str, str], cfg: dict, args,
                  deadline: float) -> int:
    sku = product["product_id"]
    target = int(args.target or cfg.get("min_reviews_per_product", 500))
    page_size = int(cfg.get("page_size", 10))
    sort_type = int(cfg.get("sort_type", 5))
    score = int(getattr(args, "score", 0) or 0)

    # 页面间隔：优先用 --interval；否则按「每条评论间隔 × 每页条数」换算。
    # 京东每页固定 10 条，所以 8 秒/条 ≈ 80 秒/页。
    page_interval = (
        args.interval if args.interval is not None
        else args.review_interval * page_size
    )

    # 先把非 jd 来源的数据（mock 等）隔离出去，避免真实数据和模拟数据混在一起
    backup = quarantine_non_jd(sku)
    if backup:
        log(f"  ⚠️ 已有非 jd 数据，已备份到 {backup.name}，本次从空开始采集")

    state = load_state(sku, score)
    file_ids = _ids_from_file(jsonl_path(sku))
    state_ids = set(state.get("ids", []))
    if score:
        # 补抓特定星级：进度按该星级独立计数，去重仍然看整个文件
        known = file_ids | state_ids
        have = len(state_ids)
    else:
        if file_ids != state_ids:
            # 文件与状态文件不一致（例如刚备份过 mock），以实际文件为准
            state["ids"] = sorted(file_ids)
            known = set(file_ids)
        else:
            known = state_ids
        have = len(known)
    new_ids: set[str] = set()

    log(f"===== {sku} {product['brand']} {product['model']} "
        f"({ '目标' if product['is_target'] else '竞品'}) =====")
    if score:
        log(f"  星级过滤 score={score}（1 差评 / 2 中评 / 3 一般 / 4 好评）")
    log(f"  已采集 {have} 条，目标 {target} 条，从第 {state['next_page']} 页继续")
    if have >= target:
        log("  已达标，跳过")
        return have

    session = make_session(cookies, sku)
    page = int(state.get("next_page", 0))
    limit_hits = 0
    new_total = 0

    while have < target and time.time() < deadline:
        status, data = fetch_page(session, sku, page, page_size, sort_type, score)

        if status == "limit":
            limit_hits += 1
            wait = args.limit_wait * min(limit_hits, 4)
            log(f"  🚫 第 {page} 页被限流（第 {limit_hits} 次），等 {wait}s 后重试")
            time.sleep(wait)
            continue

        if status in ("exc", "bad"):
            log(f"  ⚠️ 第 {page} 页失败: {data}，等 {args.limit_wait}s")
            time.sleep(args.limit_wait)
            continue

        if status == "empty":
            log(f"  第 {page} 页无数据，{sku} 采集结束")
            break

        limit_hits = 0
        max_page = int(data.get("maxPage") or 0)
        state["max_page"] = max_page
        if page == 0 or state.get("next_page") in (0, None):
            summary = data.get("productCommentSummary") or {}
            log(f"  总评价 {summary.get('commentCountStr', '?')}，"
                f"好评率 {summary.get('goodRateShow', '?')}%，maxPage={max_page}")

        rows = []
        for c in data["comments"]:
            rid = str(c.get("id") or c.get("idStr") or "")
            if not rid or rid in known:
                continue
            known.add(rid)
            new_ids.add(rid)
            rows.append({
                "review_id": rid,
                "product_id": sku,
                "brand": product["brand"],
                "model": product["model"],
                "is_target": bool(product["is_target"]),
                "score": c.get("score"),
                "content": c.get("content") or "",
                "create_time": c.get("creationTime") or "",
                "source": "jd",
                "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        if rows:
            append_rows(sku, rows)
            have += len(rows)
            new_total += len(rows)

        state["ids"] = sorted(state_ids | new_ids if score else known)
        state["next_page"] = page + 1
        save_state(sku, state, score)

        page += 1
        if max_page and page >= max_page:
            log(f"  第 {page}/{max_page} 页 +{len(rows)} 条，累计 {have}｜已到最后一页")
            break

        # 每页之间随机抖动，避免固定间隔形成可识别的机器节奏
        delay = page_interval * random.uniform(1 - args.jitter, 1 + args.jitter)
        log(
            f"  第 {page}/{max_page or '?'} 页 +{len(rows)} 条，累计 {have}"
            f"｜{delay:.0f}s 后请求下一页"
        )
        time.sleep(delay)

    log(f"  {sku} 本次新增 {new_total} 条，总 {have} 条")
    return have


def show_status(products: list[dict], cfg: dict) -> None:
    print("采集进度（只统计 source=jd 的真实数据）")
    print("-" * 62)
    for p in products:
        sku = p["product_id"]
        n, other = count_rows(jsonl_path(sku))
        st = load_state(sku)
        target = cfg.get("min_reviews_per_product", 500)
        pct = min(100, n * 100 // target) if target else 0
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"  {sku} {p['brand']} {p['model']:<12} [{bar}] {n}/{target} "
              f"  下一页={st.get('next_page')}")
        if other:
            print(f"      另有 {other} 条非 jd 数据（mock/local），未计入，"
                  f"采集时会自动备份隔离")


def main() -> int:
    ap = argparse.ArgumentParser(description="京东评论慢速可续传采集")
    ap.add_argument(
        "--review-interval", type=float, default=8.0,
        help="每条评论的平均间隔秒数；页面间隔 = 该值 × 每页条数（默认 8 秒/条 ≈ 80 秒/页）",
    )
    ap.add_argument(
        "--interval", type=float, default=None,
        help="直接指定页间隔秒数，优先于 --review-interval",
    )
    ap.add_argument(
        "--jitter", type=float, default=0.3,
        help="间隔随机抖动比例，默认 0.3 表示 ±30%%",
    )
    ap.add_argument(
        "--limit-wait", type=float, default=3600,
        help="被限流后等待秒数，默认 3600（每次被拒后翻倍，封顶 4 倍）",
    )
    ap.add_argument("--target", type=int, help="每产品目标条数，默认取 crawl 配置")
    ap.add_argument("--max-minutes", type=float, default=480, help="最长运行分钟数，默认 480")
    ap.add_argument("--sku", help="只采某一个 SKU")
    ap.add_argument(
        "--score", type=int, default=0,
        help="星级过滤：0 全部（默认）/ 1 差评 / 2 中评 / 3 一般 / 4 好评。"
             "补抓差评时用 --score 1 --target 200",
    )
    ap.add_argument("--status", action="store_true", help="只看进度")
    ap.add_argument("--reset", action="store_true", help="清空进度重来")
    args = ap.parse_args()

    cfg = load_crawl_config()
    products = load_products()
    if args.sku:
        products = [p for p in products if p["product_id"] == args.sku]
        if not products:
            print(f"没有这个 SKU: {args.sku}", file=sys.stderr)
            return 1

    if args.status:
        show_status(products, cfg)
        return 0

    if args.reset:
        for p in products:
            state_path(p["product_id"], args.score).unlink(missing_ok=True)
            if not args.score:
                jsonl_path(p["product_id"]).unlink(missing_ok=True)
        log(f"已清空进度（score={args.score}）")

    cookies = load_cookies()
    if not cookies:
        print(f"[FAIL] 没有 cookie：{COOKIE_FILE}", file=sys.stderr)
        return 1
    log(f"cookie {len(cookies)} 个，登录态: "
        f"{sorted({'thor', 'pin'} & set(cookies)) or '⚠️ 无'}")

    page_size = int(cfg.get("page_size", 10))
    page_interval = (
        args.interval if args.interval is not None
        else args.review_interval * page_size
    )
    deadline = time.time() + args.max_minutes * 60
    log(f"开始，最长运行 {args.max_minutes:.0f} 分钟")
    log(
        f"  节奏：每条评论约 {page_interval / page_size:.1f}s，"
        f"每页约 {page_interval:.0f}s（±{args.jitter * 100:.0f}% 随机抖动）"
    )
    log(f"  限流等待 {args.limit_wait:.0f}s，逐次翻倍，封顶 4 倍")

    for product in products:
        if time.time() >= deadline:
            log("已到时间上限，停止")
            break
        try:
            crawl_product(product, cookies, cfg, args, deadline)
        except KeyboardInterrupt:
            log("收到中断，已保存进度")
            break
        except Exception as e:  # noqa: BLE001
            log(f"产品 {product['product_id']} 出错: {type(e).__name__}: {e}")
            time.sleep(60)

    log("")
    show_status(products, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
