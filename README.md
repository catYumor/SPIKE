# SPIKE — Smart Product Insight & Knowledge Engine

面向品牌产品团队的 2B 电商评论洞察平台。通过方面级情感分析（ABSA）从评论中抽取产品属性与情感，
识别产品优势、痛点、改进优先级，并支持竞品对比。

> 规格原文见 [`docs/SPIKE-规格说明.md`](docs/SPIKE-规格说明.md)
> 环境搭建与踩坑记录见 [`docs/环境搭建说明.md`](docs/环境搭建说明.md)

## 目录约定

**本项目所有文件都放在本目录内。** Windows 路径 `D:\workspace\SPIKE`，
WSL 路径 `/mnt/d/workspace/SPIKE`，两者是同一个目录。

```
SPIKE/
├── README.md              本文件
├── docs/                  规格说明、环境文档、实现偏差记录、采集交接文档
│   ├── SPIKE-规格说明.md
│   ├── 环境搭建说明.md
│   ├── 实现偏差记录.md    与规格不一致处的说明（D1–D9）
│   ├── 数据采集交接说明.md  可直接转发给帮忙采数据的人
│   ├── 验收报告.md        T1–T10 验收结果（自动生成）
│   ├── ABSA抽检报告.md    100 条抽样明细（自动生成）
│   ├── 补抓差评操作单.md  明天补抓差评 → 一键更新的完整步骤
│   ├── 存档/              流水线日志、HDFS 目录清单（§15.7 交付物）
│   └── screenshots/       三个页面的截图
├── sql/
│   └── schema.sql         MySQL 建表 + 种子数据
├── scripts/               运维脚本（HDFS 生命周期、环境自检、冒烟测试、流水线）
│   ├── spike-env.sh       环境变量，其他脚本 source 它
│   ├── spike-hdfs-boot.sh HDFS 幂等启动（cron 看门狗调用）
│   ├── spike-hdfs-start.sh
│   ├── spike-hdfs-stop.sh
│   ├── spike-hdfs-status.sh
│   ├── init_hdfs.sh       幂等创建 §6 的 5 个 HDFS 目录
│   ├── upload_to_hdfs.sh  把 data/raw/*.jsonl 上传到 /spike/raw/reviews/
│   ├── run_pipeline.sh    按 §8 顺序编排 4 个作业（--from / --only / --list）
│   ├── run_backend.sh     后端服务管理（start/stop/restart/status/test）
│   ├── test_api.py        后端 12 项接口自测
│   ├── acceptance_test.py 验收测试 T1–T10（自动生成验收报告）
│   ├── screenshot_ui.py   headless 打开三视图、断言并刷新截图
│   ├── refresh_all.sh     一条命令：起 HDFS → 校验 → 上传 → 流水线 → 验收 → 截图
│   ├── merge_reviews.py   合并补抓的评论（按 review_id 去重，自动备份）
│   ├── start_slow_crawl.sh 慢速采集器后台管理（可设首试延迟/限流等待/每条间隔）
│   ├── check_env.sh       静态自检（25 项）
│   ├── smoke_test.sh      功能性验证：实际读写 HDFS/PySpark/MySQL（9 项）
│   ├── smoke_pyspark.py   PySpark 冒烟作业（读 HDFS → 清洗 → Parquet → 读回）
│   └── probe_pyspark.py   PySpark 分步探测（排障用）
├── config/                配置（对应规格 §4）
│   ├── settings.py        全局配置：路径、HDFS、MySQL、加载器
│   ├── products.yaml      目标产品 + 竞品清单、采集参数
│   └── aspects.yaml       属性字典 + 情感极性规则
├── crawler/               采集（对应规格 §5）
│   ├── jd_review_crawler.py   统一 CLI，provider = jd / local / mock
│   ├── jd_slow_crawl.py       京东慢速可续传采集器（长时间后台跑）
│   ├── probe_with_cookie.py   注入 cookie 探测评论接口是否可用
│   ├── validate_jsonl.py      交付数据格式校验
│   └── .secrets/cookies.txt   登录 cookie（已 gitignore）
├── spark_jobs/            分布式作业（对应规格 §8，已完成）
│   ├── 01_clean.py        清洗：raw JSONL -> staging/cleaned
│   ├── 02_absa_extract.py ABSA 抽取：cleaned -> staging/aspects
│   ├── 03_aggregate.py    归并聚合 -> output/aggregation + MySQL
│   └── 04_priority_gap.py 痛点优先级 + 竞品差距 -> MySQL
├── absa/                  属性情感抽取（对应规格 §8.2 / §9）
│   ├── model_adapter.py   统一接口 extract_aspects()，模型不可用时自动降级
│   └── rule_fallback.py   词典 + jieba 规则抽取，离线可跑
├── hdfs_util.py           HDFS 写入助手（safe mode 自动等待重试）
├── backend/               Flask 只读 API（规格 §10）
│   ├── app.py             应用入口，存在 dist 时一并托管前端
│   ├── db.py              MySQL 访问层（只读）
│   └── routes/            products / gap / evidence / meta
├── frontend/              Vue 3 + Vite + ECharts（规格 §11）
│   ├── src/views/         Dashboard / Insights / GapMatrix
│   ├── src/components/    EChart 封装
│   └── dist/              打包产物（Flask 直接托管）
└── logs/                  后端与采集的后台日志
```

