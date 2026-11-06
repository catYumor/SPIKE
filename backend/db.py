# -*- coding: utf-8 -*-
"""MySQL 访问层（对应规格 §10「Flask 只读 MySQL」）。

设计要点：
  - 每个请求复用一条连接（放在 flask.g 里），请求结束自动关闭
  - 强制只读：所有查询都走这里，不提供任何写接口
  - MySQL 的 DECIMAL / datetime 不能直接 jsonify，统一在这是转成 float / str
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pymysql
from flask import g

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import MYSQL  # noqa: E402


def get_conn() -> pymysql.connections.Connection:
    """取当前请求的数据库连接，没有就建一条。"""
    if "db" not in g:
        g.db = pymysql.connect(
            **MYSQL,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            connect_timeout=5,
            read_timeout=15,
        )
    return g.db


def close_conn(exc=None) -> None:
    """请求结束时关闭连接。注册为 teardown_appcontext。"""
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def query(sql: str, args: tuple | list | None = None) -> list[dict]:
    with get_conn().cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def query_one(sql: str, args: tuple | list | None = None) -> dict | None:
    with get_conn().cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchone()


def scalar(sql: str, args: tuple | list | None = None):
    row = query_one(sql, args)
    if not row:
        return None
    return next(iter(row.values()))


# ---------------------------------------------------------------- 类型转换


def to_jsonable(value):
    """把 MySQL 返回的特殊类型转成可 JSON 序列化的类型。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


def jsonable_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {k: to_jsonable(v) for k, v in row.items()}


def jsonable_rows(rows: list[dict]) -> list[dict]:
    return [{k: to_jsonable(v) for k, v in r.items()} for r in rows]
