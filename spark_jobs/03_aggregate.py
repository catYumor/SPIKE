#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Job 03 — 属性归并与聚合（对应规格 §8.3）

输入: /spike/staging/aspects/   （Job 02 的 Parquet）
      /spike/staging/cleaned/   （取 score，供 §8.4 的 severity 公式用，见偏差记录 D1）
输出: /spike/output/aggregation/ （聚合结果备份，规格 §6）
      MySQL: review_aspects、aspect_aggregation

逻辑（严格按 §8.3）：
  1. 加载 aspect_dictionary 同义词表
  2. 原始属性映射到标准 aspect_name；未命中则保留原词并记录待人工维护
  3. 按 (product_id, aspect_name) 聚合 pos/neg/neu/total 与 pos_rate/neg_rate
  4. 写入 review_aspects 和 aspect_aggregation

运行:
  python spark_jobs/03_aggregate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402
from pyspark.sql import SparkSession, functions as F  # noqa: E402
from pyspark.sql.types import StringType  # noqa: E402

from config.settings import (  # noqa: E402
    HDFS_ASPECTS,
    HDFS_CLEANED,
    HDFS_OUTPUT,
    MYSQL,
    SPARK_APP_NAME,
    SPARK_MASTER,
    load_aspects,
)
from hdfs_util import write_parquet  # noqa: E402


def build_spark() -> SparkSession:
    return (
        SparkSession.builder.appName(f"{SPARK_APP_NAME}-03-aggregate")
        .master(SPARK_MASTER)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )


def mysql_connect():
    return pymysql.connect(**MYSQL, autocommit=False)


def load_dictionary(conn) -> dict[str, str]:
    """从 aspect_dictionary 表加载 同义词 -> 标准属性名。表为空则回退到 YAML。"""
    with conn.cursor() as cur:
        cur.execute("SELECT aspect_name, synonyms FROM aspect_dictionary")
        rows = cur.fetchall()

    syn2std: dict[str, str] = {}
    for name, synonyms in rows:
        syn2std[name] = name
        if synonyms:
            for s in str(synonyms).split(","):
                s = s.strip()
                if s:
                    syn2std[s] = name

    if not syn2std:
        print("  [提示] aspect_dictionary 表为空，改从 config/aspects.yaml 加载")
        for a in load_aspects():
            name = a["aspect_name"]
            syn2std[name] = name
            for s in a.get("synonyms", []):
                syn2std[s] = name

    return syn2std


