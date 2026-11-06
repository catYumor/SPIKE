#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Job 04 — 痛点优先级与竞品差距（对应规格 §8.4）

输入: /spike/staging/aspects/    （取 sentiment + review_id）
      /spike/staging/cleaned/    （取 score，用于 severity 的星级加权）
      MySQL: aspect_aggregation、products
输出: MySQL: aspect_priority、competitive_gap

规则（严格按 §8.4）:
  优势 advantage : total_count >= 5 且 pos_rate >= 0.75
  痛点 painpoint : total_count >= 5 且 neg_rate >= 0.30
  其余 neutral

  Kano:
    neg_rate >= 0.40 且 pos_rate <= 0.40 -> must_be
    pos_rate >= 0.60 且 neg_rate >= 0.20 -> one_dimensional
    pos_rate >= 0.75 且 neg_rate <= 0.15 -> attractive
    其他                                 -> indifferent

  severity      = (1星负面*1.0 + 2星负面*0.8 + 3星负面*0.5) / neg_count
  kano_weight   = {must_be:1.0, one_dimensional:0.7, attractive:0.4, indifferent:0.2}
  priority_score = neg_count * severity * kano_weight[kano_type]

  竞品差距: pos_gap = target_pos_rate - competitor_pos_rate
            neg_gap = target_neg_rate - competitor_neg_rate

运行:
  python spark_jobs/04_priority_gap.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402
from pyspark.sql import SparkSession, functions as F  # noqa: E402
from pyspark.sql.types import (  # noqa: E402
    IntegerType,
    StringType,
    StructField,
    StructType,
)

from config.settings import (  # noqa: E402
    HDFS_ASPECTS,
    HDFS_CLEANED,
    MYSQL,
    SPARK_APP_NAME,
    SPARK_MASTER,
)

KANO_WEIGHT = {
    "must_be": 1.0,
    "one_dimensional": 0.7,
    "attractive": 0.4,
    "indifferent": 0.2,
}
MIN_TOTAL = 5  # §8.4：优势/痛点判定的最小样本量

AGG_SCHEMA = StructType(
    [
        StructField("product_id", StringType(), True),
        StructField("aspect_name", StringType(), True),
        StructField("pos_count", IntegerType(), True),
        StructField("neg_count", IntegerType(), True),
        StructField("neu_count", IntegerType(), True),
        StructField("total_count", IntegerType(), True),
        StructField("pos_rate", StringType(), True),  # 转 double 用
        StructField("neg_rate", StringType(), True),
    ]
)


def build_spark() -> SparkSession:
    return (
        SparkSession.builder.appName(f"{SPARK_APP_NAME}-04-priority-gap")
        .master(SPARK_MASTER)
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )


def mysql_connect():
    return pymysql.connect(**MYSQL, autocommit=False)


def load_aggregation(conn) -> list[tuple]:
    """读 aspect_aggregation。Decimal 转成字符串交给 Spark 再转 double，避免序列化问题。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT product_id, aspect_name, pos_count, neg_count, neu_count,
                      total_count, pos_rate, neg_rate
               FROM aspect_aggregation"""
        )
        rows = cur.fetchall()
    return [
        (
            r[0], r[1], int(r[2]), int(r[3]), int(r[4]), int(r[5]),
            str(r[6] if r[6] is not None else 0),
            str(r[7] if r[7] is not None else 0),
        )
        for r in rows
    ]


def load_products(conn) -> list[dict]:
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("SELECT product_id, brand, model, category, is_target FROM products")
        return list(cur.fetchall())


