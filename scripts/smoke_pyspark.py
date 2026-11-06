#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SPIKE PySpark 功能性冒烟测试：JSONL -> HDFS -> Spark 清洗去重 -> Parquet -> 读回校验。

对应规格 §5（评论 JSONL 字段）与 §8（Spark 作业）的最小可用版本，
用于确认 PySpark 3.5.3 + HDFS 3.3.5 + JDK 11 这条链路真的通了。
"""
import sys
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

HDFS = "hdfs://localhost:9000"
RAW = f"{HDFS}/spike/raw/reviews/_smoke"
CLEAN = f"{HDFS}/spike/staging/cleaned/_smoke"

# 6 条原始数据：其中 1 条内容为空（应被过滤），1 条 review_id 重复（应被去重）
ROWS = [
    ("r1", "100012043978", "拍照清晰，夜景效果好", 5, "2026-10-01 10:00:00"),
    ("r2", "100012043978", "电池续航一般，一天要充两次", 3, "2026-10-01 10:05:00"),
    ("r3", "100012043978", "", 4, "2026-10-01 10:10:00"),
    ("r1", "100012043978", "拍照清晰，夜景效果好", 5, "2026-10-01 10:00:00"),
    ("r4", "100012043978", "屏幕显示细腻，但做工质感一般", 3, "2026-10-01 10:15:00"),
    ("r5", "100012043979", "性价比高，物流很快", 5, "2026-10-01 10:20:00"),
]
EXPECT_CLEAN = 4   # 6 - 1(空内容) - 1(重复) = 4


def main():
    spark = (SparkSession.builder
             .appName("SPIKE-smoke")
             .master("local[2]")
             .config("spark.hadoop.fs.defaultFS", HDFS)
             .config("spark.sql.shuffle.partitions", "4")
             .config("spark.ui.enabled", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    print(f"  Spark 版本: {spark.version}")
    print(f"  Spark 内置 Hadoop 客户端: {spark.sparkContext._jsc.hadoopConfiguration().get('hadoop.common.configuration.version', 'n/a')}")
    print(f"  defaultFS : {spark.sparkContext._jsc.hadoopConfiguration().get('fs.defaultFS')}")

    # ---- 1. 写 JSONL 到 HDFS ----
    df = spark.createDataFrame(
        ROWS, "review_id string, product_id string, content string, score int, create_time string"
    )
    df.write.mode("overwrite").json(RAW)
    n_raw = spark.read.json(RAW).count()
    print(f"  [1] 写入 HDFS 原始数据: {n_raw} 行 -> {RAW}")
    assert n_raw == len(ROWS), f"写入行数不符: {n_raw} != {len(ROWS)}"

    # ---- 2. 清洗：丢空内容 + 按 review_id 去重 ----
    raw = spark.read.json(RAW)
    cleaned = (raw
               .filter(F.col("content").isNotNull() & (F.trim(F.col("content")) != ""))
               .dropDuplicates(["review_id"])
               .withColumn("content_len", F.length("content")))
    n_clean = cleaned.count()
    print(f"  [2] 清洗后: {n_clean} 行（过滤空内容 1 条，去重 1 条）")
    assert n_clean == EXPECT_CLEAN, f"清洗结果不符: {n_clean} != {EXPECT_CLEAN}"

    # ---- 3. 按产品分区写 Parquet ----
    cleaned.write.mode("overwrite").partitionBy("product_id").parquet(CLEAN)
    print(f"  [3] 写入 Parquet（按 product_id 分区）-> {CLEAN}")

    # ---- 4. 读回校验 ----
    back = spark.read.parquet(CLEAN)
    n_back = back.count()
    # 注意：Spark 默认开启分区列类型推断，纯数字的 product_id 读回时会变成 bigint，
    # 所以统一转字符串再比较（这是真实项目里很容易踩的坑）
    parts = sorted(str(r["product_id"]) for r in back.select("product_id").distinct().collect())
    print(f"  [4] 读回: {n_back} 行，分区: {parts}")
    assert n_back == n_clean, f"读回行数不符: {n_back} != {n_clean}"
    assert parts == ["100012043978", "100012043979"], f"分区不符: {parts}"

    # ---- 5. 简单聚合（验证 SQL 能力）----
    agg = back.groupBy("product_id").agg(
        F.count("*").alias("n"),
        F.round(F.avg("score"), 2).alias("avg_score"),
        F.round(F.avg("content_len"), 1).alias("avg_len"),
    ).orderBy("product_id")
    print("  [5] 聚合结果:")
    for r in agg.collect():
        print(f"        product={r['product_id']} 条数={r['n']} 均分={r['avg_score']} 平均字数={r['avg_len']}")

    # ---- 6. 中文处理（jieba 分词）----
    import jieba
    sample = back.select("content").limit(1).collect()[0]["content"]
    toks = [t for t in jieba.cut(sample) if t.strip()]
    print(f"  [6] jieba 分词: {sample!r} -> {toks}")
    assert len(toks) > 0, "jieba 分词失败"

    # ---- 清理临时数据 ----
    jvm = spark._jvm
    uri = jvm.java.net.URI(HDFS)
    fs = jvm.org.apache.hadoop.fs.FileSystem.get(uri, spark._jsc.hadoopConfiguration())
    for p in (RAW, CLEAN):
        fs.delete(jvm.org.apache.hadoop.fs.Path(p), True)
    print("  [7] 已清理 HDFS 临时数据")

    spark.stop()
    print("\n  >>> PYSPARK SMOKE OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  >>> PYSPARK SMOKE FAILED: {e}", file=sys.stderr)
        sys.exit(1)
