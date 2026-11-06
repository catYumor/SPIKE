# SPIKE 产品技术栈说明与实现方法

> **项目代号：SPIKE**（Smart Product Insight & Knowledge Engine）  
> **定位：** 面向品牌产品团队的 2B 电商评论洞察平台。通过方面级情感分析（ABSA）从好评/差评中抽取产品属性与情感，识别产品优势、痛点、改进优先级，并支持竞品对比。  
> **本文档目标：** 交付给其他实现 Agent 时，可按本文直接完成环境搭建、数据采集、存储、计算、后端、前端与验收。

---

## 1. 项目边界与核心输出

### 1.1 必须实现的功能

1. 采集目标产品及竞品的公开电商评论（以京东为主）。
2. 原始评论存入 HDFS 伪分布式。
3. 使用 PySpark Local 模式完成清洗、ABSA 抽取、属性归并、聚合、优先级和竞品差距计算。
4. 结构化结果写入 MySQL。
5. Flask 提供只读 API。
6. Vue 3 + ECharts 展示 2B 决策看板：
   - 产品健康总览
   - 优势清单
   - 痛点清单及原始评论证据
   - 竞品差距矩阵

### 1.2 不实现的功能

- 不做 2C 用户推荐。
- 不做用户画像和协同过滤。
- 不采集用户隐私数据。
- 不绕过强风控平台；淘宝/天猫不作为主力数据源。
- 不在伪分布式上部署 YARN/Spark 集群，PySpark 使用 Local 模式。

---

## 2. 技术栈清单

| 层次 | 技术 | 版本建议 | 说明 |
|---|---|---|---|
| 操作系统 | Linux | Ubuntu 22.04 / CentOS 7+ | 单机 |
| JDK | OpenJDK | 8 或 11 | Hadoop/Spark 兼容 |
| 分布式存储 | Hadoop HDFS 伪分布式 | 3.3.x | `dfs.replication=1` |
| 分布式计算 | PySpark Local | 3.5.x | 通过 HDFS 客户端读写 |
| 关系数据库 | MySQL | 8.0 | utf8mb4 |
| 爬虫 | Python + Requests | Python 3.9+ | 京东评论接口为主 |
| 爬虫备选 | Playwright | 可选 | 登录态/懒加载场景 |
| 中文分词 | jieba | 最新 | 规则回退与关键词 |
| 情感回退 | Snownlp | 最新 | 规则兜底 |
| ABSA 主模型 | ModelScope SiameseAOE / SiameseUIE 中文模型 | 以实际可用模型 ID 为准 | 零样本/少样本抽取 |
| 深度学习运行时 | PyTorch + transformers + modelscope | 与模型匹配 | 离线批处理 |
| 后端 | Flask | 3.x | 只读 MySQL |
| 前端 | Vue 3 + Vite + ECharts 5 | 最新稳定 | 2B 看板 |
| 调度 | cron / 手动脚本 | 系统自带 | 离线批处理 |
| 反向代理 | Nginx | 可选 | 前端静态资源与 API 代理 |

---

## 3. 总体架构与数据流

```text
京东评论采集
   ↓ JSONL
本地临时目录
   ↓ hdfs dfs -put
HDFS: /spike/raw/reviews/product_id=xxx/
   ↓ PySpark Job 01
HDFS: /spike/staging/cleaned/
   ↓ PySpark Job 02 + ABSA 模型
HDFS: /spike/staging/aspects/
   ↓ PySpark Job 03/04 + MySQL JDBC
MySQL: spike 数据库
   ↓ Flask API
Vue 3 + ECharts 看板
```

**关键原则：**
- 原始评论文本只存 HDFS，不进入 MySQL。
- MySQL 只存结构化结果、聚合结果、优先级和竞品矩阵。
- 重计算全部离线完成，Flask 不触发模型推理。
- 每个 Job 可独立重跑，按 `product_id` 分区幂等写入。

---

## 4. 推荐目录结构

```text
spike/
├── config/
│   ├── products.yaml
│   ├── aspects.yaml
│   └── settings.py
├── crawler/
│   ├── jd_review_crawler.py
│   └── upload_to_hdfs.sh
├── spark_jobs/
│   ├── 01_clean.py
│   ├── 02_absa_extract.py
│   ├── 03_aggregate.py
│   └── 04_priority_gap.py
├── absa/
│   ├── model_adapter.py
│   └── rule_fallback.py
├── backend/
│   ├── app.py
│   ├── db.py
│   └── routes/
├── frontend/
│   ├── src/
│   └── vite.config.js
├── sql/
│   └── schema.sql
├── scripts/
│   ├── init_hdfs.sh
│   ├── run_pipeline.sh
│   └── check_env.sh
└── README.md
```

---

## 5. 数据采集模块

### 5.1 数据源与采集策略

