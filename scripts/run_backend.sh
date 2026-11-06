#!/usr/bin/env bash
# SPIKE 后端服务管理（规格 §12）
#
#   bash scripts/run_backend.sh start     # 后台启动
#   bash scripts/run_backend.sh stop      # 停止
#   bash scripts/run_backend.sh restart   # 重启
#   bash scripts/run_backend.sh status    # 看状态
#   bash scripts/run_backend.sh test      # 跑接口自测
#   bash scripts/run_backend.sh fg        # 前台运行（调试用）
#
# 端口默认 5000，可用环境变量 SPIKE_BACKEND_PORT 覆盖。

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV="${SPIKE_VENV:-/home/hadoop/spike-venv}"
PY="$VENV/bin/python"
PORT="${SPIKE_BACKEND_PORT:-5000}"
LOG_DIR="$PROJECT_ROOT/logs"
LOG="$LOG_DIR/backend.log"
PID_FILE="$LOG_DIR/backend.pid"

mkdir -p "$LOG_DIR"

is_running() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

start() {
    if is_running; then
        echo "已经在运行 (PID $(cat "$PID_FILE"))"
        return 0
    fi
    echo "启动后端，端口 $PORT ..."
    setsid nohup "$PY" -m backend.app --port "$PORT" > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    for _ in $(seq 1 20); do
        sleep 0.5
        if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
            echo "已启动 (PID $(cat "$PID_FILE"))  http://127.0.0.1:$PORT"
            return 0
        fi
    done
    echo "启动失败，日志如下："
    tail -30 "$LOG"
    return 1
}

stop() {
    if is_running; then
        local pid
        pid="$(cat "$PID_FILE")"
        kill "$pid" 2>/dev/null
        for _ in $(seq 1 10); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.3
        done
        kill -9 "$pid" 2>/dev/null
        echo "已停止 (PID $pid)"
    else
        echo "没有在运行"
    fi
    # 兜底：按端口清理
    if command -v fuser > /dev/null 2>&1; then
        fuser -k "${PORT}/tcp" > /dev/null 2>&1 || true
    fi
    rm -f "$PID_FILE"
}

status() {
    if is_running; then
        echo "运行中 (PID $(cat "$PID_FILE"))  http://127.0.0.1:$PORT"
        curl -s "http://127.0.0.1:$PORT/api/health" | "$PY" -m json.tool 2>/dev/null | head -20
    else
        echo "未运行"
        return 1
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    test)    "$PY" "$PROJECT_ROOT/scripts/test_api.py" --base "http://127.0.0.1:$PORT" ;;
    fg)      exec "$PY" -m backend.app --port "$PORT" ;;
    *)       echo "用法: $0 {start|stop|restart|status|test|fg}"; exit 1 ;;
esac
