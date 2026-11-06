#!/usr/bin/env bash
# SPIKE 环境变量。用法：source scripts/spike-env.sh
# 项目根目录：Windows D:\workspace\SPIKE  <->  WSL /mnt/d/workspace/SPIKE

export SPIKE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- 运行时目录 ----
# 日志留在 WSL 的 ext4 上：HDFS 启动路径（cron @reboot）不应依赖 /mnt/d 已挂载
export SPIKE_LOG_DIR="${SPIKE_LOG_DIR:-/home/hadoop/logs}"

# ---- JDK / Hadoop ----
export JAVA_HOME=/home/hadoop/jdk-11
export HADOOP_HOME=/usr/local/hadoop
export HADOOP_CONF_DIR="$HADOOP_HOME/etc/hadoop"
export HADOOP_MAPRED_HOME="$HADOOP_HOME"
export PATH="$JAVA_HOME/bin:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$PATH"

# ---- Python 虚拟环境 ----
export SPIKE_VENV="${SPIKE_VENV:-/home/hadoop/spike-venv}"
export PYSPARK_PYTHON="$SPIKE_VENV/bin/python"
export PYSPARK_DRIVER_PYTHON="$SPIKE_VENV/bin/python"
export SPARK_LOCAL_IP=127.0.0.1

# ---- MySQL（WSL 内 3307；3306 被 Windows MySQL80 占用）----
export SPIKE_MYSQL_HOST="${SPIKE_MYSQL_HOST:-127.0.0.1}"
export SPIKE_MYSQL_PORT="${SPIKE_MYSQL_PORT:-3307}"
export SPIKE_MYSQL_DB="${SPIKE_MYSQL_DB:-spike}"
export SPIKE_MYSQL_USER="${SPIKE_MYSQL_USER:-spike}"
export SPIKE_MYSQL_PASSWORD="${SPIKE_MYSQL_PASSWORD:-spike_pass_2026}"
export SPIKE_MYSQL_URL="jdbc:mysql://${SPIKE_MYSQL_HOST}:${SPIKE_MYSQL_PORT}/${SPIKE_MYSQL_DB}?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai"

# ---- HDFS ----
export SPIKE_HDFS_ROOT="${SPIKE_HDFS_ROOT:-hdfs://localhost:9000}"
