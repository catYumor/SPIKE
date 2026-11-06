#!/usr/bin/env bash
# SPIKE 数据处理流水线编排（对应规格 §4 scripts/run_pipeline.sh、§8 作业规格、§12 部署步骤）
#
# 用法:
#   bash scripts/run_pipeline.sh              # 从第 1 步跑到最后
#   bash scripts/run_pipeline.sh --from 2     # 从第 2 步开始
#   bash scripts/run_pipeline.sh --only 1     # 只跑第 1 步
#   bash scripts/run_pipeline.sh --list       # 只看步骤清单
#
# 4 个作业（规格 §8）:
#   1. clean     raw JSONL      -> staging/cleaned (Parquet)
#   2. absa      cleaned        -> staging/aspects
#   3. aggregate aspects        -> output/aggregation + MySQL(review_aspects, aspect_aggregation)
#   4. gap       aggregation    -> MySQL(aspect_priority, competitive_gap)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/spike-env.sh"

PROJECT_ROOT="$(dirname "$HERE")"
JOBS_DIR="$PROJECT_ROOT/spark_jobs"

# 步骤编号 | 名称 | 脚本文件 | 说明
STEP_NUM=(1 2 3 4)
STEP_NAME=(clean absa aggregate gap)
STEP_FILE=(
  "01_clean.py"
  "02_absa_extract.py"
  "03_aggregate.py"
  "04_priority_gap.py"
)
STEP_DESC=(
  "清洗：raw JSONL -> staging/cleaned (Parquet)"
  "ABSA 抽取：cleaned -> staging/aspects"
  "归并聚合：aspects -> output/aggregation + MySQL"
  "优先级与竞品差距：aggregation -> MySQL"
)

list_steps() {
  echo "SPIKE 流水线步骤（规格 §8）:"
  for i in "${!STEP_NUM[@]}"; do
    local mark="  "
    [ -f "$JOBS_DIR/${STEP_FILE[$i]}" ] && mark="✅" || mark="⬜"
    printf '  %s %d. %-10s %-24s %s\n' \
      "$mark" "${STEP_NUM[$i]}" "${STEP_NAME[$i]}" "${STEP_FILE[$i]}" "${STEP_DESC[$i]}"
  done
  echo
  echo "  图例: ✅ 已实现   ⬜ 未实现"
}

run_step() {
  local idx=$1
  local num="${STEP_NUM[$idx]}"
  local file="${STEP_FILE[$idx]}"
  local path="$JOBS_DIR/$file"

  echo
  echo "================================================================"
  echo "  步骤 $num/4 — ${STEP_NAME[$idx]}"
  echo "  ${STEP_DESC[$idx]}"
  echo "================================================================"

  if [ ! -f "$path" ]; then
    echo "  [跳过] 作业尚未实现: spark_jobs/$file" >&2
    echo "         按规格 §8.${num} 实现后即可运行。" >&2
    return 2
  fi

  local t0=$SECONDS
  if "$SPIKE_VENV/bin/python" "$path"; then
    printf '\n  [ OK ] 步骤 %s 完成，耗时 %ds\n' "$num" "$((SECONDS - t0))"
    return 0
  else
    local rc=$?
    printf '\n  [FAIL] 步骤 %s 失败，退出码 %d\n' "$num" "$rc" >&2
    return "$rc"
  fi
}

main() {
  local from=1
  local only=""

  while [ $# -gt 0 ]; do
    case "$1" in
      --list) list_steps; exit 0 ;;
      --from) from="${2:?--from 需要参数}"; shift 2 ;;
      --only) only="${2:?--only 需要参数}"; shift 2 ;;
      -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *) echo "未知参数: $1（用 --help 看用法）" >&2; exit 1 ;;
    esac
  done

  echo "=== SPIKE 流水线启动 ==="
  echo "  项目:     $PROJECT_ROOT"
  echo "  venv:     $SPIKE_VENV"
  echo "  HDFS:     $SPIKE_HDFS_ROOT"
  echo

  # 前置检查：HDFS 必须可用（4 个作业全都读写 HDFS）
  # 刚启动的 HDFS 会短暂处于 safemode，不能一看到非 OFF 就退出
  if ! hdfs dfsadmin -safemode get >/dev/null 2>&1; then
    echo "  [FAIL] 连不上 HDFS，先跑: bash scripts/spike-hdfs-boot.sh" >&2
    exit 1
  fi
  if ! hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF; then
    echo "  [等待] safemode 未退出，最多等 60 秒..."
    for _ in $(seq 1 60); do
      hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF && break
      sleep 1
    done
    if ! hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF; then
      echo "  [FAIL] safemode 60 秒内未退出" >&2
      exit 1
    fi
    echo "  [ OK ] safemode 已退出"
  fi

  local started=$SECONDS
  local ran=0 skipped=0 failed=0

  for i in "${!STEP_NUM[@]}"; do
    if [ -n "$only" ] && [ "${STEP_NUM[$i]}" != "$only" ]; then continue; fi
    if [ "${STEP_NUM[$i]}" -lt "$from" ]; then continue; fi

    run_step "$i"
    case $? in
      0) ran=$((ran + 1)) ;;
      2) skipped=$((skipped + 1)) ;;
      *) failed=$((failed + 1)); break ;;
    esac
  done

  echo
  echo "================================================================"
  printf '  流水线结束：执行 %d 步，跳过 %d 步，失败 %d 步，总耗时 %ds\n' \
    "$ran" "$skipped" "$failed" "$((SECONDS - started))"
  echo "================================================================"

  [ "$failed" -eq 0 ] || exit 1
  [ "$ran" -gt 0 ] || { echo "  没有可执行的步骤（作业都还没实现）"; exit 2; }
}

main "$@"
