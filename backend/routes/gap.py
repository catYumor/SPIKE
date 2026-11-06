# -*- coding: utf-8 -*-
"""竞品差距接口（规格 §10 / §11）：

  GET /api/competitive-gap?target_id=xxx&competitor_ids=a,b

返回两部分：
  1. matrix —— 前端竞品差距矩阵直接渲染用（行=属性，列=产品）
  2. gaps   —— competitive_gap 表的原始行（含 pos_gap / neg_gap）

不传 competitor_ids 时默认取所有竞品。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import jsonable_rows, query

bp = Blueprint("gap", __name__, url_prefix="/api")


def _product(product_id: str) -> dict | None:
    rows = query(
        """SELECT product_id, brand, model, is_target
             FROM products WHERE product_id = %s""",
        (product_id,),
    )
    if not rows:
        return None
    p = rows[0]
    p["is_target"] = bool(p["is_target"])
    return p


@bp.get("/competitive-gap")
def competitive_gap():
    target_id = (request.args.get("target_id") or "").strip()

    # 没指定目标产品就用 products 表里 is_target=1 的那个
    if not target_id:
        rows = query("SELECT product_id FROM products WHERE is_target = 1 LIMIT 1")
        if not rows:
            return jsonify({"error": "no_target_product"}), 404
        target_id = rows[0]["product_id"]

    target = _product(target_id)
    if not target:
        return jsonify({"error": "target_not_found", "target_id": target_id}), 404

    raw_ids = (request.args.get("competitor_ids") or "").strip()
    if raw_ids:
        comp_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
    else:
        comp_ids = [r["product_id"] for r in query(
            "SELECT product_id FROM products WHERE is_target = 0"
        )]

    competitors = [p for cid in comp_ids if (p := _product(cid))]
    all_ids = [target_id] + [c["product_id"] for c in competitors]

    placeholders = ",".join(["%s"] * len(all_ids))
    agg_rows = jsonable_rows(
        query(
            f"""SELECT product_id, aspect_name, pos_rate, neg_rate, total_count
                  FROM aspect_aggregation
                 WHERE product_id IN ({placeholders})""",
            tuple(all_ids),
        )
    )

    # 按 属性 -> 产品 组织成矩阵
    values: dict[str, dict[str, dict]] = {}
    for r in agg_rows:
        values.setdefault(r["aspect_name"], {})[r["product_id"]] = {
            "pos_rate": r["pos_rate"],
            "neg_rate": r["neg_rate"],
            "total_count": r["total_count"],
        }

    # 只保留目标产品和所有竞品都有数据的属性（才能真正对比）
    common_aspects = [
        a for a, per in values.items()
        if all(pid in per for pid in all_ids)
    ]
    common_aspects.sort(
        key=lambda a: values[a][target_id]["pos_rate"],
        reverse=True,
    )

    matrix = [
        {
            "aspect_name": a,
            "values": {pid: values[a][pid] for pid in all_ids},
        }
        for a in common_aspects
    ]

    gaps = jsonable_rows(
        query(
            """SELECT category, aspect_name, target_product_id, competitor_product_id,
                      target_pos_rate, competitor_pos_rate, pos_gap,
                      target_neg_rate, competitor_neg_rate, neg_gap
                 FROM competitive_gap
                WHERE target_product_id = %s
                ORDER BY pos_gap DESC""",
            (target_id,),
        )
    )

    return jsonify(
        {
            "target": target,
            "competitors": competitors,
            "products": [target] + competitors,
            "aspects": common_aspects,
            "matrix": matrix,
            "gaps": gaps,
            "common_aspect_count": len(common_aspects),
        }
    )