- 主力：京东商品评论公开接口形态。
- 核心参数：`productId`、`score`（0 全部 / 1 差评 / 2 中评 / 3 好评）、`sortType`（6 按时间）、`page`、`pageSize`。
- 每个产品建议采集 500—3000 条；目标产品与至少 1 个竞品同品类。
- 频率控制：单产品分页间隔 1—3 秒，失败重试 3 次，指数退避。
- 只采集公开评论，不采集用户隐私字段。

### 5.2 产品配置 `config/products.yaml`

```yaml
category: 手机
products:
  - product_id: "100012043978"
    brand: "品牌A"
    model: "A1"
    is_target: true
  - product_id: "100026667910"
    brand: "品牌B"
    model: "B2"
    is_target: false
```

### 5.3 原始 JSONL 字段规范

每条评论一行 JSON：

```json
{
  "review_id": "jd_100012043978_123456",
  "product_id": "100012043978",
  "brand": "品牌A",
  "model": "A1",
  "is_target": true,
  "score": 4,
  "content": "拍照清晰，但电池续航一般",
  "create_time": "2024-05-01 12:30:00",
  "source": "jd",
  "crawl_time": "2026-10-18 10:00:00"
}
```

### 5.4 HDFS 写入规范

```bash
hdfs dfs -mkdir -p /spike/raw/reviews/product_id=100012043978
hdfs dfs -put -f data/raw/100012043978.jsonl /spike/raw/reviews/product_id=100012043978/
```

---

## 6. HDFS 目录规范

| 路径 | 用途 |
|---|---|
| `/spike/raw/reviews/product_id=<id>/` | 原始评论 JSONL |
| `/spike/staging/cleaned/product_id=<id>/` | 清洗后 Parquet/JSON |
| `/spike/staging/aspects/product_id=<id>/` | ABSA 抽取结果 |
| `/spike/output/aggregation/` | 聚合结果备份 |
| `/spike/checkpoint/` | 可选 |

HDFS 伪分布式配置：
- `core-site.xml`：`fs.defaultFS=hdfs://localhost:9000`
- `hdfs-site.xml`：`dfs.replication=1`
- 启动：格式化 NameNode → `start-dfs.sh` → `hdfs dfs -ls /` 验证。

---

## 7. MySQL 数据模型

数据库名：`spike`，字符集 `utf8mb4`。

### 7.1 DDL

```sql
CREATE DATABASE IF NOT EXISTS spike DEFAULT CHARACTER SET utf8mb4;
USE spike;

CREATE TABLE products (
  product_id VARCHAR(64) PRIMARY KEY,
  brand VARCHAR(128),
  model VARCHAR(128),
  category VARCHAR(128),
  is_target TINYINT DEFAULT 0
);

CREATE TABLE aspect_dictionary (
  aspect_id INT AUTO_INCREMENT PRIMARY KEY,
  aspect_name VARCHAR(128) NOT NULL UNIQUE,
  synonyms TEXT,
  category VARCHAR(128)
);

CREATE TABLE review_aspects (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  review_id VARCHAR(128),
  product_id VARCHAR(64),
  aspect_name VARCHAR(128),
  sentiment ENUM('positive','negative','neutral'),
  confidence DECIMAL(5,4),
  evidence_text TEXT,
  create_time DATETIME,
  INDEX idx_product_aspect (product_id, aspect_name),
  INDEX idx_review (review_id)
);

CREATE TABLE aspect_aggregation (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id VARCHAR(64),
  aspect_name VARCHAR(128),
  pos_count INT DEFAULT 0,
  neg_count INT DEFAULT 0,
  neu_count INT DEFAULT 0,
  total_count INT DEFAULT 0,
  pos_rate DECIMAL(6,4),
  neg_rate DECIMAL(6,4),
  UNIQUE KEY uk_product_aspect (product_id, aspect_name)
);

CREATE TABLE aspect_priority (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id VARCHAR(64),
  aspect_name VARCHAR(128),
  kano_type ENUM('must_be','one_dimensional','attractive','indifferent'),
  priority_score DECIMAL(8,4),
  label ENUM('advantage','painpoint','neutral'),
  updated_at DATETIME,
  UNIQUE KEY uk_product_aspect (product_id, aspect_name)
);

CREATE TABLE competitive_gap (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  category VARCHAR(128),
  aspect_name VARCHAR(128),
  target_product_id VARCHAR(64),
  competitor_product_id VARCHAR(64),
  target_pos_rate DECIMAL(6,4),
  competitor_pos_rate DECIMAL(6,4),
  pos_gap DECIMAL(7,4),
  target_neg_rate DECIMAL(6,4),
  competitor_neg_rate DECIMAL(6,4),
  neg_gap DECIMAL(7,4),
  updated_at DATETIME,
  UNIQUE KEY uk_gap (aspect_name, target_product_id, competitor_product_id)
);
```

