#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 headless Chromium 打开三个页面，做基础断言并刷新截图（规格 §15.5）。

    python scripts/screenshot_ui.py
    python scripts/screenshot_ui.py --base http://127.0.0.1:5000

截图输出到 docs/screenshots/。所有页面无 JS 报错时退出码 0。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "docs" / "screenshots"

PAGES = [
    ("/dashboard", "01-dashboard"),
    ("/insights", "02-insights"),
    ("/gap", "03-gap"),
]


async def run(base: str) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        print(f"[FAIL] 需要 playwright: {e}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900}, device_scale_factor=2
        )
        page = await ctx.new_page()
        page.on(
            "console",
            lambda m: errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error"
            else None,
        )
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        for path, name in PAGES:
            await page.goto(base + path, wait_until="networkidle")
            await page.wait_for_timeout(1500)

            h1 = ""
            if await page.query_selector("h1"):
                h1 = (await page.inner_text("h1")).strip()
            kpis = await page.eval_on_selector_all(
                ".kpi", "els => els.map(e => e.innerText.replace(/\\n/g, ' | '))"
            )
            rows = await page.eval_on_selector_all("table.data tbody tr", "els => els.length")
            canvases = await page.eval_on_selector_all("canvas", "els => els.length")
            empties = await page.eval_on_selector_all(
                ".empty", "els => els.map(e => e.innerText.trim())"
            )

            print(f"--- {path}")
            print(f"    标题: {h1}")
            print(f"    KPI({len(kpis)})")
            for k in kpis:
                print(f"      {k}")
            print(f"    表格行数: {rows}   canvas 图表: {canvases}")
            if empties:
                print(f"    空状态: {empties}")
            if await page.query_selector(".error-box"):
                errors.append(
                    f"{path} 显示错误框: {(await page.inner_text('.error-box'))[:120]}"
                )

            await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print(f"    截图: docs/screenshots/{name}.png")

        await browser.close()

    print()
    if errors:
        print(f"[FAIL] 页面报错 {len(errors)} 条:")
        for e in errors[:20]:
            print("   ", e)
        return 1
    print("[ OK ] 三个页面均无 JS 报错，截图已刷新")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="前端三视图截图与断言")
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    return asyncio.run(run(ap.parse_args().base))


if __name__ == "__main__":
    sys.exit(main())
