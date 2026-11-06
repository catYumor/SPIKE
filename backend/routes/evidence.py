# -*- coding: utf-8 -*-
"""评论证据接口（规格 §10）：

  GET /api/evidence?product_id=xxx&aspect_name=电池续航&sentiment=negative
                   &page=1&page_size=20

返回某产品（或某属性、某情感）下的原始评论证据，分页。
证据文本来自 review_aspects.evidence_text（规格 §9.3 要求保留用户原话）。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import jsonable_rows, query, query_one

bp = Blueprint("evidence", __name__, url_prefix="/api")

MAX_PAGE_SIZE = 100
VALID_SENTIMENTS = {"positive", "negative", "neutral"}


@bp.get("/evidence")
def evidence():
    product_id = (request.args.get("product_id") or "").strip()
    aspect_name = (request.args.get("aspect_name") or "").strip()
    sentiment = (request.args.get("sentiment") or "").strip()
    review_id = (request.args.get("review_id") or "").strip()

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        page_size = 20
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    offset = (page - 1) * page_size

    where: list[str] = []
    args: list = []

    if product_id:
        where.append("product_id = %s")
        args.append(product_id)
    if aspect_name:
        where.append("aspect_name = %s")
        args.append(aspect_name)
    if sentiment:
        if sentiment not in VALID_SENTIMENTS:
            return jsonify({
                "error": "invalid_sentiment",
                "message": f"sentiment 只能是 {sorted(VALID_SENTIMENTS)}",
            }), 400
        where.append("sentiment = %s")
        args.append(sentiment)
    if review_id:
        where.append("review_id = %s")
        args.append(review_id)

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    total_row = query_one(f"SELECT COUNT(*) AS n FROM review_aspects {clause}", tuple(args))
    total = (total_row or {}).get("n", 0)

    rows = jsonable_rows(
        query(
            f"""SELECT review_id, product_id, aspect_name, sentiment,
                       confidence, evidence_text, create_time, score
                  FROM review_aspects
                  {clause}
                 ORDER BY create_time DESC, review_id DESC
                 LIMIT %s OFFSET %s""",
            tuple(args) + (page_size, offset),
        )
    )

    total_pages = (total + page_size - 1) // page_size if total else 0
    return jsonify(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "items": rows,
        }
    )