---

## 8. 分布式计算作业规格

### 8.1 Job 01：清洗

**输入：** `/spike/raw/reviews/product_id=*/`  
**输出：** `/spike/staging/cleaned/`  
**逻辑：**
1. 读取 JSONL。
2. 过滤 `content` 为空或长度 < 5。
3. 按 `review_id` 去重。
4. 解析 `score`、`create_time`。
5. 保留 `product_id`、`brand`、`model`、`is_target`。
6. 写 Parquet。

### 8.2 Job 02：ABSA 抽取

**输入：** cleaned Parquet  
**输出：** `/spike/staging/aspects/`，字段：
`review_id, product_id, aspect_name, sentiment, confidence, evidence_text, create_time`

**实现要求：**
- 封装 `absa/model_adapter.py`，提供统一接口：
  ```python
  def extract_aspects(text: str) -> list[dict]:
      # 返回 [{"aspect": "...", "sentiment": "positive|negative|neutral", "confidence": 0.0, "evidence": "..."}]
  ```
- 主路径调用 SiameseAOE/SiameseUIE 中文模型；模型加载在 `mapPartitions` 内每分区一次。
- 失败或低置信度时调用 `rule_fallback.py`：
  - jieba 词性标注取名词/名词短语候选属性；
  - Snownlp 或情感词典判断邻近情感；
  - 输出相同结构。
- 每条评论最多保留 5 个属性，避免噪声。

### 8.3 Job 03：属性归并与聚合

**逻辑：**
1. 加载 `aspect_dictionary` 同义词表。
2. 将原始属性映射到标准 `aspect_name`；未命中则保留原词并记录待人工维护。
3. 按 `(product_id, aspect_name)` 聚合：
   - `pos_count`、`neg_count`、`neu_count`、`total_count`
   - `pos_rate = pos_count / total_count`
   - `neg_rate = neg_count / total_count`
4. 写入 `review_aspects` 和 `aspect_aggregation`。

### 8.4 Job 04：优先级与竞品差距

**优势/痛点规则：**
- 优势：`total_count >= 5` 且 `pos_rate >= 0.75`
- 痛点：`total_count >= 5` 且 `neg_rate >= 0.30`
- 其余为 `neutral`

**Kano 规则化映射：**
- `neg_rate >= 0.40` 且 `pos_rate <= 0.40` → `must_be`
- `pos_rate >= 0.60` 且 `neg_rate >= 0.20` → `one_dimensional`
- `pos_rate >= 0.75` 且 `neg_rate <= 0.15` → `attractive`
- 其他 → `indifferent`

**痛点优先级分数：**

```text
severity = (1星负面提及数 * 1.0 + 2星负面提及数 * 0.8 + 3星负面提及数 * 0.5) / neg_count
kano_weight = {must_be: 1.0, one_dimensional: 0.7, attractive: 0.4, indifferent: 0.2}
priority_score = neg_count * severity * kano_weight[kano_type]
```

**竞品差距：**
- 按 `category` 和 `aspect_name` 对齐目标产品与竞品。
- `pos_gap = target_pos_rate - competitor_pos_rate`
- `neg_gap = target_neg_rate - competitor_neg_rate`
- 写入 `competitive_gap`。

---

## 9. 算法与属性字典规范

### 9.1 属性字典 `config/aspects.yaml`

示例：

```yaml
aspects:
  - name: 电池续航
    synonyms: [电池, 续航, 电量, 待机, 充电]
  - name: 影像拍照
    synonyms: [拍照, 相机, 像素, 成像, 夜景]
  - name: 屏幕显示
    synonyms: [屏幕, 显示, 刷新率, 亮度]
  - name: 做工质感
    synonyms: [做工, 质感, 手感, 外壳]
  - name: 物流配送
    synonyms: [物流, 快递, 配送, 发货]
  - name: 客服服务
    synonyms: [客服, 售后, 服务]
  - name: 性价比
    synonyms: [价格, 性价比, 贵, 便宜]
```

### 9.2 ABSA Schema 适配

实现 Agent 需在 `model_adapter.py` 中固定输出统一结构。若模型输出为“属性词-情感词”对，则转换为 `aspect_name` 和 `sentiment`；情感词按词典映射到 positive/negative/neutral。

### 9.3 证据文本

`evidence_text` 保存包含该属性及其上下文的短句，长度 100—200 字，供前端展示“用户原话”。

---

## 10. 后端 API 规格