### 全栈已跑通

后端 8 个接口 + 前端 3 个视图全部实现并联调通过：

```bash
bash scripts/run_backend.sh start    # 起后端（含托管前端）
# 浏览器打开 http://127.0.0.1:5000
```

三个页面：**产品健康总览**（KPI + 堆叠柱状图 + 雷达图 + 属性明细）、
**优势与痛点清单**（可展开用户原话）、**竞品差距矩阵**（正面率对照 + 差值排序）。

### 计算流水线已可跑通

```bash
bash scripts/run_pipeline.sh          # 4 个作业依次执行
bash scripts/run_pipeline.sh --list   # 只看步骤清单
bash scripts/run_pipeline.sh --only 3 # 只跑第 3 步
```

用 mock 数据（1000 条评论）实测：**全流程 59 秒，4 步全部成功**，
产出 `review_aspects` 1343 行、`aspect_aggregation` 14 行、
`aspect_priority` 14 行、`competitive_gap` 7 行。

**当前仓库里跑的是真实数据**：两个商品**各 700 条**真实京东评论
（`source: "jd"`，含京东特有的 `&hellip;` 实体和「外形外观：」式标签开头，
经 `crawler/validate_jsonl.py` 校验通过）：

| 商品 | 好评 | 差评 | 合计 |
|---|---|---|---|
| 小米 REDMI K90（目标 `100215674663`） | 500 | 200 | 700 |
| 华为 nova 14（竞品 `100224796932`） | 500 | 200 | 700 |

两边样本结构对称，所以竞品差距是可比的。全流程产出 `review_aspects` 2314 行。

结果：整体正面率 **77.0%**、负面率 **7.1%**，识别出 **1 个痛点**（客服服务，负面率 33.3%）
和 6 个优势；竞品差距 7 个共同属性，最大短板 **影像拍照 −8.5pp**。

数据来源与竞品变更过程见 [`docs/实现偏差记录.md`](docs/实现偏差记录.md) D9-8 / D11。

### 打包给外部验收人

```bash
bash scripts/run_backend.sh start        # 需要后端在跑（数据从 API 取）
python scripts/build_standalone.py
```

产出 `打包输出/SPIKE验收协助包/` 和一个同名 zip。前端会被压成**单文件 HTML**
（接口数据全部内嵌，路由切成 hash 模式），对方**双击就能看，不需要装
Hadoop / MySQL / Node**。包内还带验收报告、ABSA 抽检报告、可自动算准确率的
人工核对表、验收确认单、原始数据样本和规格说明。

### 采集

