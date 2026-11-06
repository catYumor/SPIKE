# -*- coding: utf-8 -*-
"""产品相关接口（规格 §10）:

  GET /api/products
  GET /api/products/<product_id>/overview     健康总览
  GET /api/products/<product_id>/advantages   优势清单
  GET /api/products/<product_id>/painpoints   痛点清单（按 priority_score 降序）
  GET /api/products/<product_id>/aspects      全部属性聚合

全部只读，数据来自 aspect_aggregation + aspect_priority 两张表。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import jsonable_row, jsonable_rows, query, query_one

bp = Blueprint("products", __name__, url_prefix="/api")

# 属性聚合 + 优先级信息的统一定义，下面几个接口复用
ASPECT_SQL = """
    SELECT a.product_id,
           a.aspect_name,
           a.pos_count,
           a.neg_count,
           a.neu_count,
           a.total_count,
           a.pos_rate,
           a.neg_rate,
           p.label,
           p.kano_type,
           p.priority_score
      FROM aspect_aggregation a
      LEFT JOIN aspect_priority p
             ON p.product_id = a.product_id
            AND p.aspect_name = a.aspect_name
     WHERE a.product_id = %s
"""


def _get_product(product_id: str) -> dict | None:
    return jsonable_row(
        query_one(
            """SELECT product_id, brand, model, category, is_target
                 FROM products WHERE product_id = %s""",
            (product_id,),
        )
    )


def _not_found(product_id: str):
    return jsonify({"error": "product_not_found", "product_id": product_id}), 404


def _aspect_rows(product_id: str, order_by: str) -> list[dict]:
    rows = jsonable_rows(query(f"{ASPECT_SQL} ORDER BY {order_by}", (product_id,)))
    # label / kano_type 可能为 NULL（还没跑 Job 04），统一兜底
    for r in rows:
        r["label"] = r.get("label") or "neutral"
        r["kano_type"] = r.get("kano_type") or "indifferent"
        r["priority_score"] = r.get("priority_score") or 0.0
    return rows


@bp.get("/products")
def list_products():
    rows = jsonable_rows(
        query(
            """SELECT product_id, brand, model, category, is_target
                 FROM products ORDER BY is_target DESC, product_id"""
        )
    )
    for r in rows:
        r["is_target"] = bool(r["is_target"])
    return jsonify({"products": rows, "count": len(rows)})


@bp.get("/products/<product_id>/overview")
def overview(product_id: str):
    product = _get_product(product_id)
    if not product:
        return _not_found(product_id)

    aspects = _aspect_rows(product_id, "a.total_count DESC")

    total_aspects = sum(a["total_count"] or 0 for a in aspects)
    pos_total = sum(a["pos_count"] or 0 for a in aspects)
    neg_total = sum(a["neg_count"] or 0 for a in aspects)

    covered = query_one(
        """SELECT COUNT(DISTINCT review_id) AS n
             FROM review_aspects WHERE product_id = %s""",
        (product_id,),
    )

    summary = {
        "covered_reviews": (covered or {}).get("n", 0),
        "total_aspects": total_aspects,
        "positive_rate": round(pos_total / total_aspects, 4) if total_aspects else 0.0,
        "negative_rate": round(neg_total / total_aspects, 4) if total_aspects else 0.0,
        "aspect_count": len(aspects),
        "advantage_count": sum(1 for a in aspects if a["label"] == "advantage"),
        "painpoint_count": sum(1 for a in aspects if a["label"] == "painpoint"),
    }

    product["is_target"] = bool(product["is_target"])
    return jsonify({"product": product, "summary": summary, "aspects": aspects})


@bp.get("/products/<product_id>/advantages")
def advantages(product_id: str):
    if not _get_product(product_id):
        return _not_found(product_id)

    rows = [r for r in _aspect_rows(product_id, "a.pos_rate DESC") if r["label"] == "advantage"]
    return jsonify(
        {
            "product_id": product_id,
            "count": len(rows),
            "items": rows,
            "note": "优势判定：total_count >= 5 且 pos_rate >= 0.75（规格 §8.4）",
        }
    )


@bp.get("/products/<product_id>/painpoints")
def painpoints(product_id: str):
    if not _get_product(product_id):
        return _not_found(product_id)

    rows = [r for r in _aspect_rows(product_id, "p.priority_score DESC") if r["label"] == "painpoint"]
    fallback = False
    note = "痛点判定：提及量 ≥ 5 且负面率 ≥ 30%，按 priority_score 降序（规格 §8.4）"

    if not rows:
        # 数据源如果以好评为主，没有任何属性达到 30% 的绝对阈值，
        # 这时退化成「相对短板」：按负面率排序取前 3 个，仍然只做参考。
        ranked = sorted(
            [r for r in _aspect_rows(product_id, "a.neg_rate DESC") if (r["neg_count"] or 0) > 0],
            key=lambda r: (float(r["neg_rate"] or 0), int(r["neg_count"] or 0)),
            reverse=True,
        )
        rows = ranked[:3]
        if rows:
            fallback = True
            note = (
                "样本中没有属性达到「负面率 ≥ 30%」的痛点阈值（数据源以好评为主，"
                f"全部属性负面率仅 {min(r['neg_rate'] for r in ranked):.1%}–"
                f"{max(r['neg_rate'] for r in ranked):.1%}）。"
                "以下按负面率排序给出**相对短板**，仅供参考，不等同于规格定义的痛点。"
            )

    return jsonify(
        {
            "product_id": product_id,
            "count": len(rows),
            "items": rows,
            "fallback": fallback,
            "note": note,
        }
    )


@bp.get("/products/<product_id>/aspects")
def aspects(product_id: str):
    if not _get_product(product_id):
        return _not_found(product_id)

    rows = _aspect_rows(product_id, "a.total_count DESC")
    return jsonify({"product_id": product_id, "count": len(rows), "items": rows})