Flask 只读 MySQL。统一返回 JSON。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/products` | 产品列表，含 `is_target` |
| GET | `/api/products/<product_id>/overview` | 健康总览：属性正负率、标签、Kano |
| GET | `/api/products/<product_id>/advantages` | 优势清单 |
| GET | `/api/products/<product_id>/painpoints` | 痛点清单，按 priority_score 降序 |
| GET | `/api/products/<product_id>/aspects` | 全部属性聚合 |
| GET | `/api/competitive-gap?target_id=&competitor_ids=` | 竞品差距矩阵 |
| GET | `/api/evidence?product_id=&aspect_name=&sentiment=` | 原始评论证据，分页 |
| GET | `/api/health` | 健康检查 |

响应示例：

```json
{
  "product_id": "100012043978",
  "aspects": [
    {"aspect_name": "电池续航", "pos_rate": 0.32, "neg_rate": 0.51, "label": "painpoint", "kano_type": "must_be", "priority_score": 18.7}
  ]
}
```

---

## 11. 前端看板规格

Vue 3 + Vite + ECharts。三个主视图：

1. **产品健康总览**
   - 属性卡片：正面率、负面率、Kano 类型、标签颜色。
   - 支持切换目标产品和竞品。

2. **优势/痛点清单**
   - 痛点按 `priority_score` 排序。
   - 点击痛点展开证据评论列表。
   - 优势清单按正面率排序。

3. **竞品差距矩阵**
   - 行：属性；列：产品。
   - 单元格显示正面率/负面率，颜色高亮差距。
   - 高亮“自身优势”和“自身短板”。

---

## 12. 部署与运行步骤

### 12.1 环境初始化

1. 安装 JDK、Hadoop、MySQL、Python、Node.js。
2. 配置 HDFS 伪分布式，`dfs.replication=1`。
3. 启动 HDFS 并验证。
4. 创建 MySQL 数据库并执行 `sql/schema.sql`。
5. 安装 Python 依赖：`pyspark`、`flask`、`pymysql`、`jieba`、`snownlp`、`torch`、`transformers`、`modelscope`、`requests`、`pyyaml`。
6. 将 MySQL JDBC 驱动放入 Spark classpath。

### 12.2 运行流水线

```bash
# 1. 采集
python crawler/jd_review_crawler.py --config config/products.yaml

# 2. 上传 HDFS
bash crawler/upload_to_hdfs.sh

# 3. 清洗
spark-submit spark_jobs/01_clean.py

# 4. ABSA
spark-submit spark_jobs/02_absa_extract.py

# 5. 聚合
spark-submit spark_jobs/03_aggregate.py

# 6. 优先级与竞品
spark-submit spark_jobs/04_priority_gap.py

# 7. 启动后端
cd backend && flask run --host 0.0.0.0 --port 5000

# 8. 启动前端
cd frontend && npm run dev
```

### 12.3 cron 示例

```cron
0 2 * * * cd /opt/spike && bash scripts/run_pipeline.sh >> logs/pipeline.log 2>&1
```

---

## 13. 验收测试

| 编号 | 验收项 | 通过标准 |
|---|---|---|
| T1 | HDFS 伪分布式 | `hdfs dfs -ls /spike/raw` 可访问 |
| T2 | 采集 | 目标产品 + 至少 1 个竞品，各 ≥500 条评论 |
| T3 | 清洗 | 无空评论、无重复 review_id |
| T4 | ABSA | 随机抽检 100 条，属性-情感对可读；输出统一结构 |
| T5 | 聚合 | `aspect_aggregation` 正负率计算正确 |
| T6 | 优先级 | 痛点按 `priority_score` 降序，Kano 类型非空 |
| T7 | 竞品差距 | 至少 5 个共同属性有 `pos_gap/neg_gap` |
| T8 | API | 所有接口 200，返回结构符合约定 |
| T9 | 前端 | 三个视图可切换，痛点可展开证据 |
| T10 | 幂等 | 重跑 Job 不产生重复聚合行 |

---

## 14. 风险与回退

1. **京东接口不稳定**：降低频率、分产品小批量、保留本地 JSONL 重试。
2. **ABSA 模型精度不足**：规则回退 + 人工抽检报告；答辩展示错误分析。
3. **伪分布式资源冲突**：PySpark 只用 Local 模式，不连 YARN。
4. **属性同义词覆盖不足**：未命中属性保留原词并记录，前端可显示“待归并”标签。
5. **竞品数据不足**：产品配置阶段强制要求至少 1 个竞品，否则竞品页面显示提示。

---

## 15. 交付物清单

1. 源码仓库，包含 `crawler/`、`spark_jobs/`、`absa/`、`backend/`、`frontend/`。
2. `sql/schema.sql`。
3. `config/products.yaml`、`config/aspects.yaml`。
4. `README.md`：环境搭建、运行命令、验收步骤。
5. 样例数据与运行截图。
6. ABSA 抽检报告：100 条样本的准确率/错误分析。
7. 流水线日志与 HDFS 目录截图。
