#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分步探测 PySpark 到底哪一步崩。"""
import sys
import traceback

STEP = 0


def step(name):
    global STEP
    STEP += 1
    print(f"\n--- 步骤 {STEP}: {name} ---", flush=True)


print(f"Python: {sys.version}", flush=True)

step("导入 pyspark")
try:
    import pyspark
    print(f"  pyspark {pyspark.__version__}", flush=True)
except Exception:
    traceback.print_exc(); sys.exit(1)

step("创建 SparkSession")
try:
    from pyspark.sql import SparkSession
    spark = (SparkSession.builder
             .appName("probe").master("local[1]")
             .config("spark.ui.enabled", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    print(f"  Spark {spark.version}", flush=True)
except Exception:
    traceback.print_exc(); sys.exit(1)

step("最简单的 RDD 作业 sc.parallelize().count()")
try:
    print(f"  count = {spark.sparkContext.parallelize(range(10)).count()}", flush=True)
except Exception:
    traceback.print_exc()

step("spark.range(10).count()")
try:
    print(f"  count = {spark.range(10).count()}", flush=True)
except Exception:
    traceback.print_exc()

step("createDataFrame(列表)")
try:
    df = spark.createDataFrame([(1, "a"), (2, "b")], "id int, v string")
    print(f"  schema ok: {df.schema.simpleString()}", flush=True)
    print(f"  count = {df.count()}", flush=True)
except Exception:
    traceback.print_exc()

step("DataFrame 过滤 + 去重")
try:
    from pyspark.sql import functions as F
    df2 = df.filter(F.col("v") != "").dropDuplicates(["id"])
    print(f"  count = {df2.count()}", flush=True)
except Exception:
    traceback.print_exc()

step("写 JSON 到本地（不写 HDFS）")
try:
    import tempfile, os
    d = tempfile.mkdtemp()
    df.write.mode("overwrite").json(os.path.join(d, "out"))
    print(f"  写到 {d}/out 成功", flush=True)
except Exception:
    traceback.print_exc()

step("写 Parquet 到本地并读回")
try:
    import tempfile, os
    d = tempfile.mkdtemp()
    p = os.path.join(d, "pq")
    df.repartition(1).write.mode("overwrite").parquet(p)
    print(f"  读回 count = {spark.read.parquet(p).count()}", flush=True)
except Exception:
    traceback.print_exc()

step("写 HDFS")
try:
    df.write.mode("overwrite").json("hdfs://localhost:9000/spike/_probe/raw")
    print(f"  HDFS 读回 count = {spark.read.json('hdfs://localhost:9000/spike/_probe/raw').count()}", flush=True)
    jvm = spark._jvm
    uri = jvm.java.net.URI("hdfs://localhost:9000")
    fs = jvm.org.apache.hadoop.fs.FileSystem.get(uri, spark._jsc.hadoopConfiguration())
    fs.delete(jvm.org.apache.hadoop.fs.Path("hdfs://localhost:9000/spike/_probe"), True)
except Exception:
    traceback.print_exc()

spark.stop()
print("\n探测结束", flush=True)