def main() -> int:
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")

    print("=" * 66)
    print("Job 04 痛点优先级与竞品差距")
    print("=" * 66)

    conn = mysql_connect()
    agg_rows = load_aggregation(conn)
    products = load_products(conn)
    print(f"  读入 aspect_aggregation {len(agg_rows)} 行，产品 {len(products)} 个")

    if not agg_rows:
        print("  [FAIL] aspect_aggregation 为空，先跑 Job 03", file=sys.stderr)
        return 1

    targets = [p for p in products if p["is_target"]]
    competitors = [p for p in products if not p["is_target"]]
    if len(targets) != 1:
        print(f"  [FAIL] 目标产品应恰好 1 个，实际 {len(targets)}", file=sys.stderr)
        return 1
    target = targets[0]
    print(f"  目标产品: {target['product_id']} {target['brand']} {target['model']}")
    for c in competitors:
        print(f"  竞品:     {c['product_id']} {c['brand']} {c['model']}")

    # ---- 聚合结果 -> DataFrame ----
    agg = (
        spark.createDataFrame(agg_rows, schema=AGG_SCHEMA)
        .withColumn("pos_rate", F.col("pos_rate").cast("double"))
        .withColumn("neg_rate", F.col("neg_rate").cast("double"))
    )

    # ---- severity：需要负面提及的星级分布（D1）----
    # 分区列 product_id 会被推断成 bigint，显式转回 string
    aspects = spark.read.parquet(HDFS_ASPECTS).withColumn(
        "product_id", F.col("product_id").cast("string")
    )
    cleaned = spark.read.parquet(HDFS_CLEANED).select("review_id", "score")
    detail = aspects.join(cleaned, on="review_id", how="left").dropDuplicates(
        ["review_id", "aspect_name"]
    )

    neg_star = (
        detail.filter(F.col("sentiment") == "negative")
        .groupBy("product_id", "aspect_name")
        .agg(
            F.sum(F.when(F.col("score") == 1, 1).otherwise(0)).alias("neg_1"),
            F.sum(F.when(F.col("score") == 2, 1).otherwise(0)).alias("neg_2"),
            F.sum(F.when(F.col("score") == 3, 1).otherwise(0)).alias("neg_3"),
        )
    )

    merged = (
        agg.join(neg_star, on=["product_id", "aspect_name"], how="left")
        .fillna(0, subset=["neg_1", "neg_2", "neg_3"])
    )

    # ---- severity / kano / label / priority_score ----
    severity = F.when(
        F.col("neg_count") > 0,
        (
            F.col("neg_1") * 1.0 + F.col("neg_2") * 0.8 + F.col("neg_3") * 0.5
        ) / F.col("neg_count"),
    ).otherwise(F.lit(0.0))

    kano = (
        F.when(
            (F.col("neg_rate") >= 0.40) & (F.col("pos_rate") <= 0.40), "must_be"
        )
        .when(
            (F.col("pos_rate") >= 0.60) & (F.col("neg_rate") >= 0.20), "one_dimensional"
        )
        .when(
            (F.col("pos_rate") >= 0.75) & (F.col("neg_rate") <= 0.15), "attractive"
        )
        .otherwise("indifferent")
    )

    label = (
        F.when(
            (F.col("total_count") >= MIN_TOTAL) & (F.col("pos_rate") >= 0.75), "advantage"
        )
        .when(
            (F.col("total_count") >= MIN_TOTAL) & (F.col("neg_rate") >= 0.30), "painpoint"
        )
        .otherwise("neutral")
    )

    weighted = (
        F.when(F.col("kano_type") == "must_be", KANO_WEIGHT["must_be"])
        .when(F.col("kano_type") == "one_dimensional", KANO_WEIGHT["one_dimensional"])
        .when(F.col("kano_type") == "attractive", KANO_WEIGHT["attractive"])
        .otherwise(KANO_WEIGHT["indifferent"])
    )

    priority = (
        merged.withColumn("severity", severity)
        .withColumn("kano_type", kano)
        .withColumn("label", label)
        .withColumn(
            "priority_score",
            F.round(F.col("neg_count") * F.col("severity") * weighted, 4),
        )
        .select(
            "product_id", "aspect_name", "kano_type", "priority_score",
            "label", "severity", "neg_count", "pos_rate", "neg_rate", "total_count",
        )
        .orderBy("product_id", F.desc("priority_score"))
    )

    # ---- 竞品差距 ----
    prod_df = spark.createDataFrame(
        [
            (p["product_id"], p["category"] or "手机", int(p["is_target"]))
            for p in products
        ],
        schema="product_id string, category string, is_target int",
    )
    # 必须带上 is_target，否则后面无法区分目标产品与竞品
    preg = priority.join(
        prod_df.select("product_id", "category", "is_target"), on="product_id", how="left"
    )

    tgt = preg.filter(F.col("is_target") == 1).select(
        "category",
        "aspect_name",
        F.col("product_id").alias("target_product_id"),
        F.col("pos_rate").alias("target_pos_rate"),
        F.col("neg_rate").alias("target_neg_rate"),
    )
    comp = preg.filter(F.col("is_target") == 0).select(
        F.col("product_id").alias("competitor_product_id"),
        "aspect_name",
        F.col("pos_rate").alias("competitor_pos_rate"),
        F.col("neg_rate").alias("competitor_neg_rate"),
    )

    gap = (
        tgt.join(comp, on="aspect_name", how="inner")
        .withColumn("pos_gap", F.round(F.col("target_pos_rate") - F.col("competitor_pos_rate"), 4))
        .withColumn("neg_gap", F.round(F.col("target_neg_rate") - F.col("competitor_neg_rate"), 4))
        .select(
            "category", "aspect_name", "target_product_id", "competitor_product_id",
            "target_pos_rate", "competitor_pos_rate", "pos_gap",
            "target_neg_rate", "competitor_neg_rate", "neg_gap",
        )
        .orderBy(F.desc("pos_gap"))
    )

    prio_rows = priority.collect()
    gap_rows = gap.collect()
    now = datetime.now().replace(microsecond=0)

    print("\n  ---- 属性优先级 ----")
    for r in prio_rows:
        print(
            f"    {r['product_id']}  {r['aspect_name']:<8} {r['label']:<9} "
            f"Kano={r['kano_type']:<15} 分={r['priority_score']:<7} "
            f"severity={round(r['severity'], 3)} 负面数={r['neg_count']}"
        )

    print(f"\n  ---- 竞品差距（{len(gap_rows)} 组属性）----")
    for r in gap_rows:
        arrow = "⬆" if r["pos_gap"] > 0 else ("⬇" if r["pos_gap"] < 0 else "＝")
        print(
            f"    {r['aspect_name']:<8} 目标 {r['target_pos_rate']:.2f} vs 竞品 "
            f"{r['competitor_pos_rate']:.2f}  差 {r['pos_gap']:+.2f} {arrow}"
        )

    # ---- 写 MySQL ----
    print("\n  写入 MySQL...")
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE aspect_priority")
        cur.executemany(
            """INSERT INTO aspect_priority
               (product_id, aspect_name, kano_type, priority_score, label, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            [
                (
                    r["product_id"], r["aspect_name"], r["kano_type"],
                    float(r["priority_score"] or 0), r["label"], now,
                )
                for r in prio_rows
            ],
        )
        print(f"    aspect_priority: {len(prio_rows)} 行")

        cur.execute("TRUNCATE TABLE competitive_gap")
        cur.executemany(
            """INSERT INTO competitive_gap
               (category, aspect_name, target_product_id, competitor_product_id,
                target_pos_rate, competitor_pos_rate, pos_gap,
                target_neg_rate, competitor_neg_rate, neg_gap, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                (
                    r["category"], r["aspect_name"], r["target_product_id"],
                    r["competitor_product_id"],
                    float(r["target_pos_rate"] or 0), float(r["competitor_pos_rate"] or 0),
                    float(r["pos_gap"] or 0),
                    float(r["target_neg_rate"] or 0), float(r["competitor_neg_rate"] or 0),
                    float(r["neg_gap"] or 0), now,
                )
                for r in gap_rows
            ],
        )
        print(f"    competitive_gap: {len(gap_rows)} 行")
    conn.commit()

    # ---- 校验：验收 T7 要求共同属性至少 5 个 ----
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT aspect_name) FROM competitive_gap")
        n_gap_aspects = cur.fetchone()[0]
        cur.execute("SELECT label, COUNT(*) FROM aspect_priority GROUP BY label")
        label_dist = dict(cur.fetchall())
        cur.execute("SELECT kano_type, COUNT(*) FROM aspect_priority GROUP BY kano_type")
        kano_dist = dict(cur.fetchall())
    conn.close()

    print(f"\n  校验: 竞品差距覆盖共同属性 {n_gap_aspects} 个（T7 要求 >= 5）")
    print(f"        标签分布 {label_dist}")
    print(f"        Kano 分布 {kano_dist}")

    print("\nJob 04 完成")
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
