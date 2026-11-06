#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后端接口自测（规格 §10 / §13）。

起好服务后运行：
  python scripts/test_api.py                  # 默认 http://127.0.0.1:5000
  python scripts/test_api.py --base http://127.0.0.1:8000

会逐个打 9 个接口，检查状态码和返回结构，最后给通过率。
"""

from __future__ import annotations

import argparse
import json
import sys

import requests

TARGET = "100215674663"
COMPETITOR = "100300314335"

CASES = [
    # (名称, 路径, 期望状态码, 必须存在的键)
    ("健康检查", "/api/health", 200, ["status", "mysql", "tables"]),
    ("产品列表", "/api/products", 200, ["products", "count"]),
    ("健康总览", f"/api/products/{TARGET}/overview", 200, ["product", "summary", "aspects"]),
    ("优势清单", f"/api/products/{TARGET}/advantages", 200, ["product_id", "items", "count"]),
    ("痛点清单", f"/api/products/{TARGET}/painpoints", 200, ["product_id", "items", "count"]),
    ("属性聚合", f"/api/products/{TARGET}/aspects", 200, ["product_id", "items", "count"]),
    ("竞品差距", f"/api/competitive-gap?target_id={TARGET}", 200,
     ["target", "competitors", "matrix", "gaps", "common_aspect_count"]),
    ("证据分页", f"/api/evidence?product_id={TARGET}&page=1&page_size=5", 200,
     ["total", "page", "items", "total_pages"]),
    ("证据筛选", f"/api/evidence?product_id={TARGET}&sentiment=negative&page_size=3", 200,
     ["total", "items"]),
    ("属性字典", "/api/meta/aspects", 200, ["count", "items"]),
    # 错误路径
    ("产品不存在应 404", "/api/products/999999/overview", 404, ["error"]),
    ("非法 sentiment 应 400", f"/api/evidence?product_id={TARGET}&sentiment=xxx", 400, ["error"]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    print(f"后端接口自测  base={args.base}\n" + "=" * 72)
    passed = failed = 0

    for name, path, want_status, keys in CASES:
        url = args.base + path
        try:
            r = requests.get(url, timeout=15)
        except requests.RequestException as e:
            print(f"❌ {name:<20} {path}")
            print(f"     请求失败: {type(e).__name__}: {e}")
            failed += 1
            continue

        ok = r.status_code == want_status
        body = None
        try:
            body = r.json()
        except ValueError:
            ok = False

        missing = [k for k in keys if not isinstance(body, dict) or k not in body]
        if missing:
            ok = False

        mark = "✅" if ok else "❌"
        print(f"{mark} {name:<20} {want_status} -> {r.status_code}  {path}")
        if not ok:
            failed += 1
            if missing:
                print(f"     缺少字段: {missing}")
            print(f"     返回: {json.dumps(body, ensure_ascii=False)[:300]}")
        else:
            passed += 1
            if args.verbose:
                print(f"     {json.dumps(body, ensure_ascii=False)[:400]}")

    print("=" * 72)
    print(f"通过 {passed}/{passed + failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
