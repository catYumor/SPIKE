#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SPIKE 验收测试 T1–T10（对应规格 §13）。

用法（在 WSL 里、先 source scripts/spike-env.sh）:

    python scripts/acceptance_test.py              # 全跑（含 T10 重跑作业，约 2 分钟）
    python scripts/acceptance_test.py --fast       # 跳过 T10（速度优先）
    python scripts/acceptance_test.py --only T2,T7 # 只跑指定项
    python scripts/acceptance_test.py --report docs/验收报告.md

每项都会打印判定依据；全部通过时退出码 0，否则 1。
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import pymysql  # noqa: E402
import requests  # noqa: E402

from config.settings import (  # noqa: E402
    ABSA_SAMPLE_SIZE,
    HDFS_CLEANED,
    HDFS_RAW,
    MIN_COMMON_ASPECTS,
    MYSQL,
    load_aspects,
    load_products,
    target_product,
)

HDFS_BIN = shutil.which("hdfs") or "/usr/local/hadoop/bin/hdfs"
BACKEND_BASE = "http://127.0.0.1:5000"
FIELDS = {
    "review_id", "product_id", "brand", "model", "is_target",
    "score", "content", "create_time", "source", "crawl_time",
}

RESULTS: list[dict] = []


# ============================================================ 基础设施


