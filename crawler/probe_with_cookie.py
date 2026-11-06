#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用用户提供的 cookie 注入 Playwright，测试评论接口是否恢复。

cookie 文件: crawler/.secrets/cookies.txt
  内容为纯文本 "key=value; key2=value2; ..."（F12 -> Network -> Request Headers -> Cookie）
  https://item.jd.com/xxx.html  页面里的 Cookie 整段即可。
"""

import asyncio
import os
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path("/mnt/d/workspace/SPIKE")
COOKIE_FILE = PROJECT_ROOT / "crawler" / ".secrets" / "cookies.txt"
SKU = "100215674663"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

hits = []


def parse_cookies(raw: str) -> list[dict]:
    """把 Cookie 头字符串解析成 Playwright 的 cookie 列表。"""
    raw = raw.strip()
    # 容错：用户可能把 "Cookie:" 前缀也带进来了，或整段是 cURL
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1]
    raw = raw.replace("\n", " ").strip()
    raw = raw.strip("'\"")

    out = []
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        out.append({
            "name": name,
            "value": value,
            "domain": ".jd.com",
            "path": "/",
            # 京东 cookie 基本都是长期有效的，个别是 session，给个宽松过期时间
            "expires": -1,
            "httpOnly": False,
            "secure": True,
        })
    return out


async def main():
    if not COOKIE_FILE.exists():
        print(f"[FAIL] 找不到 {COOKIE_FILE}", file=sys.stderr)
        return 1

    raw = COOKIE_FILE.read_text(encoding="utf-8", errors="ignore")
    # 允许用户把整段 "Copy as cURL" 粘进来：自动从中提取 -H 'Cookie: ...'
    m = re.search(r"-H\s+'Cookie:\s*([^']+)'", raw) or re.search(r'-H\s+"Cookie:\s*([^"]+)"', raw)
    if m:
        raw = m.group(1)
        print("  （检测到 cURL 格式，已自动提取 Cookie 头）")

    cookies = parse_cookies(raw)
    if not cookies:
        print("[FAIL] 没解析出任何 cookie，文件内容可能不对", file=sys.stderr)
        print(f"  文件大小: {len(raw)} 字节", file=sys.stderr)
        return 1

    print(f"解析到 {len(cookies)} 个 cookie: {[c['name'] for c in cookies][:12]}")
    key_names = {"thor", "pin", "pt_key", "pt_pin", "wskey"}
    have = key_names & {c["name"] for c in cookies}
    print(f"  关键登录态 cookie: {sorted(have) if have else '⚠️ 一个都没有，可能没登录'}")

    async with async_playwright() as p:
        headless = os.environ.get("SPIKE_HEADLESS", "1") == "1"
        print(f"  浏览器模式: {'无头' if headless else '有头（Xvfb）'}")
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await browser.new_context(
            user_agent=UA, viewport={"width": 390, "height": 844},
            is_mobile=True, has_touch=True, locale="zh-CN", timezone_id="Asia/Shanghai",
        )
        # 抹掉最常见的自动化特征
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = {runtime: {}};
        """)
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        async def on_resp(r):
            u = r.url
            if "comment" not in u.lower():
                return
            if "api.m.jd.com" not in u and "club.jd.com" not in u:
                return
            try:
                body = await r.text()
            except Exception:
                body = "<读取失败>"
            hits.append(body)
            print(f"\n[接口] {r.status}  {len(body)} 字节")
            print(f"  BODY: {body[:400]}")

        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        print(f"\n加载 {SKU} ...")
        await page.goto(f"https://item.m.jd.com/product/{SKU}.html",
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        for _ in range(8):
            await page.evaluate("window.scrollBy(0, 900)")
            await page.wait_for_timeout(800)

        # 判断是否被踢到登录页
        if "plogin" in page.url or "/login" in page.url:
            print(f"\n  ⚠️ 被重定向到登录页: {page.url[:100]}")
            print("     -> cookie 无效或已过期")

        try:
            await page.locator(".cmt_more_lnk").first.click(timeout=6000)
            print("  已点击「查看全部评价」")
        except Exception as e:
            print(f"  点击失败: {type(e).__name__}")
        await page.wait_for_timeout(7000)

        txt = await page.evaluate("document.body.innerText")
        m2 = re.search(r"(\d[\d,\.]*)\s*[万千]?\+?\s*条评价", txt)
        print(f"\n  当前 URL: {page.url[:110]}")
        print(f"  评价计数: {m2.group(0) if m2 else '未找到'}")
        print(f"  含「暂无评价」: {'是' if '暂无评价' in txt else '否'}")

        print("\n=== 结论 ===")
        blob = " ".join(hits)
        if '"code":601' in blob or '"code": 601' in blob:
            print("  ❌ 仍是 601 风控")
        elif '"commentData"' in blob or '"content"' in blob:
            print("  ✅ 评论数据通了！可以开始写正式采集器")
        elif not hits:
            print("  ⚠️ 没触发评论接口")
        else:
            print("  ？见上方 BODY")

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