```bash
# 模拟数据（下游联调、演示用）
python crawler/jd_review_crawler.py --provider mock

# 真实京东（需要 crawler/.secrets/cookies.txt 里的登录 cookie）
python crawler/jd_review_crawler.py --provider jd --count 500

# 京东限流很凶时，用慢速可续传版本后台慢慢跑
nohup python crawler/jd_slow_crawl.py > logs/crawl.log 2>&1 &
python crawler/jd_slow_crawl.py --status     # 看进度

bash scripts/upload_to_hdfs.sh               # 传到 HDFS
```

**京东采集的现实情况（重要）**：评论接口
`club.jd.com/comment/skuProductPageComments.action` 本身可用、能翻 100 页（1000 条），
但必须带登录 cookie，且限流极严——实测连续 2 页就被封，返回「活动太火爆，请稍微再试」，
静默 90 秒后仍然 8/8 拒绝。所以真实数据只能靠极慢速（≥30 秒/页 + 限流后退避 10 分钟）长期采集。
细节见 [`docs/实现偏差记录.md`](docs/实现偏差记录.md) D2 / D6 / D7 / D9。

## 运行环境（已就绪）

| 组件 | 版本 | 位置 |
|---|---|---|
| JDK | Temurin 11.0.32.1 | `/home/hadoop/jdk-11` |
| Hadoop | 3.3.5（HDFS 伪分布式） | `/usr/local/hadoop` |
| Python（项目） | **3.11.18** | venv `/home/hadoop/spike-venv` |
| PySpark | 3.5.3 | 同上 |
| MySQL | 8.4.11 | 系统服务，**端口 3307** |
| Node.js | 22.22.1 | `/usr/bin/node` |

> **为什么项目用 Python 3.11 而不是系统自带的 3.14？**
> PySpark 3.5.3 官方只支持 Python 3.8–3.11，在 3.14 上 `cloudpickle` 会栈溢出，
> 任何作业（哪怕 `sc.parallelize(range(10)).count()`）都跑不起来。
> 而 Ubuntu 26.04 的系统 Python 是 3.14，`apt`/`sudo`/WSL 集成等 **118 个包都依赖它，不能卸**。
> 所以用 `uv` 装了独立的 CPython 3.11.18（不碰系统），项目 venv 建在它之上；
> 系统 `python3` 仍是 3.14，两者互不影响。
> 旧的 3.14 venv 备份在 `/home/hadoop/spike-venv-py314-backup`（确认无用后可删）。

## 快速开始

在 WSL 里执行（发行版一启动 HDFS 会自动拉起，无需手动 start）：

```bash
cd /mnt/d/workspace/SPIKE
source scripts/spike-env.sh

scripts/check_env.sh            # 静态自检（25 项）
scripts/smoke_test.sh           # 功能性验证（实际读写 HDFS/PySpark/MySQL，9 项）
scripts/spike-hdfs-status.sh    # HDFS 状态

hdfs dfs -ls -R /spike          # 看一眼 HDFS 目录
```

数据库连接：

```
mysql://spike:spike_pass_2026@127.0.0.1:3307/spike
```

## 后端与前端

### 后端（Flask，只读 MySQL）

```bash
bash scripts/run_backend.sh start     # 后台起，默认 5000 端口
bash scripts/run_backend.sh status    # 状态 + /api/health
bash scripts/run_backend.sh test      # 12 项接口自测
bash scripts/run_backend.sh stop
```

| 接口 | 说明 |
|---|---|
| `GET /api/health` | 健康检查，含 6 张表行数 |
| `GET /api/products` | 产品列表 |
| `GET /api/products/<id>/overview` | 健康总览（KPI + 各属性） |
| `GET /api/products/<id>/advantages` | 优势清单 |
| `GET /api/products/<id>/painpoints` | 痛点清单（按优先级降序） |
| `GET /api/products/<id>/aspects` | 全部属性聚合 |
| `GET /api/competitive-gap` | 竞品差距矩阵 + 原始 gap 行 |
| `GET /api/evidence` | 评论证据，支持产品/属性/情感筛选 + 分页 |
| `GET /api/meta/aspects` | 属性字典 |

### 前端（Vue 3 + Vite + ECharts）

```bash
cd frontend
npm install          # 若 /mnt/d 下 chmod 报 EPERM，加 --no-bin-links
node node_modules/vite/bin/vite.js build    # 产出 dist/

# 开发模式（热更新，5173 端口，/api 代理到 5000）
node node_modules/vite/bin/vite.js
```