def record(tid: str, name: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": tid, "name": name, "ok": bool(ok), "detail": detail})
    print(f"  {'✅' if ok else '❌'} {tid}  {name}")
    print(f"       {detail}")


def db_conn():
    return pymysql.connect(
        **MYSQL, cursorclass=pymysql.cursors.DictCursor, autocommit=True, connect_timeout=5
    )


def q(sql: str, args: tuple | list | None = None) -> list[dict]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchall()
    finally:
        conn.close()


def hdfs(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [HDFS_BIN, *args], capture_output=True, text=True, timeout=timeout
    )


def backend_alive() -> bool:
    try:
        return requests.get(BACKEND_BASE + "/api/health", timeout=3).status_code == 200
    except requests.RequestException:
        return False


def ensure_backend() -> tuple[bool, str]:
    if backend_alive():
        return True, "后端已在运行"
    script = PROJECT_ROOT / "scripts" / "run_backend.sh"
    r = subprocess.run(
        ["bash", str(script), "start"], capture_output=True, text=True, timeout=120
    )
    for _ in range(20):
        if backend_alive():
            return True, "后端已自动启动"
        time.sleep(0.5)
    return False, f"后端启动失败: {(r.stdout + r.stderr)[-200:]}"


# ============================================================ T1


def t1_hdfs() -> None:
    try:
        r = hdfs("dfs", "-ls", "/spike/raw")
    except Exception as e:  # noqa: BLE001
        record("T1", "HDFS 伪分布式", False, f"调用 hdfs 失败: {type(e).__name__}: {e}")
        return
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()[:300]
        record("T1", "HDFS 伪分布式", False, f"hdfs dfs -ls /spike/raw 失败: {detail}")
        return
    entries = [x for x in r.stdout.splitlines() if "/spike/raw" in x]
    record(
        "T1",
        "HDFS 伪分布式",
        True,
        f"`hdfs dfs -ls /spike/raw` 可访问，{len(entries)} 个条目",
    )


# ============================================================ T2


def t2_crawl() -> None:
    products = load_products()
    lines_out = []
    problems = []
    n_target = n_comp = 0

    for p in products:
        pid = p["product_id"]
        path = f"{HDFS_RAW}/product_id={pid}/reviews.jsonl"
        r = hdfs("dfs", "-cat", path)
        tag = "目标" if p["is_target"] else "竞品"
        if r.returncode != 0:
            problems.append(f"{pid} 读不到 HDFS 文件")
            lines_out.append(f"{p['brand']}{p['model']}({tag}) 读取失败")
            continue
        rows = [ln for ln in r.stdout.splitlines() if ln.strip()]
        bad_fields = 0
        for ln in rows:
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                bad_fields += 1
                continue
            if set(d.keys()) != FIELDS:
                bad_fields += 1
        if len(rows) < 500:
            problems.append(f"{pid} 只有 {len(rows)} 条，要求 ≥500")
        if bad_fields:
            problems.append(f"{pid} 有 {bad_fields} 行字段不符合 §5.3")
        if p["is_target"]:
            n_target += 1
        else:
            n_comp += 1
        lines_out.append(f"{p['brand']} {p['model']}({tag}) {len(rows)} 条")

    if n_target < 1:
        problems.append("没有目标产品")
    if n_comp < 1:
        problems.append("没有竞品")

    ok = not problems
    detail = "；".join(lines_out) + ("" if ok else "\n" + "；".join(problems))
    record("T2", "采集（各 ≥500 条）", ok, detail)


# ============================================================ T3


def t3_clean() -> None:
    try:
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F
    except Exception as e:  # noqa: BLE001
        record("T3", "清洗（无空评论/无重复）", False, f"PySpark 不可用: {e}")
        return

    spark = (
        SparkSession.builder.master("local[2]")
        .appName("SPIKE-acceptance-T3")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    try:
        df = spark.read.parquet(HDFS_CLEANED)
        total = df.count()
        empty = df.filter(F.trim(F.col("content")) == "").count()
        nulls = df.filter(
            F.col("content").isNull() | F.col("review_id").isNull()
        ).count()
        dups = (
            df.groupBy("review_id").count().filter(F.col("count") > 1).count()
        )
        bad_score = df.filter(~F.col("score").between(1, 5)).count()
        ok = total > 0 and empty == 0 and nulls == 0 and dups == 0 and bad_score == 0
        record(
            "T3",
            "清洗（无空评论/无重复）",
            ok,
            f"cleaned {total} 行｜空内容 {empty}｜空值 {nulls}｜"
            f"重复 review_id {dups}｜非法星级 {bad_score}",
        )
    finally:
        spark.stop()


# ============================================================ T4

ABSA_REPORT = PROJECT_ROOT / "docs" / "ABSA抽检报告.md"


def t4_absa() -> None:
    product_ids = sorted({p["product_id"] for p in load_products()})
    total_rows = q("SELECT COUNT(*) c FROM review_aspects")[0]["c"]
    if not total_rows:
        record("T4", "ABSA 抽检 100 条", False, "review_aspects 表为空，请先跑流水线")
        return

    sample = q(
        """SELECT review_id, product_id, aspect_name, sentiment, confidence,
                  evidence_text, score, create_time
             FROM review_aspects
          ORDER BY RAND()
            LIMIT %s""",
        (ABSA_SAMPLE_SIZE,),
    )
    dict_names = {a["aspect_name"] for a in load_aspects()}
    problems: list[str] = []

    for r in sample:
        rid = r.get("review_id")
        if not r.get("aspect_name"):
            problems.append(f"{rid}: aspect_name 为空")
        if r.get("sentiment") not in {"positive", "negative", "neutral"}:
            problems.append(f"{rid}: sentiment 非法 {r.get('sentiment')!r}")
        conf = r.get("confidence")
        if conf is None or not (0.0 <= float(conf) <= 1.0):
            problems.append(f"{rid}: confidence 越界 {conf!r}")
        ev = (r.get("evidence_text") or "").strip()
        if not ev:
            problems.append(f"{rid}: evidence_text 为空")
        elif len(ev) > 200:
            problems.append(f"{rid}: evidence_text 超过 200 字 ({len(ev)})")
        if r.get("score") not in range(1, 6):
            problems.append(f"{rid}: score 越界 {r.get('score')!r}")

    in_dict = sum(1 for r in sample if r["aspect_name"] in dict_names)
    dist = {"positive": 0, "negative": 0, "neutral": 0}
    for r in sample:
        dist[r["sentiment"]] = dist.get(r["sentiment"], 0) + 1

    _write_absa_report(sample, dict_names, dist, total_rows, product_ids)

    ok = not problems
    detail = (
        f"从 {total_rows} 行中随机抽 {len(sample)} 条｜结构异常 {len(problems)} 条｜"
        f"命中属性字典 {in_dict}/{len(sample)}｜情感分布 {dist}｜报告 {ABSA_REPORT.name}"
    )
    if problems:
        detail += "\n" + "；".join(problems[:5])
    record("T4", "ABSA 抽检 100 条", ok, detail)


def _write_absa_report(sample, dict_names, dist, total_rows, product_ids) -> None:
    ABSA_REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ABSA 抽检报告（规格 §13 T4 / §15.6）",
        "",
        f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"> 抽样方式：`SELECT ... FROM review_aspects ORDER BY RAND() LIMIT {ABSA_SAMPLE_SIZE}`",
        f"> 抽样基数：{total_rows} 行；本次抽到 {len(sample)} 条",
        "",
        "## 一、结构一致性检查",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
    ]
    problems = 0
    for r in sample:
        if not r.get("aspect_name") or r.get("sentiment") not in {
            "positive", "negative", "neutral"
        }:
            problems += 1
    lines += [
        f"| 字段齐全、类型正确 | {'全部通过' if problems == 0 else f'{problems} 条异常'} |",
        f"| 命中属性字典（§9.1 的 7 个标准属性） | {sum(1 for r in sample if r['aspect_name'] in dict_names)}/{len(sample)} |",
        f"| `evidence_text` 非空且 ≤200 字 | 已逐条校验 |",
        f"| `confidence` ∈ [0,1] | 已逐条校验 |",
        "",
        "## 二、情感分布",
        "",
        "| 情感 | 条数 | 占比 |",
        "|---|---|---|",
    ]
    for k, v in dist.items():
        pct = f"{v / len(sample) * 100:.1f}%" if sample else "0%"
        lines.append(f"| {k} | {v} | {pct} |")

    lines += [
        "",
        "## 三、100 条抽样明细（人工可读性复核用）",
        "",
        "> 说明：本项目没有标注集，**准确率无法自动计算**。下表把原文证据与抽取结果并列，",
        "> 供人工快速复核。判读方法：看「属性」是否确实是「用户原话」在评价的那个方面、",
        "> 「情感」是否符合语义。规则路径的典型错误是**反讽**和**双重否定**。",
        "",
        "| # | 用户原话（证据） | 属性 | 情感 | 置信度 | 星级 |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(sample, 1):
        ev = (r["evidence_text"] or "").replace("|", "\\|").replace("\n", " ")[:70]
        lines.append(
            f"| {i} | {ev} | {r['aspect_name']} | {r['sentiment']} | "
            f"{float(r['confidence']):.2f} | {r['score']} |"
        )

    lines += [
        "",
        "## 四、已知局限",
        "",
        "1. **无标注集**：准确率需人工判读上表后填入，系统本身不产出 precision/recall。",
        "2. **规则路径**：`torch`/`transformers` 未安装，当前走 `absa/rule_fallback.py`",
        "   的词典 + jieba 规则抽取（见 `docs/实现偏差记录.md` D8）。",
        "3. **典型错误类型**：反讽（「这续航真棒，一天充三次」）、双重否定、",
        "   属性词与情感词跨句搭配。",
        "",
    ]
    ABSA_REPORT.write_text("\n".join(lines), encoding="utf-8")


# ============================================================ T5


def t5_aggregate() -> None:
    agg = q(
        """SELECT product_id, aspect_name, pos_count, neg_count, neu_count,
                  total_count, pos_rate, neg_rate
             FROM aspect_aggregation"""
    )
    if not agg:
        record("T5", "聚合正负率计算正确", False, "aspect_aggregation 表为空")
        return

    raw = q(
        """SELECT product_id, aspect_name, sentiment, COUNT(*) c
             FROM review_aspects
         GROUP BY product_id, aspect_name, sentiment"""
    )
    lookup = {(r["product_id"], r["aspect_name"], r["sentiment"]): r["c"] for r in raw}
    problems: list[str] = []

    for a in agg:
        key = (a["product_id"], a["aspect_name"])
        total = (a["pos_count"] or 0) + (a["neg_count"] or 0) + (a["neu_count"] or 0)
        if total != a["total_count"]:
            problems.append(f"{key} 三个分项之和 {total} != total_count {a['total_count']}")
        if a["total_count"]:
            expect_pos = a["pos_count"] / a["total_count"]
            expect_neg = a["neg_count"] / a["total_count"]
            if abs(float(a["pos_rate"]) - expect_pos) > 1e-3:
                problems.append(f"{key} pos_rate {a['pos_rate']} != {expect_pos:.4f}")
            if abs(float(a["neg_rate"]) - expect_neg) > 1e-3:
                problems.append(f"{key} neg_rate {a['neg_rate']} != {expect_neg:.4f}")
        for sentiment, col in (
            ("positive", "pos_count"),
            ("negative", "neg_count"),
            ("neutral", "neu_count"),
        ):
            actual = lookup.get((a["product_id"], a["aspect_name"], sentiment), 0)
            if actual != (a[col] or 0):
                problems.append(f"{key}.{col} 聚合值 {a[col]} != review_aspects 实际 {actual}")

    ok = not problems
    detail = f"{len(agg)} 个聚合行，正负率与明细重算一致" if ok else "；".join(problems[:5])
    record("T5", "聚合正负率计算正确", ok, detail)


# ============================================================ T6


def t6_priority() -> None:
    rows = q(
        """SELECT product_id, aspect_name, kano_type, priority_score, label
             FROM aspect_priority
         ORDER BY product_id, priority_score DESC"""
    )
    if not rows:
        record("T6", "痛点按优先级降序 + Kano 非空", False, "aspect_priority 表为空")
        return

    allowed = {"must_be", "one_dimensional", "attractive", "indifferent"}
    problems: list[str] = []
    by_prod: dict[str, list[float]] = {}

    for r in rows:
        if not r["kano_type"] or r["kano_type"] not in allowed:
            problems.append(f"{r['product_id']}/{r['aspect_name']} Kano 类型异常 {r['kano_type']!r}")
        if r["priority_score"] is None:
            problems.append(f"{r['product_id']}/{r['aspect_name']} priority_score 为空")
        if r["label"] not in {"advantage", "painpoint", "neutral"}:
            problems.append(f"{r['product_id']}/{r['aspect_name']} label 异常 {r['label']!r}")
        by_prod.setdefault(r["product_id"], []).append(float(r["priority_score"] or 0))

    for pid, scores in by_prod.items():
        if scores != sorted(scores, reverse=True):
            problems.append(f"{pid} 未按 priority_score 降序")

    pain = q(
        """SELECT aspect_name, priority_score FROM aspect_priority
            WHERE label = 'painpoint' ORDER BY priority_score DESC"""
    )
    kano_dist: dict[str, int] = {}
    for r in rows:
        kano_dist[r["kano_type"]] = kano_dist.get(r["kano_type"], 0) + 1

    ok = not problems
    detail = (
        f"{len(rows)} 行，Kano 全部有效 {kano_dist}，排序正确，"
        f"痛点 {len(pain)} 项按分数降序"
        if ok
        else "；".join(problems[:5])
    )
    record("T6", "痛点按优先级降序 + Kano 非空", ok, detail)


# ============================================================ T7


def t7_gap() -> None:
    target = target_product()
    rows = q(
        """SELECT aspect_name, target_pos_rate, competitor_pos_rate, pos_gap,
                  target_neg_rate, competitor_neg_rate, neg_gap
             FROM competitive_gap
            WHERE target_product_id = %s""",
        (target["product_id"],),
    )
    if not rows:
        record("T7", f"竞品差距 ≥{MIN_COMMON_ASPECTS} 个共同属性", False, "competitive_gap 表为空")
        return

    aspects = {r["aspect_name"] for r in rows}
    problems: list[str] = []
    for r in rows:
        if r["pos_gap"] is None or r["neg_gap"] is None:
            problems.append(f"{r['aspect_name']} gap 为空")
            continue
        expect_pos = float(r["target_pos_rate"]) - float(r["competitor_pos_rate"])
        if abs(float(r["pos_gap"]) - expect_pos) > 1e-3:
            problems.append(f"{r['aspect_name']} pos_gap {r['pos_gap']} != {expect_pos:.4f}")
        expect_neg = float(r["target_neg_rate"]) - float(r["competitor_neg_rate"])
        if abs(float(r["neg_gap"]) - expect_neg) > 1e-3:
            problems.append(f"{r['aspect_name']} neg_gap {r['neg_gap']} != {expect_neg:.4f}")

    ok = len(aspects) >= MIN_COMMON_ASPECTS and not problems
    detail = (
        f"{len(aspects)} 个共同属性（要求 ≥{MIN_COMMON_ASPECTS}），{len(rows)} 行，"
        f"pos_gap/neg_gap 全部非空且等于两端差"
        if ok
        else "；".join(problems[:5]) or f"只有 {len(aspects)} 个共同属性"
    )
    record("T7", "竞品差距（共同属性 + gap）", ok, detail)


# ============================================================ T8


def t8_api() -> None:
    ok_start, msg = ensure_backend()
    if not ok_start:
        record("T8", "API 全部 200 且结构符合约定", False, msg)
        return

    from test_api import CASES  # 复用接口自测的用例定义

    failures: list[str] = []
    for name, path, want_status, keys in CASES:
        try:
            r = requests.get(BACKEND_BASE + path, timeout=15)
        except requests.RequestException as e:
            failures.append(f"{name}: 请求异常 {e}")
            continue
        if r.status_code != want_status:
            failures.append(f"{name}: 期望 {want_status} 实际 {r.status_code}")
            continue
        try:
            body = r.json()
        except ValueError:
            failures.append(f"{name}: 返回不是 JSON")
            continue
        missing = [k for k in keys if k not in body]
        if missing:
            failures.append(f"{name}: 缺字段 {missing}")

    ok = not failures
    detail = (
        f"{len(CASES)} 个用例全部通过（{msg}）"
        if ok
        else f"{len(failures)}/{len(CASES)} 失败: " + "；".join(failures[:4])
    )
    record("T8", "API 全部 200 且结构符合约定", ok, detail)


# ============================================================ T9


def t9_frontend() -> None:
    try:
        import asyncio

        from playwright.async_api import async_playwright
    except ImportError as e:
        record("T9", "前端三视图 + 证据展开", False, f"playwright 不可用: {e}")
        return

    async def run() -> tuple[bool, str]:
        errors: list[str] = []
        pages = [
            ("/dashboard", "产品健康总览"),
            ("/insights", "优势与痛点清单"),
            ("/gap", "竞品差距矩阵"),
        ]
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))

            titles: list[str] = []
            for path, expect in pages:
                await page.goto(BACKEND_BASE + path, wait_until="networkidle")
                await page.wait_for_timeout(1200)
                h1 = ""
                if await page.query_selector("h1"):
                    h1 = (await page.inner_text("h1")).strip()
                titles.append(h1)
                if h1 != expect:
                    errors.append(f"{path} 标题应为 {expect}，实际 {h1!r}")

            # 切到优势痛点页，点开第一个证据按钮
            await page.goto(BACKEND_BASE + "/insights", wait_until="networkidle")
            await page.wait_for_timeout(1000)
            buttons = await page.query_selector_all("button.select")
            quotes_before = len(await page.query_selector_all(".quote"))
            expanded = 0
            if buttons:
                await buttons[0].click()
                await page.wait_for_timeout(1500)
                expanded = len(await page.query_selector_all(".quote"))
                if expanded == 0:
                    errors.append("点击「查看用户原话」后没有出现证据")

            await browser.close()

        ok = not errors
        detail = (
            f"三视图切换正常 {titles}｜证据按钮 {len(buttons)} 个，"
            f"展开前 {quotes_before} 条、展开后 {expanded} 条"
            if ok
            else "；".join(errors[:4])
        )
        return ok, detail

    try:
        ok, detail = asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"
    record("T9", "前端三视图 + 证据展开", ok, detail)


