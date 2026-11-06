#!/usr/bin/env bash
# 后台启动京东慢速采集器（规格 §14 的「保留本地 JSONL 重试」）。
#
#   bash scripts/start_slow_crawl.sh              # 立即开始，默认 8 秒/条
#   bash scripts/start_slow_crawl.sh 900          # 先等 900 秒再开始（避免持续触发风控）
#   bash scripts/start_slow_crawl.sh 0 3600 12    # 无延迟 + 限流等 1 小时 + 12 秒/条
#
# 参数依次为：首试延迟秒数、限流等待秒数、每条评论间隔秒数
# 进度： python crawler/jd_slow_crawl.py --status
# 日志： logs/crawl.log

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV="${SPIKE_VENV:-/home/hadoop/spike-venv}"
PY="$VENV/bin/python"
LOG_DIR="$PROJECT_ROOT/logs"
LOG="$LOG_DIR/crawl.log"
PID_FILE="$LOG_DIR/crawl.pid"

DELAY="${1:-0}"
LIMIT_WAIT="${2:-3600}"
REVIEW_INTERVAL="${3:-8}"
PAGE_INTERVAL="$(awk -v r="$REVIEW_INTERVAL" 'BEGIN{printf "%.0f", r*10}')"

mkdir -p "$LOG_DIR"

# 停掉旧进程（含 setsid 派生的）
if [[ -f "$PID_FILE" ]]; then
    old="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
        kill "$old" 2>/dev/null
        echo "已停止旧进程 (PID $old)"
    fi
    rm -f "$PID_FILE"
fi
pkill -f "[j]d_slow_crawl.py" 2>/dev/null || true
sleep 1

echo "启动慢速采集："
echo "  每条评论间隔 : ${REVIEW_INTERVAL}s（每页约 ${PAGE_INTERVAL}s，含随机抖动）"
echo "  首试延迟     : ${DELAY}s"
echo "  限流等待     : ${LIMIT_WAIT}s（逐次翻倍）"
echo "  日志         : $LOG"
setsid nohup bash -c \
    "sleep ${DELAY}; exec \"$PY\" crawler/jd_slow_crawl.py --review-interval ${REVIEW_INTERVAL} --limit-wait ${LIMIT_WAIT} --max-minutes 480" \
    > "$LOG" 2>&1 < /dev/null &
echo $! > "$PID_FILE"

sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "已启动 (PID $(cat "$PID_FILE"))"
    if [[ "$DELAY" -gt 0 ]]; then
        echo "将于约 $((DELAY / 60)) 分钟后发起第一次请求"
    fi
else
    echo "启动失败，日志："
    tail -20 "$LOG"
    exit 1
fi
