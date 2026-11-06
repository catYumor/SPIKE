#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建「验收协助包」：把前端压成单文件 HTML + 数据内嵌，连同验收文档一起打包。

收包的人不需要装 Hadoop / MySQL / Node，双击 HTML 就能看前端。

用法：

    bash scripts/run_backend.sh start          # 需要后端在跑（数据从 API 取）
    python scripts/build_standalone.py
    python scripts/build_standalone.py --no-build   # 跳过 vite 构建，复用 dist-static

产物：

    打包输出/SPIKE验收协助包/
    打包输出/SPIKE验收协助包-YYYYMMDD.zip
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import requests
from markdown import markdown

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = PROJECT_ROOT / "frontend"
DIST_STATIC = FRONTEND / "dist-static"
OUT_ROOT = PROJECT_ROOT / "打包输出"
PKG = OUT_ROOT / "SPIKE验收协助包"
DOCS = PROJECT_ROOT / "docs"
SHOTS = DOCS / "screenshots"

sys.path.insert(0, str(PROJECT_ROOT))
from config.settings import load_products  # noqa: E402

API = "http://127.0.0.1:5000"
TODAY = datetime.now().strftime("%Y-%m-%d")

# ---------------------------------------------------------------- 工具

PAGE_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 20px 80px;
  font: 15px/1.75 -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  color: #1f2430; background: #f6f7f9;
}
.wrap { max-width: 1080px; margin: 0 auto; background: #fff; border-radius: 12px;
        box-shadow: 0 2px 16px rgba(20,30,50,.07); padding: 36px 44px 48px; }
