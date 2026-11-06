#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Job 02 — ABSA 属性情感抽取（对应规格 §8.2）

输入: /spike/staging/cleaned/        （Job 01 的 Parquet）
输出: /spike/staging/aspects/        （Parquet）

输出字段（严格按 §8.2）:
  review_id, product_id, aspect_name, sentiment, confidence, evidence_text, create_time

实现要点:
  - 统一接口在 absa/model_adapter.py: extract_aspects(text)
  - 模型加载放在 mapPartitions 内，每分区只加载一次（§8.2 明确要求）
  - 模型不可用 / 低置信度时自动回退到 absa/rule_fallback.py
  - 每条评论最多保留 5 个属性（在 rule_fallback 与 model_adapter 内部均已截断）

运行:
  python spark_jobs/02_absa_extract.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pyspark.sql import SparkSession, functions as F  # noqa: E402
from pyspark.sql.types import (  # noqa: E402
    FloatType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from config.settings import (  # noqa: E402
    HDFS_ASPECTS,
    HDFS_CLEANED,
    SPARK_APP_NAME,
    SPARK_MASTER,
)
from hdfs_util import write_parquet  # noqa: E402

OUT_SCHEMA = StructType(
    [
        StructField("review_id", StringType(), True),
        StructField("product_id", StringType(), True),
        StructField("aspect_name", StringType(), True),
        StructField("sentiment", StringType(), True),
        StructField("confidence", FloatType(), True),
        StructField("evidence_text", StringType(), True),
        StructField("create_time", TimestampType(), True),
    ]
)

MAX_ASPECTS = 5


def process_partition(rows):
    """在一个分区内完成抽取。模型/词典只在此处初始化一次。"""
    from absa.model_adapter import extract_aspects

    for r in rows:
        text = r["content"] or ""
        if not text.strip():
            continue
        try:
            items = extract_aspects(text)
        except Exception:  # noqa: BLE001 —— 单条失败不能拖垮整个分区
            items = []
        for it in items[:MAX_ASPECTS]:
            yield (
                r["review_id"],
                r["product_id"],
                it.get("aspect"),
                it.get("sentiment"),
                float(it.get("confidence") or 0.0),
                (it.get("evidence") or "")[:200],
                r["create_time"],
            )


def build_spark() -> SparkSession:
    return (
        SparkSession.builder.appName(f"{SPARK_APP_NAME}-02-absa")
        .master(SPARK_MASTER)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )


def main() -> int:
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")

    # 报告当前走的哪条路径（模型 / 规则）
    from absa.model_adapter import model_status

    st = model_status()

    print("=" * 66)
    print("Job 02 ABSA 抽取")
    print("=" * 66)
    print(f"  输入: {HDFS_CLEANED}")
    print(f"  输出: {HDFS_ASPECTS}")
    print(f"  抽取路径: {st['mode']}  （{st['reason']}）")

    cleaned = spark.read.parquet(HDFS_CLEANED)
    n_reviews = cleaned.count()
    print(f"\n  待处理评论 {n_reviews} 条")

    rdd = cleaned.select("review_id", "product_id", "content", "create_time").rdd.mapPartitions(
        process_partition
    )
    aspects = spark.createDataFrame(rdd, schema=OUT_SCHEMA)
    n_aspects = aspects.count()
    print(f"  抽出属性 {n_aspects} 条")

    if n_aspects == 0:
        print("  [FAIL] 一条属性都没抽出来", file=sys.stderr)
        return 1

    write_parquet(aspects, HDFS_ASPECTS, partition_by="product_id")
    print(f"  已写入 {HDFS_ASPECTS}（product_id=* 分区）")
    # ---- 统计 ----
    print("\n  情感分布:")
    for row in (
        aspects.groupBy("sentiment")
        .agg(F.count("*").alias("n"))
        .orderBy(F.desc("n"))
        .collect()
    ):
        print(f"    {row['sentiment']:<9} {row['n']:>5} 条")

    print("\n  属性 Top 10:")
    for row in (
        aspects.groupBy("aspect_name")
        .agg(
            F.count("*").alias("n"),
            # 注意：Spark 的 avg() 不接受 BOOLEAN，必须先 when/otherwise 成 0/1
            F.round(
                F.avg(F.when(F.col("sentiment") == "positive", F.lit(1.0)).otherwise(F.lit(0.0))),
                3,
            ).alias("pos_rate"),
            F.round(F.avg("confidence"), 3).alias("conf"),
        )
        .orderBy(F.desc("n"))
        .limit(10)
        .collect()
    ):
        print(f"    {row['aspect_name']:<10} {row['n']:>5} 条  正面率 {row['pos_rate']}  置信 {row['conf']}")

    # ---- 覆盖率 ----
    cov = aspects.select("review_id").distinct().count()
    print(f"\n  覆盖率: {cov}/{n_reviews} 条评论至少命中 1 个属性 "
          f"({cov / n_reviews * 100:.1f}%)")

    # 验收 T4 抽样：随机 3 条看证据文本
    print("\n  抽样检查（验收 T4 格式预演）:")
    for row in aspects.orderBy(F.rand(seed=42)).limit(3).collect():
        print(f"    [{row['aspect_name']}/{row['sentiment']}] {row['evidence_text'][:50]}")

    back = spark.read.parquet(HDFS_ASPECTS)
    print(f"\n  读回校验: {back.count()} 行，列 -> {back.columns}")
    print("\nJob 02 完成")
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
