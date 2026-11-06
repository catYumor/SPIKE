#!/usr/bin/env bash
# 一条命令把数据变更推到能看的状态（明天补抓完差评就跑这个）。
#
#   bash scripts/refresh_all.sh              # 全流程
#   bash scripts/refresh_all.sh --no-shots   # 跳过截图
#   bash scripts/refresh_all.sh --no-test    # 跳过验收测试
#
# 依次做：
#   1. 起 HDFS（WSL 闲置会自动停，必须先拉起来）
#   2. 校验 data/raw/*.jsonl 格式
#   3. 上传 HDFS
#   4. 跑 4 个 Spark 作业
#   5. 重启后端
#   6. 验收测试 T1–T10（生成 docs/验收报告.md）
#   7. 刷新三个页面截图
#
# 任何一步失败都会立刻停下并告诉你停在哪一步。

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$HERE/.." && pwd)"
cd "$PROJECT_ROOT"
source "$HERE/spike-env.sh" >/dev/null 2>&1

VENV="${SPIKE_VENV:-/home/hadoop/spike-venv}"
PY="$VENV/bin/python"

SHOTS=1
TEST=1
for arg in "$@"; do
    case "$arg" in
        --no-shots) SHOTS=0 ;;
        --no-test)  TEST=0 ;;
        -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "未知参数: $arg"; exit 2 ;;
    esac
done

STEP=0
TOTAL=7
step() { STEP=$((STEP + 1)); printf '\n\033[1m[%d/%d] %s\033[0m\n' "$STEP" "$TOTAL" "$1"; }
die()  { printf '\n\033[31m[FAIL] %s\033[0m\n' "$1"; exit 1; }

START=$(date +%s)

# ---------------------------------------------------------------- 1. HDFS
step "启动 HDFS"
if hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF; then
    echo "  HDFS 已在运行"
else
    bash "$HERE/spike-hdfs-start.sh" >/dev/null 2>&1 \
        || die "HDFS 启动失败，单独跑 bash scripts/spike-hdfs-start.sh 看详情"
    echo "  HDFS 已启动"
fi

# ---------------------------------------------------------------- 2. 校验
step "校验 data/raw 数据格式"
shopt -s nullglob
FILES=(data/raw/*.jsonl)
shopt -u nullglob
[[ ${#FILES[@]} -gt 0 ]] || die "data/raw 下没有 jsonl 文件"
"$PY" crawler/validate_jsonl.py "${FILES[@]}" || die "数据格式校验不通过，先修数据"

# ---------------------------------------------------------------- 3. 上传
step "上传 HDFS"
bash "$HERE/upload_to_hdfs.sh" || die "上传失败"

# ---------------------------------------------------------------- 4. 流水线
step "跑 Spark 流水线"
bash "$HERE/run_pipeline.sh" || die "流水线失败，看 logs/ 下的作业日志"

# ---------------------------------------------------------------- 5. 后端
step "重启后端"
bash "$HERE/run_backend.sh" restart >/dev/null 2>&1 || die "后端启动失败"
curl -sf http://127.0.0.1:5000/api/health >/dev/null \
    || die "后端健康检查不通过"
echo "  后端 OK： http://127.0.0.1:5000"

# ---------------------------------------------------------------- 6. 验收
if [[ $TEST -eq 1 ]]; then
    step "验收测试 T1–T10"
    "$PY" scripts/acceptance_test.py || die "验收测试未全部通过，看 docs/验收报告.md"
else
    step "验收测试（已跳过）"
fi

# ---------------------------------------------------------------- 7. 截图
if [[ $SHOTS -eq 1 ]]; then
    step "刷新前端截图"
    "$PY" scripts/screenshot_ui.py || die "截图失败（页面有 JS 报错？）"
else
    step "截图（已跳过）"
fi

ELAPSED=$(( $(date +%s) - START ))
printf '\n\033[1m================================================================\033[0m\n'
printf '  全部完成，耗时 %d 秒\n' "$ELAPSED"
printf '  打开 http://127.0.0.1:5000 查看\n'
printf '\033[1m================================================================\033[0m\n'