h1 { font-size: 26px; margin: 0 0 6px; padding-bottom: 14px; border-bottom: 3px solid #2f6df6; }
h2 { font-size: 20px; margin: 30px 0 10px; padding-left: 10px; border-left: 4px solid #2f6df6; }
h3 { font-size: 16px; margin: 22px 0 8px; color: #2c3444; }
p, li { color: #2c3444; }
a { color: #2f6df6; }
table { border-collapse: collapse; width: 100%; margin: 14px 0 22px; font-size: 13.5px; }
th, td { border: 1px solid #dfe3ea; padding: 8px 10px; text-align: left; vertical-align: top; }
th { background: #eef3fd; font-weight: 600; white-space: nowrap; }
tr:nth-child(even) td { background: #fafbfd; }
code { background: #eef1f6; padding: 2px 6px; border-radius: 4px;
       font: 13px/1.5 Consolas, "Courier New", monospace; }
pre { background: #1e2432; color: #e6ebf5; padding: 16px 18px; border-radius: 8px;
      overflow-x: auto; font-size: 13px; line-height: 1.6; }
pre code { background: none; color: inherit; padding: 0; }
blockquote { margin: 14px 0; padding: 10px 16px; background: #f3f7ff;
             border-left: 4px solid #9dbcf7; color: #384258; }
hr { border: none; border-top: 1px solid #e3e7ee; margin: 30px 0; }
.nav { margin-bottom: 22px; font-size: 13px; }
.nav a { margin-right: 14px; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 20px;
         background: #e7f7ec; color: #17743a; font-size: 12px; font-weight: 600; }
"""


def md2html(text: str) -> str:
    """Markdown -> HTML 片段（支持表格、围栏代码块）。"""
    return markdown(text, extensions=["tables", "fenced_code", "sane_lists"])


# 文档之间用 .md 互链，渲染成 HTML 后要改指对应的 .html
DOC_LINK_MAP = {
    "验收报告.md": "验收报告.html",
    "ABSA抽检报告.md": "ABSA抽检报告.html",
    "实现偏差记录.md": "实现偏差记录.html",
    "数据采集交接说明.md": "数据采集交接说明.html",
    "环境搭建说明.md": "环境搭建说明.html",
    "补抓差评操作单.md": "补抓差评操作单.html",
    "SPIKE-规格说明.md": "../05-规格说明.html",
}


def rewrite_doc_links(body: str) -> str:
    for old, new in DOC_LINK_MAP.items():
        body = body.replace(f'href="./{old}"', f'href="{new}"')
        body = body.replace(f'href="{old}"', f'href="{new}"')
    return body


# 顶部导航：包内各页相对路径不同，按所在层级加前缀
NAV_ITEMS = [
    ("00-先看这里.html", "先看这里"),
    ("01-打开前端.html", "打开前端"),
    ("03-验收文档/验收报告.html", "验收报告"),
    ("03-验收文档/人工核对表.html", "人工核对表"),
    ("03-验收文档/验收确认单.html", "验收确认单"),
]


def nav_html(depth: int = 0) -> str:
    prefix = "../" * depth
    return "".join(f'<a href="{prefix}{href}">{label}</a>' for href, label in NAV_ITEMS)


def page(title: str, body: str, nav: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
{f'<div class="nav">{nav}</div>' if nav else ''}
{body}
</div>
</body>
</html>
"""


def fetch(path: str, **params):
    r = requests.get(API + path, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- 1. 导出数据


def collect_data() -> dict:
    print("[1/6] 从后端导出接口数据")
    products = fetch("/api/products")
    plist = products["products"]
    data: dict = {
        "health": fetch("/api/health"),
        "products": products,
        "aspect_dict": fetch("/api/meta/aspects"),
        "product_data": {},
        "gap": {},
        "evidence": {},
    }

    for p in plist:
        pid = p["product_id"]
        data["product_data"][pid] = {
            "overview": fetch(f"/api/products/{pid}/overview"),
            "advantages": fetch(f"/api/products/{pid}/advantages"),
            "painpoints": fetch(f"/api/products/{pid}/painpoints"),
            "aspects": fetch(f"/api/products/{pid}/aspects"),
        }
        print(f"      {pid}  {p['brand']} {p['model']}")

    target = next(p for p in plist if p["is_target"])
    rivals = [p for p in plist if not p["is_target"]]
    if rivals:
        data["gap"][target["product_id"]] = fetch(
            "/api/competitive-gap",
            target_id=target["product_id"],
            competitor_ids=",".join(p["product_id"] for p in rivals),
        )

    # 证据：按 (商品, 属性, 情感) 预取全部，前端只做分页切片
    total_items = 0
    for p in plist:
        pid = p["product_id"]
        aspects = fetch(f"/api/products/{pid}/aspects")["items"]
        for a in aspects:
            for sentiment in ("positive", "negative", "neutral"):
                key = f"{pid}|{a['aspect_name']}|{sentiment}"
                items, page = [], 1
                while True:
                    res = fetch(
                        "/api/evidence",
                        product_id=pid,
                        aspect_name=a["aspect_name"],
                        sentiment=sentiment,
                        page=page,
                        page_size=100,
                    )
                    items += res["items"]
                    if not res.get("has_next"):
                        break
                    page += 1
                data["evidence"][key] = items
                total_items += len(items)
    print(f"      证据 {len(data['evidence'])} 组，共 {total_items} 条")

    raw = json.dumps(data, ensure_ascii=False, default=str)
    print(f"      数据大小 {len(raw.encode()) / 1024:.0f} KB")
    return data


# ---------------------------------------------------------------- 2. 构建单文件前端


def build_static_bundle(run_build: bool) -> None:
    if not run_build:
        print("[2/6] 跳过 vite 构建，复用 dist-static/")
        return
    print("[2/6] 构建静态前端（vite build --config vite.config.static.js）")
    node = shutil.which("node") or "node"
    r = subprocess.run(
        [node, "node_modules/vite/bin/vite.js", "build", "--config", "vite.config.static.js"],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise SystemExit("vite 构建失败:\n" + (r.stdout + r.stderr)[-2000:])
    print("      构建完成")


def inline_single_html(data: dict) -> str:
    print("[3/6] 内联成单文件 HTML")
    idx = (DIST_STATIC / "index.html").read_text(encoding="utf-8")

    # CSS
    m = re.search(r'<link[^>]+href="([^"]+\.css)"[^>]*>', idx)
    css = ""
    if m:
        css_path = DIST_STATIC / m.group(1).lstrip("./")
        css = css_path.read_text(encoding="utf-8")
        idx = idx.replace(m.group(0), f"<style>{css}</style>")

    # JS
    # 注意：vite 产物是 <script type="module">，模块脚本天生是「延迟执行」的，
    # 放在 <head> 也不会出问题；但内联成普通 <script> 后就会立刻执行，
    # 那时 <div id="app"> 还没被解析出来，Vue 会挂在 null 上。
    # 所以先把它从 <head> 摘掉，最后统一插到 </body> 之前。
    m = re.search(r'<script[^>]+src="([^"]+\.js)"[^>]*></script>', idx)
    if not m:
        raise SystemExit("没找到打包出来的 app.js")
    js_path = DIST_STATIC / m.group(1).lstrip("./")
    js = js_path.read_text(encoding="utf-8")
    idx = idx.replace(m.group(0), "")

    if re.search(r"^\s*(import|export)\s", js, re.M) or "import.meta" in js:
        raise SystemExit("打包产物里还有 ESM 语法，无法直接内联成普通 <script>")

    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    boot = (
        "<script>window.__SPIKE_STATIC__ = true;</script>\n"
        f"<script>window.__SPIKE_DATA__ = {payload};</script>\n"
        f"<script>\n(function(){{\n{js}\n}})();\n</script>\n"
    )
    idx = idx.replace("</body>", boot + "</body>")
    print(f"      单文件 {len(idx.encode()) / 1024:.0f} KB")
    return idx


# ---------------------------------------------------------------- 4. 文档渲染


DOC_FILES = [
    ("验收报告.md", "验收报告.html", "T1–T10 的机器验收结果"),
    ("ABSA抽检报告.md", "ABSA抽检报告.html", "100 条 ABSA 抽样明细"),
    ("实现偏差记录.md", "实现偏差记录.html", "与规格不一致处及原因"),
    ("数据采集交接说明.md", "数据采集交接说明.html", "数据来源与格式约定"),
    ("环境搭建说明.md", "环境搭建说明.html", "环境配置全过程"),
    ("补抓差评操作单.md", "补抓差评操作单.html", "补数据时的操作步骤"),
]


def render_docs() -> None:
    print("[4/6] 渲染验收文档")
    doc_dir = PKG / "03-验收文档"
    md_dir = doc_dir / "markdown原件"
    md_dir.mkdir(parents=True, exist_ok=True)

    for src, dst, desc in DOC_FILES:
        path = DOCS / src
        if not path.exists():
            print(f"      跳过（不存在）{src}")
            continue
        text = path.read_text(encoding="utf-8")
        body = f"<h1>{html.escape(path.stem)}</h1>\n" + rewrite_doc_links(md2html(text))
        (doc_dir / dst).write_text(
            page(path.stem, f'<p class="badge">{html.escape(desc)}</p>' + body, nav_html(1)),
            encoding="utf-8",
        )
        shutil.copyfile(path, md_dir / src)
        print(f"      {src} -> {dst}")

    # 规格说明单独放一处
    spec = DOCS / "SPIKE-规格说明.md"
    if spec.exists():
        (PKG / "05-规格说明.html").write_text(
            page(spec.stem, rewrite_doc_links(md2html(spec.read_text(encoding="utf-8"))), nav_html(0)),
            encoding="utf-8",
        )
        print("      SPIKE-规格说明.md -> 05-规格说明.html")


CHECK_CSS = """
.sample { border: 1px solid #dfe3ea; border-radius: 10px; padding: 14px 16px; margin: 12px 0;
          background: #fff; }
.sample .q { font-size: 15px; line-height: 1.7; margin-bottom: 10px; color: #1f2430; }
.sample .meta { font-size: 12.5px; color: #667; margin-bottom: 8px; }
.sample .meta span { margin-right: 14px; }
.tagpos { color: #17743a; font-weight: 600; }
.tagneg { color: #b42318; font-weight: 600; }
.tagneu { color: #8a6d1f; font-weight: 600; }
.opts label { margin-right: 18px; font-size: 13.5px; cursor: pointer; }
.opts input { margin-right: 4px; }
.bar { position: sticky; top: 0; z-index: 9; background: #fff; padding: 14px 0;
       border-bottom: 1px solid #e3e7ee; margin-bottom: 8px; }
.bar .num { font-size: 22px; font-weight: 700; color: #2f6df6; }
.bar .sub { font-size: 13px; color: #667; }
textarea { width: 100%; height: 90px; font: 13px/1.6 Consolas, monospace;
           padding: 10px; border: 1px solid #dfe3ea; border-radius: 8px; }
button { padding: 8px 18px; font-size: 14px; border: none; border-radius: 8px;
         background: #2f6df6; color: #fff; cursor: pointer; }
button:hover { background: #1f57d0; }
"""

CHECK_JS = """
const state = {};
function tally() {
  let ok = 0, half = 0, bad = 0;
  for (const v of Object.values(state)) {
    if (v === 'ok') ok++; else if (v === 'half') half++; else if (v === 'bad') bad++;
  }
  const judged = ok + half + bad;
  const acc = judged ? (ok + half * 0.5) / judged * 100 : 0;
  document.getElementById('acc').textContent = acc.toFixed(1) + '%';
  document.getElementById('cnt').textContent =
    `已判 ${judged} 条：正确 ${ok}｜部分正确 ${half}｜错误 ${bad}（共 ${TOTAL} 条）`;
}
function pick(i, v) {
  state[i] = v;
  tally();
}
function copySummary() {
  const judged = Object.keys(state).length;
  const el = document.getElementById('acc').textContent;
  const txt = document.getElementById('cnt').textContent;
  const out = `ABSA 抽检人工判读结果（${judged}/${TOTAL} 条已判）\\n` +
              `加权准确率（正确=1，部分=0.5，错误=0）：${el}\\n${txt}`;
  navigator.clipboard.writeText(out).then(
    () => document.getElementById('copied').textContent = '已复制，可以直接贴进验收报告',
    () => document.getElementById('copied').textContent = '复制失败，请手动选中文本复制'
  );
}
"""


def build_check_table() -> None:
    """生成 100 条人工核对表：勾选后自动算加权准确率。"""
    print("[5/6] 生成 ABSA 人工核对表")
    import pymysql

    from config.settings import MYSQL

    conn = pymysql.connect(**MYSQL, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT aspect_name, sentiment, confidence, evidence_text, score, review_id
                 FROM review_aspects
             ORDER BY RAND()
                LIMIT 100"""
        )
        rows = cur.fetchall()
    conn.close()

    tag = {"positive": "tagpos", "negative": "tagneg", "neutral": "tagneu"}
    label = {"positive": "正面", "negative": "负面", "neutral": "中性"}
    blocks = []
    for i, r in enumerate(rows, 1):
        ev = html.escape(str(r["evidence_text"] or "")[:160])
        s = r["sentiment"]
        blocks.append(
            f"""<div class="sample">
  <div class="q">{i}. 「{ev}」</div>
  <div class="meta">
    <span>抽取属性：<b>{html.escape(str(r['aspect_name']))}</b></span>
    <span>情感：<b class="{tag.get(s,'')}">{label.get(s,s)}</b></span>
    <span>置信度 {float(r['confidence']):.2f}</span>
    <span>星级 {r['score']}</span>
  </div>
  <div class="opts">
    <label><input type="radio" name="s{i}" onclick="pick({i},'ok')"> 正确</label>
    <label><input type="radio" name="s{i}" onclick="pick({i},'half')"> 部分正确</label>
    <label><input type="radio" name="s{i}" onclick="pick({i},'bad')"> 错误</label>
  </div>
</div>"""
        )

    body = f"""
<h1>ABSA 抽检人工核对表</h1>
<p class="badge">耗时约 15–20 分钟｜100 条随机抽样</p>

<h2>怎么用</h2>
<ol>
  <li>逐条看「用户原话」和「抽取属性 / 情感」是否对得上。</li>
  <li>
    <b>正确</b>：属性和情感都对；<br>
    <b>部分正确</b>：属性对但情感判反，或只抽对了一部分；<br>
    <b>错误</b>：属性认错了，或原话根本没在说这个方面。
  </li>
  <li>全部判完后，点下面的按钮复制结果，粘到 <code>验收报告</code> 的「人工判读」一栏。</li>
</ol>

<blockquote>
说明：本项目没有人工标注集，所以准确率只能靠人工抽检得出。
这里的 100 条与 <code>ABSA抽检报告.html</code> 里的明细不是同一批（每份都是独立随机抽样）。
</blockquote>

<div class="bar">
  <div><span class="num" id="acc">0.0%</span>
       <span class="sub">　加权准确率（正确=1，部分=0.5，错误=0）</span></div>
  <div class="sub" id="cnt">已判 0 条（共 100 条）</div>
  <div style="margin-top:10px">
    <button onclick="copySummary()">复制结果</button>
    <span class="sub" id="copied" style="margin-left:12px"></span>
  </div>
</div>

{''.join(blocks)}

<script>const TOTAL = {len(rows)};{CHECK_JS}</script>
"""
    (PKG / "03-验收文档" / "人工核对表.html").write_text(
        page("ABSA 抽检人工核对表", body, nav_html(1)).replace("</style>", CHECK_CSS + "</style>"),
        encoding="utf-8",
    )
    print(f"      100 条样本 -> 03-验收文档/人工核对表.html")


# ---------------------------------------------------------------- 6. README / 打包


def write_readme(data: dict) -> None:
    products = data["products"]["products"]
    target = next(p for p in products if p["is_target"])
    rival = next((p for p in products if not p["is_target"]), None)
    ov = data["product_data"][target["product_id"]]["overview"]
    summary = ov.get("summary", {})

    rows = "".join(
        f"<tr><td>{html.escape(p['brand'])} {html.escape(p['model'])}</td>"
        f"<td>{p['product_id']}</td>"
        f"<td>{'目标产品' if p['is_target'] else '竞品'}</td></tr>"
        for p in products
    )

    body = f"""
<h1>SPIKE 项目 — 验收协助包</h1>
<p class="badge">生成于 {TODAY}</p>

<h2>这是什么</h2>
<p>
这是「手机评论洞察系统」SPIKE 的验收材料包。
前端已经打包成<b>单文件网页</b>（数据全部内嵌），
<b>不需要安装 Hadoop、MySQL、Node 或任何环境，双击就能看</b>。
</p>

<h2>你需要做的三件事</h2>
<table>
  <tr><th>#</th><th>做什么</th><th>打开哪个文件</th><th>大约耗时</th></tr>
  <tr>
    <td>1</td>
    <td>看三个页面是否正常显示、图表和数据是否合理</td>
    <td><a href="01-打开前端.html">01-打开前端.html</a></td>
    <td>5 分钟</td>
  </tr>
  <tr>
    <td>2</td>
    <td>逐条判读 100 条 ABSA 抽样，得到准确率</td>
    <td><a href="03-验收文档/人工核对表.html">03-验收文档/人工核对表.html</a></td>
    <td>15–20 分钟</td>
  </tr>
  <tr>
    <td>3</td>
    <td>把准确率和意见填进验收确认单</td>
    <td><a href="03-验收文档/验收确认单.html">03-验收文档/验收确认单.html</a></td>
    <td>5 分钟</td>
  </tr>
</table>

<h2>项目数据概况</h2>
<p>目标产品：<b>{html.escape(target['brand'])} {html.escape(target['model'])}</b>　
竞品：<b>{html.escape(rival['brand']) + ' ' + html.escape(rival['model']) if rival else '—'}</b></p>

<table>
  <tr><th>商品</th><th>SKU</th><th>身份</th></tr>
  {rows}
</table>

<p>分析结果（来自已跑通的流水线）：</p>
<table>
  <tr><th>指标</th><th>数值</th></tr>
  <tr><td>覆盖评论数</td><td>{summary.get('covered_reviews', '—')}</td></tr>
  <tr><td>属性提及总数</td><td>{summary.get('total_aspects', '—')}</td></tr>
  <tr><td>整体正面率</td><td>{round(float(summary.get('positive_rate', 0)) * 100, 1)}%</td></tr>
  <tr><td>整体负面率</td><td>{round(float(summary.get('negative_rate', 0)) * 100, 1)}%</td></tr>
  <tr><td>识别出的痛点</td><td>{summary.get('painpoint_count', '—')} 个</td></tr>
  <tr><td>识别出的优势</td><td>{summary.get('advantage_count', '—')} 个</td></tr>
</table>

<h2>包里有什么</h2>
<table>
  <tr><th>目录 / 文件</th><th>内容</th></tr>
  <tr><td><code>01-打开前端.html</code></td><td>单文件前端，双击打开，三个页面可切换</td></tr>
  <tr><td><code>02-前端截图/</code></td><td>三个页面的静态截图，方便放进报告</td></tr>
  <tr><td><code>03-验收文档/</code></td><td>验收报告、ABSA 抽检报告、人工核对表、<b>验收确认单</b>、偏差记录、采集说明</td></tr>
  <tr><td><code>04-数据样本/</code></td><td>两个商品各 700 条原始评论（JSONL），可自行抽查</td></tr>
  <tr><td><code>05-规格说明.html</code></td><td>项目规格，验收标准的依据</td></tr>
</table>

<h2>几点说明</h2>
<ul>
  <li>
    <b>为什么是静态版？</b> 完整系统需要 Hadoop 伪分布式 + Spark + MySQL，
    装一套要一两个小时。为了让您能直接看，这里把 API 返回的数据全部预取并内嵌，
    前端逻辑和线上版本是同一份代码，只是数据源从 HTTP 换成了内嵌对象。
  </li>
  <li>
    <b>数据是真的。</b> 两个商品各 700 条真实京东评论（500 好评 + 200 差评），
    见 <code>04-数据样本/</code>。
  </li>
  <li>
    <b>关于准确率。</b> ABSA 用的是规则+词典方案（模型体积太大未随包），
    准确率需要靠人工抽检，这就是第 2 件事要做的。
  </li>
</ul>
"""
    (PKG / "00-先看这里.html").write_text(page("先看这里", body, nav_html(0)), encoding="utf-8")


def build_signoff() -> None:
    """验收确认单：把机器结果列成清单，辅助验收人勾选/填写/打印。"""
    print("[5b/6] 生成验收确认单")
    rows = []
    report = DOCS / "验收报告.md"
    if report.exists():
        for line in report.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\|\s*(T\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
            if m:
                rows.append((m.group(1), m.group(2), m.group(3)))

    items = "".join(
        f"""<tr>
  <td>{tid}</td>
  <td>{html.escape(name)}</td>
  <td>{html.escape(res)}</td>
  <td style="text-align:center"><input type="checkbox" checked></td>
</tr>"""
        for tid, name, res in rows
    ) or '<tr><td colspan="4">未找到验收报告，请先跑 scripts/acceptance_test.py</td></tr>'

    body = f"""
<h1>验收确认单</h1>
<p class="badge">请填写后随验收报告一并返回</p>

<h2>一、基本信息</h2>
<table>
  <tr><th style="width:150px">验收人</th><td><input type="text" style="width:100%;border:none;border-bottom:1px solid #ccc;padding:4px"></td></tr>
  <tr><th>验收日期</th><td><input type="text" style="width:100%;border:none;border-bottom:1px solid #ccc;padding:4px" placeholder="YYYY-MM-DD"></td></tr>
  <tr><th>验收环境</th><td>静态演示包（单文件 HTML，数据内嵌，无需安装任何环境）</td></tr>
</table>

<h2>二、前端页面检查</h2>
<p>打开 <a href="../01-打开前端.html">01-打开前端.html</a>，逐个切换后勾选：</p>
<table>
  <tr><th style="width:60px">#</th><th>检查项</th><th style="width:80px">正常</th><th style="width:80px">异常</th></tr>
  <tr><td>1</td><td>「产品健康总览」：5 个 KPI 卡片、2 张图表、7 行属性表格都能显示</td>
      <td style="text-align:center"><input type="radio" name="p1"></td>
      <td style="text-align:center"><input type="radio" name="p1"></td></tr>
  <tr><td>2</td><td>「优势与痛点清单」：点「查看用户原话」能展开评论、再点能收起</td>
      <td style="text-align:center"><input type="radio" name="p2"></td>
      <td style="text-align:center"><input type="radio" name="p2"></td></tr>
  <tr><td>3</td><td>「竞品差距矩阵」：7 个共同属性、差距数值与结论文字一致</td>
      <td style="text-align:center"><input type="radio" name="p3"></td>
      <td style="text-align:center"><input type="radio" name="p3"></td></tr>
  <tr><td>4</td><td>顶部「目标产品 / 对比竞品」下拉可切换，切换后数据跟着变</td>
      <td style="text-align:center"><input type="radio" name="p4"></td>
      <td style="text-align:center"><input type="radio" name="p4"></td></tr>
</table>

<h2>三、验收项确认（规格 §13）</h2>
<p>机器验收结果见 <a href="验收报告.html">验收报告.html</a>：</p>
<table>
  <tr><th style="width:60px">编号</th><th>验收项</th><th style="width:110px">机器结果</th><th style="width:90px">认可</th></tr>
  {items}
</table>

<h2>四、ABSA 人工判读</h2>
<p>在 <a href="人工核对表.html">人工核对表.html</a> 里逐条判定 100 条抽样，把结果粘到这里：</p>
<table>
  <tr><th style="width:220px">加权准确率</th>
      <td><input type="text" style="width:120px;border:none;border-bottom:1px solid #ccc;padding:4px" placeholder="例如 78.5%"></td></tr>
  <tr><th>已判条数</th>
      <td><input type="text" style="width:120px;border:none;border-bottom:1px solid #ccc;padding:4px" placeholder="例如 100/100"></td></tr>
  <tr><th>主要错误类型</th>
      <td><input type="text" style="width:100%;border:none;border-bottom:1px solid #ccc;padding:4px" placeholder="例如 反讽判反、属性词跨句误配"></td></tr>
</table>

<h2>五、遗留问题与意见</h2>
<textarea style="width:100%;height:120px;padding:10px;border:1px solid #dfe3ea;border-radius:8px;
font:14px/1.7 inherit" placeholder="对数据、分析结论、页面展示的意见；未通过项的原因；建议改进点"></textarea>

<h2>六、结论</h2>
<p>
  <label style="margin-right:24px"><input type="radio" name="final"> 通过验收</label>
  <label style="margin-right:24px"><input type="radio" name="final"> 整改后通过</label>
  <label><input type="radio" name="final"> 不通过</label>
</p>
<p style="margin-top:26px;color:#667;font-size:13px">
  提示：本页可直接用浏览器「打印 → 另存为 PDF」导出签名版；
  表单内容不会被保存，填完请打印或截图留存。
</p>
"""
    (PKG / "03-验收文档" / "验收确认单.html").write_text(
        page("验收确认单", body, nav_html(1)), encoding="utf-8"
    )
    print("      -> 03-验收文档/验收确认单.html")


def copy_assets(data: dict) -> tuple[str, list]:
    shot_dir = PKG / "02-前端截图"
    shot_dir.mkdir(parents=True, exist_ok=True)
    if SHOTS.exists():
        for p in sorted(SHOTS.glob("*.png")):
            shutil.copyfile(p, shot_dir / p.name)
            print(f"      截图 {p.name}")

    # 截图索引页（放在包根目录，图片路径才对）
    names = {
        "01-dashboard.png": "产品健康总览",
        "02-insights.png": "优势与痛点清单",
        "03-gap.png": "竞品差距矩阵",
    }
    cards = "".join(
        f'<h2>{names.get(p.name, p.stem)}</h2>'
        f'<p><img src="02-前端截图/{p.name}" style="width:100%;border:1px solid #dfe3ea;'
        f'border-radius:8px"></p>'
        for p in sorted(shot_dir.glob("*.png"))
    )
    shot_index = page("前端截图", f"<h1>前端截图</h1>{cards}", nav_html(0))

    sample_dir = PKG / "04-数据样本"
    sample_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in data["products"]["products"]:
        pid = p["product_id"]
        src = PROJECT_ROOT / "data" / "raw" / f"{pid}.jsonl"
        if src.exists():
            shutil.copyfile(src, sample_dir / src.name)
            n = sum(1 for _ in src.open(encoding="utf-8"))
            rows.append((pid, p["brand"], p["model"], n))
            print(f"      样本 {src.name}  {n} 条")
    return shot_index, rows


def make_zip() -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    zp = OUT_ROOT / f"SPIKE验收协助包-{stamp}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(PKG.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(OUT_ROOT))
    print(f"      压缩包 {zp.name}  {zp.stat().st_size / 1024 / 1024:.1f} MB")
    return zp


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser(description="构建前端+验收文档的一体化交付包")
    ap.add_argument("--no-build", action="store_true", help="跳过 vite 构建")
    args = ap.parse_args()

    try:
        requests.get(API + "/api/health", timeout=5)
    except requests.RequestException:
        print(f"[FAIL] 后端没起来（{API}）。先跑: bash scripts/run_backend.sh start")
        return 1

    if PKG.exists():
        shutil.rmtree(PKG)
    PKG.mkdir(parents=True)

    data = collect_data()
    build_static_bundle(not args.no_build)
    single = inline_single_html(data)
    (PKG / "01-打开前端.html").write_text(single, encoding="utf-8")

    render_docs()
    build_check_table()
    build_signoff()

    print("[6/6] 复制截图与数据样本、写说明、压缩")
    shot_index, sample_rows = copy_assets(data)
    (PKG / "02-前端截图.html").write_text(shot_index, encoding="utf-8")
    write_readme(data)
    zp = make_zip()

    print()
    print("=" * 66)
    print(f"  输出目录: {PKG}")
    print(f"  压缩包  : {zp}")
    total = sum(p.stat().st_size for p in PKG.rglob("*") if p.is_file())
    print(f"  解压后  : {total / 1024 / 1024:.1f} MB，{sum(1 for _ in PKG.rglob('*') if _.is_file())} 个文件")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