打包后 `frontend/dist` 会被 Flask 直接托管，所以**生产只需要起后端一个进程**，
访问 `http://127.0.0.1:5000` 即可。

> `/mnt/d` 是 DrvFs，不支持 `chmod`，npm 安装可执行包时会报 `EPERM`。
> 解决：`npm install --no-bin-links`，然后用 `node node_modules/vite/bin/vite.js` 代替 `npx vite`。

## 一键跑通全流程

```bash
cd /mnt/d/workspace/SPIKE
source scripts/spike-env.sh

# ① 等 HDFS 就绪（WSL 启动后自动拉起，safemode OFF 需 25–35 秒）
scripts/spike-hdfs-status.sh

# ② 采集（mock 数据用于联调；真数据用 --provider jd 或 jd_slow_crawl.py）
python crawler/jd_review_crawler.py --provider mock

# ③ 上传 + 计算（4 个 Spark 作业，约 1 分钟）
bash scripts/upload_to_hdfs.sh
bash scripts/run_pipeline.sh

# ④ 起后端（同时托管前端）
bash scripts/run_backend.sh start

# ⑤ 打开 http://127.0.0.1:5000
bash scripts/run_backend.sh test      # 12 项接口自测
```

## 验收测试（规格 §13）

```bash
cd /mnt/d/workspace/SPIKE
source scripts/spike-env.sh
python scripts/acceptance_test.py            # 全跑，约 60 秒
python scripts/acceptance_test.py --fast     # 跳过 T10（重跑作业）
python scripts/acceptance_test.py --only T2,T7
```

最近一次运行：**10/10 通过，62.5 秒**，详细依据见
[`docs/验收报告.md`](docs/验收报告.md)。

| 编号 | 验收项 | 怎么验的 |
|---|---|---|
| T1 | HDFS 伪分布式 | `hdfs dfs -ls /spike/raw` |
| T2 | 采集 ≥500 条/产品 | 从 HDFS 读回 JSONL，数行数 + 校验 10 个字段齐全 |
| T3 | 清洗无空评论/无重复 | PySpark 读 cleaned 层，查空内容、空值、重复 `review_id`、非法星级 |
| T4 | ABSA 抽检 100 条 | `ORDER BY RAND()` 抽样，逐条校验结构；同时生成人工复核报告 |
| T5 | 聚合正负率正确 | 用 `review_aspects` 重新分组计数，与 `aspect_aggregation` 交叉校验 |
| T6 | 痛点降序 + Kano 非空 | 检查 `aspect_priority` 的 Kano 枚举、排序单调性 |
| T7 | 竞品差距 ≥5 属性 | 统计共同属性数，并重算 `pos_gap` / `neg_gap` |
| T8 | API 全部 200 | 复用 `scripts/test_api.py` 的 12 个用例，后端没起会自动拉起 |
| T9 | 前端三视图 + 证据 | headless Chromium 实测切换，并点击「查看用户原话」验证展开 |
| T10 | 幂等 | 快照行数 → 重跑步骤 3/4 → 再快照，并检查业务主键无重复 |

## 两个必须知道的坑

1. **MySQL 在 3307**：Windows 宿主已有 `MySQL80` 占用 3306/33060，而 `.wslconfig` 用的是
   `networkingMode=Mirrored`（WSL 与 Windows 共享网络栈），所以 WSL 内的 MySQL 改用 3307。
   配置在 `/etc/mysql/mysql.conf.d/zz-spike.cnf`。
2. **HDFS 随 WSL 会话存在**：WSL 在最后一个客户端断开约 15 秒后会停掉发行版，HDFS 随之停止；
   下次进入 WSL 时由 cron 看门狗的 `@reboot` 自动拉起。**完全健康（safemode OFF）需等 25—35 秒**，
   这期间 `dfsadmin` 会报 `Connection refused` 或 safemode ON，属正常暂态。

细节见 [`docs/环境搭建说明.md`](docs/环境搭建说明.md)。
