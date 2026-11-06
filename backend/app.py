# -*- coding: utf-8 -*-
"""Flask 应用入口（规格 §10）。

  python -m backend.app                 # 开发模式，端口 5000
  python -m backend.app --port 8000
  SPIKE_BACKEND_PORT=8000 python -m backend.app

接口一览（全部只读）：
  GET /api/health
  GET /api/products
  GET /api/products/<product_id>/overview
  GET /api/products/<product_id>/advantages
  GET /api/products/<product_id>/painpoints
  GET /api/products/<product_id>/aspects
  GET /api/competitive-gap?target_id=&competitor_ids=
  GET /api/evidence?product_id=&aspect_name=&sentiment=&page=&page_size=
  GET /api/meta/aspects

如果 frontend/dist 存在，会自动把打包好的前端一起托管，
这样一个端口就能跑完整个看板。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.db import close_conn  # noqa: E402
from backend.routes import evidence, gap, meta, products  # noqa: E402

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

logger = logging.getLogger("spike.backend")


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(FRONTEND_DIST) if FRONTEND_DIST.exists() else None,
        static_url_path="",
    )
    app.json.ensure_ascii = False  # 中文直接输出，不转 \uXXXX
    app.config["JSON_SORT_KEYS"] = False

    # 开发期前端跑在 5173，需要跨域；生产同源给 Flask 托管
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    app.teardown_appcontext(close_conn)

    app.register_blueprint(products.bp)
    app.register_blueprint(gap.bp)
    app.register_blueprint(evidence.bp)
    app.register_blueprint(meta.bp)

    # ------------------------------------------------------------ 日志
    if not app.logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S"))
        app.logger.addHandler(h)
        app.logger.setLevel(logging.INFO)

    @app.after_request
    def _log_request(resp):
        if request.path.startswith("/api/"):
            app.logger.info("%s %s -> %s", request.method, request.full_path.rstrip("?"), resp.status_code)
        return resp

    # ------------------------------------------------------------ 错误处理
    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found", "path": request.path}), 404
        return _spa_index()

    @app.errorhandler(500)
    def _500(e):
        app.logger.exception("内部错误")
        return jsonify({"error": "internal_error"}), 500

    @app.errorhandler(Exception)
    def _unhandled(e):
        import pymysql

        if isinstance(e, pymysql.MySQLError):
            app.logger.exception("数据库错误")
            return jsonify({"error": "db_error", "message": str(e)}), 503
        raise e

    # ------------------------------------------------------------ 前端
    def _spa_index():
        index = FRONTEND_DIST / "index.html"
        if index.exists():
            return send_from_directory(FRONTEND_DIST, "index.html")
        return (
            jsonify(
                {
                    "message": "SPIKE 后端已启动，但前端还没有打包",
                    "hint": "cd frontend && npm install && npm run build",
                    "api": "/api/health",
                }
            ),
            200,
        )

    @app.get("/")
    def index():
        return _spa_index()

    @app.get("/<path:filename>")
    def static_files(filename: str):
        if filename.startswith("api/"):
            return jsonify({"error": "not_found"}), 404
        target = FRONTEND_DIST / filename
        if FRONTEND_DIST.exists() and target.is_file():
            return send_from_directory(FRONTEND_DIST, filename)
        return _spa_index()

    return app


app = create_app()


def main() -> None:
    port = int(os.environ.get("SPIKE_BACKEND_PORT", 5000))
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    print("=" * 58)
    print("  SPIKE 后端服务")
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
        if str(rule).startswith("/api"):
            methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
            print(f"    {methods:<5} {rule}")
    print(f"  前端静态目录: {FRONTEND_DIST} "
          f"({'已打包' if FRONTEND_DIST.exists() else '未打包'})")
    print(f"  监听: http://127.0.0.1:{port}")
    print("=" * 58)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
