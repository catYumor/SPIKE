"""SPIKE 全局配置（对应规格 §4 config/settings.py）。

⚠️ 路径与数据库参数必须与 scripts/spike-env.sh 保持一致，两处改动需同步。

在 WSL 的 hadoop 用户下、项目 venv 里导入：

    cd /mnt/d/workspace/SPIKE
    source scripts/spike-env.sh
    python -c "from config.settings import HDFS_RAW, MYSQL; print(HDFS_RAW)"
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# ---------------------------------------------------------------- 路径

BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_DIR = BASE_DIR / "config"
SQL_DIR = BASE_DIR / "sql"
SCRIPTS_DIR = BASE_DIR / "scripts"
CRAWLER_DIR = BASE_DIR / "crawler"
SPARK_JOBS_DIR = BASE_DIR / "spark_jobs"
ABSA_DIR = BASE_DIR / "absa"
BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIR = BASE_DIR / "frontend"

PRODUCTS_YAML = CONFIG_DIR / "products.yaml"
ASPECTS_YAML = CONFIG_DIR / "aspects.yaml"

# ---------------------------------------------------------------- HDFS

HDFS_ROOT = os.environ.get("SPIKE_HDFS_ROOT", "hdfs://localhost:9000")

HDFS_RAW = f"{HDFS_ROOT}/spike/raw/reviews"
HDFS_CLEANED = f"{HDFS_ROOT}/spike/staging/cleaned"
HDFS_ASPECTS = f"{HDFS_ROOT}/spike/staging/aspects"
HDFS_OUTPUT = f"{HDFS_ROOT}/spike/output/aggregation"
HDFS_CHECKPOINT = f"{HDFS_ROOT}/spike/checkpoint"

# ---------------------------------------------------------------- MySQL

MYSQL = {
    "host": os.environ.get("SPIKE_MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("SPIKE_MYSQL_PORT", "3307")),  # 注意：不是 3306
    "user": os.environ.get("SPIKE_MYSQL_USER", "spike"),
    "password": os.environ.get("SPIKE_MYSQL_PASSWORD", "spike_pass_2026"),
    "database": os.environ.get("SPIKE_MYSQL_DB", "spike"),
    "charset": "utf8mb4",
}

# ---------------------------------------------------------------- Spark

SPARK_APP_NAME = "SPIKE"
SPARK_MASTER = "local[*]"

# 提交到 YARN 时必须先 start-yarn.sh，否则客户端会卡在 localhost:8032 重试
# （见 docs/环境搭建说明.md §6）

# ---------------------------------------------------------------- 业务常量

# 验收 T4：随机抽检条数
ABSA_SAMPLE_SIZE = 100

# 验收 T7：竞品差距至少覆盖的共同属性数
MIN_COMMON_ASPECTS = 5

# ---------------------------------------------------------------- 加载器


def load_products() -> list[dict]:
    """读取 config/products.yaml 的 products 列表。"""
    with PRODUCTS_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["products"]


def load_crawl_config() -> dict:
    """读取 config/products.yaml 的 crawl 段。"""
    with PRODUCTS_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["crawl"]


def load_aspects() -> list[dict]:
    """读取 config/aspects.yaml 的 aspects 列表。"""
    with ASPECTS_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["aspects"]


def load_sentiment_rules() -> dict:
    """读取 config/aspects.yaml 的 sentiment 段。"""
    with ASPECTS_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["sentiment"]


def target_product() -> dict:
    """返回 is_target=true 的本方产品。"""
    targets = [p for p in load_products() if p.get("is_target")]
    if len(targets) != 1:
        raise ValueError(f"products.yaml 中 is_target=true 的产品应恰好 1 个，实际 {len(targets)} 个")
    return targets[0]


def competitors() -> list[dict]:
    """返回所有竞品。"""
    return [p for p in load_products() if not p.get("is_target")]
