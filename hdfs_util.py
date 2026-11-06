# -*- coding: utf-8 -*-
"""HDFS 小工具：等待可写 + 带重试的写入。

为什么需要它：本项目跑在 WSL 里，发行版空闲约 15 秒就会关机，
下次调用时 HDFS 重新启动，NameNode 每次都要进 safe mode（约 8 秒）。
作业在算完准备写盘时撞上 safe mode 就会整个失败，因此统一在这里重试。

用法：
    from hdfs_util import wait_hdfs_writable, write_parquet
    wait_hdfs_writable()
    write_parquet(df, HDFS_CLEANED)
"""

from __future__ import annotations

import subprocess
import time


def safemode_off() -> bool:
    """NameNode 是否已退出 safe mode。"""
    try:
        out = subprocess.run(
            ["hdfs", "dfsadmin", "-safemode", "get"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return "OFF" in out.stdout
    except Exception:  # noqa: BLE001
        return False


def wait_hdfs_writable(timeout: int = 180, verbose: bool = True) -> bool:
    """阻塞直到 NameNode 退出 safe mode。返回是否成功。"""
    t0 = time.time()
    waited = False
    while time.time() - t0 < timeout:
        if safemode_off():
            if waited and verbose:
                print(f"  [HDFS] safe mode 已退出（等待 {time.time() - t0:.0f}s）")
            return True
        if not waited and verbose:
            print("  [HDFS] NameNode 在 safe mode，等待中...")
        waited = True
        time.sleep(2)
    if verbose:
        print(f"  [HDFS] 等待 {timeout}s 仍未退出 safe mode")
    return False


def write_parquet(
    df,
    path: str,
    partitions: int = 2,
    retries: int = 5,
    num_partitions: int | None = None,
    partition_by: str | None = None,
):
    """写 Parquet，遇到 safe mode 自动等待重试。

    partition_by: 按该列分区，产出 product_id=<id>/ 目录（规格 §6 要求）。
    """
    n = num_partitions if num_partitions is not None else partitions
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            wait_hdfs_writable(verbose=(attempt == 1))
            out = df.repartition(n, partition_by) if partition_by else df.repartition(n)
            writer = out.write.mode("overwrite")
            if partition_by:
                writer = writer.partitionBy(partition_by)
            writer.parquet(path)
            return True
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).splitlines()[0][:120]
            print(f"  [HDFS] 第 {attempt} 次写入失败: {msg}")
            if attempt < retries:
                time.sleep(10)
    raise RuntimeError(f"写入 {path} 失败（重试 {retries} 次）: {last_err}")
