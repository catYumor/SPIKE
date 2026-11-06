#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Job 01 — 清洗（对应规格 §8.1）

输入: /spike/raw/reviews/product_id=*/reviews.jsonl   （JSONL）
输出: /spike/staging/cleaned/                          （Parquet）

逻辑（严格按 §8.1）：
  1. 读取 JSONL
  2. 过滤 content 为空或长度 < 5
  3. 按 review_id 去重
  4. 解析 score、create_time
  5. 保留 product_id、brand、model、is_target
  6. 写 Parquet

运行:
  python spark_jobs/01_clean.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pyspark.sql import SparkSession, functions as F  # noqa: E402
from pyspark.sql.types import (  # noqa: E402
    BooleanType,
    StringType,
    StructField,
    StructType,
)

from config.settings import (  # noqa: E402
    HDFS_CLEANED,
    HDFS_RAW,
    SPARK_APP_NAME,
    SPARK_MASTER,
)
from hdfs_util import write_parquet  # noqa: E402

# 显式 schema：真实京东数据里 score 可能是字符串，统一按字符串读再转换
SCHEMA = StructType(
    [
        StructField("review_id", StringType(), True),
        StructField("product_id", StringType(), True),
        StructField("brand", StringType(), True),
        StructField("model", StringType(), True),
        StructField("is_target", BooleanType(), True),
        StructField("score", StringType(), True),
        StructField("content", StringType(), True),
        StructField("create_time", StringType(), True),
        StructField("source", StringType(), True),
        StructField("crawl_time", StringType(), True),
    ]
)

MIN_CONTENT_LEN = 5


def build_spark() -> SparkSession:
    return (
        SparkSession.builder.appName(f"{SPARK_APP_NAME}-01-clean")
        .master(SPARK_MASTER)
        # 清洗后数据不大，shuffle 分区数压低，避免小文件
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )


def main() -> int:
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")

    raw_glob = f"{HDFS_RAW}/*/*.jsonl"
    print("=" * 66)
    print("Job 01 清洗")
    print("=" * 66)
    print(f"  输入: {raw_glob}")
    print(f"  输出: {HDFS_CLEANED}")

    # 1. 读取 JSONL
    df = spark.read.schema(SCHEMA).json(raw_glob)
    n_raw = df.count()
    print(f"\n  读入 {n_raw} 行")
    if n_raw == 0:
        print("  [FAIL] 没有读到任何数据，先跑采集和上传", file=sys.stderr)
        return 1

    # 2. 过滤 content 为空或长度 < 5
    df = df.filter(F.col("content").isNotNull() & (F.length(F.trim("content")) >= MIN_CONTENT_LEN))
    n_content = df.count()
    print(f"  过滤空/短内容后 {n_content} 行（丢弃 {n_raw - n_content}）")

    # 4a. 解析 score：必须落在 1..5
    df = df.withColumn("score", F.col("score").cast("int"))
    df = df.filter(F.col("score").between(1, 5))
    n_score = df.count()
    print(f"  过滤非法星级后 {n_score} 行（丢弃 {n_content - n_score}）")

    # 4b. 解析 create_time -> timestamp（保留原字符串便于排查）
    df = df.withColumn(
        "create_time",
        F.to_timestamp("create_time", "yyyy-MM-dd HH:mm:ss"),
    )
    n_time = df.count()
    print(f"  create_time 解析成功 {df.filter(F.col('create_time').isNotNull()).count()} 行")

    # 3. 按 review_id 去重（保留首次出现）
    df = df.dropDuplicates(["review_id"])
    n_dedup = df.count()
    print(f"  按 review_id 去重后 {n_dedup} 行（丢弃 {n_time - n_dedup}）")

    # 5. 保留字段（§8.1 明确要求保留 product_id/brand/model/is_target）
    out_cols = [
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
    ]
    df = df.select(*out_cols)

    # 6. 写 Parquet（按 product_id 分区，规格 §6）
    write_parquet(df, HDFS_CLEANED, partition_by="product_id")
    print(f"\n  已写入 {HDFS_CLEANED}（product_id=* 分区）")

    print("\n  按产品统计:")
    for row in (
        df.groupBy("product_id", "brand", "model", "is_target")
        .agg(
            F.count("*").alias("n"),
            F.round(F.avg("score"), 2).alias("avg_score"),
            F.min("create_time").alias("t_min"),
            F.max("create_time").alias("t_max"),
        )
        .orderBy("product_id")
        .collect()
    ):
        tag = "目标" if row["is_target"] else "竞品"
        print(
            f"    {row['product_id']}  {row['brand']} {row['model']}（{tag}）"
            f"  {row['n']} 条  均分 {row['avg_score']}"
        )
        print(f"      时间 {row['t_min']} ~ {row['t_max']}")

    # 读回校验
    back = spark.read.parquet(HDFS_CLEANED)
    print(f"\n  读回校验: {back.count()} 行，{len(back.columns)} 列 -> {back.columns}")
    print("\nJob 01 完成")
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