def main() -> int:
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")

    print("=" * 66)
    print("Job 03 属性归并与聚合")
    print("=" * 66)
    print(f"  输入: {HDFS_ASPECTS}")
    print(f"  输出: {HDFS_OUTPUT} + MySQL(review_aspects, aspect_aggregation)")
    print(f"  MySQL: {MYSQL['host']}:{MYSQL['port']}/{MYSQL['database']}")

    conn = mysql_connect()
    syn2std = load_dictionary(conn)
    print(f"\n  同义词表: {len(syn2std)} 条映射")

    # ---- 读入 ----
    # 注意：staging 目录按 product_id=<id> 分区，目录名是数字，
    # Spark 会把分区列推断成 bigint。这里显式转回 string，与 MySQL 保持一致。
    aspects = spark.read.parquet(HDFS_ASPECTS).withColumn(
        "product_id", F.col("product_id").cast("string")
    )
    cleaned = spark.read.parquet(HDFS_CLEANED).select("review_id", "score")
    print(f"  读入属性 {aspects.count()} 条，评论 {cleaned.count()} 条")

    # ---- 2. 映射到标准属性名 ----
    mapping = spark.sparkContext.broadcast(syn2std)

    @F.udf(StringType())
    def to_standard(raw: str) -> str:
        if raw is None:
            return None
        return mapping.value.get(raw.strip(), raw.strip())

    mapped = aspects.withColumn("aspect_name", to_standard(F.col("aspect_name")))

    # 记录未命中标准属性的原词（规格 §8.3：待人工维护）
    unmatched = [
        r["aspect_name"]
        for r in mapped.groupBy("aspect_name").count().collect()
        if r["aspect_name"] not in set(syn2std.values())
    ]
    print(f"\n  未命中标准属性的原词 {len(unmatched)} 个（待人工维护）: {unmatched[:10]}")

    # ---- 带 score 的明细（§8.4 severity 需要星级分布，见 D1）----
    detail = (
        mapped.join(cleaned, on="review_id", how="left")
        .dropDuplicates(["review_id", "aspect_name"])
        .filter(F.col("aspect_name").isNotNull())
    )
    n_detail = detail.count()
    print(f"  去重后明细 {n_detail} 条")

    # ---- 3. 聚合 ----
    def cnt(cond):
        return F.sum(F.when(cond, F.lit(1)).otherwise(F.lit(0)))

    agg = (
        detail.groupBy("product_id", "aspect_name")
        .agg(
            cnt(F.col("sentiment") == "positive").alias("pos_count"),
            cnt(F.col("sentiment") == "negative").alias("neg_count"),
            cnt(F.col("sentiment") == "neutral").alias("neu_count"),
            F.count("*").alias("total_count"),
        )
        .withColumn("pos_rate", F.round(F.col("pos_count") / F.col("total_count"), 4))
        .withColumn("neg_rate", F.round(F.col("neg_count") / F.col("total_count"), 4))
        .orderBy("product_id", F.desc("total_count"))
    )

    rows = agg.collect()
    detail_rows = detail.collect()

    # ---- 输出备份到 HDFS ----
    write_parquet(agg, HDFS_OUTPUT, num_partitions=1)
    print(f"\n  已写入 {HDFS_OUTPUT}")

    # ---- 4. 写 MySQL ----
    print("\n  写入 MySQL...")
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE review_aspects")
        cur.executemany(
            """INSERT INTO review_aspects
               (review_id, product_id, aspect_name, sentiment, confidence,
                evidence_text, create_time, score)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                (
                    r["review_id"],
                    r["product_id"],
                    r["aspect_name"],
                    r["sentiment"],
                    float(r["confidence"] or 0),
                    r["evidence_text"],
                    r["create_time"],
                    r["score"],
                )
                for r in detail_rows
            ],
        )
        print(f"    review_aspects: {len(detail_rows)} 行")

        cur.execute("TRUNCATE TABLE aspect_aggregation")
        cur.executemany(
            """INSERT INTO aspect_aggregation
               (product_id, aspect_name, pos_count, neg_count, neu_count,
                total_count, pos_rate, neg_rate)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                (
                    r["product_id"],
                    r["aspect_name"],
                    r["pos_count"],
                    r["neg_count"],
                    r["neu_count"],
                    r["total_count"],
                    float(r["pos_rate"] or 0),
                    float(r["neg_rate"] or 0),
                )
                for r in rows
            ],
        )
        print(f"    aspect_aggregation: {len(rows)} 行")
    conn.commit()
    conn.close()

    # ---- 汇总打印 ----
    print("\n  聚合结果:")
    for r in rows:
        print(
            f"    {r['product_id']}  {r['aspect_name']:<8} "
            f"总 {r['total_count']:>3}  正 {r['pos_count']:>3}  负 {r['neg_count']:>3}  "
            f"中 {r['neu_count']:>3}  正面率 {r['pos_rate']}"
        )

    # ---- 校验 ----
    conn = mysql_connect()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM review_aspects")
        n1 = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM aspect_aggregation")
        n2 = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM aspect_aggregation WHERE total_count >= 5"
        )
        n3 = cur.fetchone()[0]
    conn.close()
    print(f"\n  校验: review_aspects={n1} 行, aspect_aggregation={n2} 行, "
          f"其中 total_count>=5 的 {n3} 行")

    print("\nJob 03 完成")
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
