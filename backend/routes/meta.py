# -*- coding: utf-8 -*-
"""健康检查与元信息接口（规格 §10）：

  GET /api/health
"""

from __future__ import annotations

import pymysql
from flask import Blueprint, jsonify

from ..db import query_one

bp = Blueprint("meta", __name__, url_prefix="/api")

TABLES = [
    "products",
    "aspect_dictionary",
    "review_aspects",
    "aspect_aggregation",
    "aspect_priority",
    "competitive_gap",
]


@bp.get("/health")
def health():
    result = {"status": "ok", "mysql": {"connected": False}, "tables": {}}
    try:
        query_one("SELECT 1 AS ok")
        result["mysql"]["connected"] = True
    except pymysql.MySQLError as e:
        result["status"] = "error"
        result["mysql"]["error"] = f"{type(e).__name__}: {e}"
        return jsonify(result), 503

    for t in TABLES:
        try:
            row = query_one(f"SELECT COUNT(*) AS n FROM `{t}`")
            result["tables"][t] = (row or {}).get("n", 0)
        except pymysql.MySQLError:
            result["tables"][t] = None

    # 数据是否为空，给前端一个「需先跑流水线」的提示
    result["has_data"] = bool(result["tables"].get("aspect_aggregation"))
    return jsonify(result)


@bp.get("/meta/aspects")
def aspect_dict():
    """属性字典（规格 §9.1），前端做筛选下拉用。"""
    from ..db import jsonable_rows, query

    rows = jsonable_rows(
        query(
            """SELECT aspect_id, aspect_name, category, synonyms
                 FROM aspect_dictionary ORDER BY aspect_id"""
        )
    )
    return jsonify({"count": len(rows), "items": rows})