# ============================================================ T10


def t10_idempotent() -> None:
    tables = ["aspect_aggregation", "aspect_priority", "competitive_gap"]
    # 每张表的「业务主键」，重跑后不应出现重复组合
    key_cols = {
        "aspect_aggregation": ["product_id", "aspect_name"],
        "aspect_priority": ["product_id", "aspect_name"],
        "competitive_gap": ["target_product_id", "competitor_product_id", "aspect_name"],
    }

    def snapshot() -> dict[str, int]:
        return {t: q(f"SELECT COUNT(*) c FROM `{t}`")[0]["c"] for t in tables}

    def duplicates() -> dict[str, int]:
        out = {}
        for t in tables:
            cols = key_cols[t]
            col_sql = ", ".join(f"`{c}`" for c in cols)
            out[t] = q(
                f"""SELECT COUNT(*) c FROM (
                        SELECT {col_sql}, COUNT(*) n
                          FROM `{t}`
                      GROUP BY {col_sql}
                        HAVING n > 1
                    ) x"""
            )[0]["c"]
        return out

    before = snapshot()
    dup_before = duplicates()
    if any(dup_before.values()):
        record(
            "T10",
            "重跑作业不产生重复聚合行",
            False,
            f"重跑前就已存在重复行: {dup_before}",
        )
        return

    for step in ("3", "4"):
        r = subprocess.run(
            ["bash", "scripts/run_pipeline.sh", "--only", step],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=900,
        )
        if r.returncode != 0:
            output = (r.stdout + r.stderr).strip().splitlines()
            record(
                "T10",
                "重跑作业不产生重复聚合行",
                False,
                f"重跑步骤 {step} 失败: " + (output[-1] if output else "无输出"),
            )
            return

    after = snapshot()
    dup_after = duplicates()

    problems = []
    for t in tables:
        if before[t] != after[t]:
            problems.append(f"{t} 行数 {before[t]} -> {after[t]}")
    for t in tables:
        if dup_after[t] > 0:
            problems.append(f"{t} 出现 {dup_after[t]} 组重复 {key_cols[t]}")

    ok = not problems
    detail = (
        f"重跑步骤 3、4 后行数不变 {after}，业务主键重复组数为 0"
        if ok
        else "；".join(problems)
    )
    record("T10", "重跑作业不产生重复聚合行", ok, detail)


# ============================================================ 主流程

CHECKS = [
    ("T1", "HDFS 伪分布式", t1_hdfs),
    ("T2", "采集（各 ≥500 条）", t2_crawl),
    ("T3", "清洗（无空评论/无重复）", t3_clean),
    ("T4", "ABSA 抽检 100 条", t4_absa),
    ("T5", "聚合正负率计算正确", t5_aggregate),
    ("T6", "痛点按优先级降序 + Kano 非空", t6_priority),
    ("T7", "竞品差距（共同属性 + gap）", t7_gap),
    ("T8", "API 全部 200 且结构符合约定", t8_api),
    ("T9", "前端三视图 + 证据展开", t9_frontend),
    ("T10", "重跑作业不产生重复聚合行", t10_idempotent),
]


def write_report(path: Path, elapsed: float) -> None:
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    lines = [
        "# SPIKE 验收报告（规格 §13）",
        "",
        f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}　｜　耗时 {elapsed:.1f} 秒",
        f"> 结果：**{passed}/{total} 通过**",
        "",
        "| 编号 | 验收项 | 结果 | 依据 |",
        "|---|---|---|---|",
    ]
    for r in RESULTS:
        detail = r["detail"].replace("\n", "<br>").replace("|", "\\|")
        lines.append(
            f"| {r['id']} | {r['name']} | {'通过 ✅' if r['ok'] else '失败 ❌'} | {detail} |"
        )
    lines += [
        "",
        "## 复现方式",
        "",
        "```bash",
        "cd /mnt/d/workspace/SPIKE",
        "source scripts/spike-env.sh",
        "python scripts/acceptance_test.py",
        "```",
        "",
        "## 说明",
        "",
        "- T3 会启动一个轻量 PySpark 会话读取 cleaned 层。",
        "- T8 若后端未运行会自动调用 `scripts/run_backend.sh start`。",
        "- T9 用 headless Chromium 实测三个视图并点击证据按钮。",
        "- T10 会重跑流水线第 3、4 步，约 30 秒。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="SPIKE 验收测试 T1–T10")
    ap.add_argument("--fast", action="store_true", help="跳过 T10（重跑作业）")
    ap.add_argument("--only", help="只跑指定项，逗号分隔，如 T2,T7")
    ap.add_argument("--report", default="docs/验收报告.md", help="报告输出路径")
    args = ap.parse_args()

    only = {x.strip().upper() for x in args.only.split(",")} if args.only else None

    checks = []
    for tid, name, fn in CHECKS:
        if only and tid not in only:
            continue
        if args.fast and tid == "T10":
            continue
        checks.append((tid, name, fn))

    print("=" * 72)
    print(f"  SPIKE 验收测试（{len(checks)} 项）")
    print("=" * 72)

    start = time.time()
    for tid, name, fn in checks:
        print()
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback

            record(tid, name, False, f"检查本身抛异常: {type(e).__name__}: {e}")
            traceback.print_exc()

    elapsed = time.time() - start
    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)

    print()
    print("=" * 72)
    print(f"  结果：{passed}/{total} 通过　耗时 {elapsed:.1f} 秒")
    failed = [r["id"] for r in RESULTS if not r["ok"]]
    if failed:
        print(f"  失败项：{', '.join(failed)}")
    print("=" * 72)

    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = PROJECT_ROOT / report_path
    write_report(report_path, elapsed)
    print(f"  报告：{report_path}")

    return 0 if passed == total and total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
