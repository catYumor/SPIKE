#!/usr/bin/env bash
# 初始化 HDFS 目录结构（对应规格 §4 scripts/init_hdfs.sh、§6 HDFS 目录规范）
# 幂等：可反复执行，不会删除已有数据。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/spike-env.sh"

DIRS=(
  "/spike/raw/reviews"
  "/spike/staging/cleaned"
  "/spike/staging/aspects"
  "/spike/output/aggregation"
  "/spike/checkpoint"
)

echo "=== SPIKE HDFS 目录初始化 ==="

# 先确认 NameNode 活着，否则后面报错难懂
if ! hdfs dfsadmin -safemode get >/dev/null 2>&1; then
  echo "  [FAIL] 连不上 HDFS（NameNode 未启动）" >&2
  echo "         请先执行: bash $HERE/spike-hdfs-boot.sh" >&2
  exit 1
fi

SAFE=$(hdfs dfsadmin -safemode get 2>/dev/null)
if ! echo "$SAFE" | grep -q OFF; then
  echo "  [等待] $SAFE —— 等待 safemode 退出（最多 60 秒）"
  for _ in $(seq 1 60); do
    sleep 1
    hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF && break
  done
  if ! hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF; then
    echo "  [FAIL] safemode 60 秒内未退出" >&2
    exit 1
  fi
fi
echo "  [ OK ] HDFS 可用（safemode OFF）"

CREATED=0
EXISTED=0
for d in "${DIRS[@]}"; do
  if hdfs dfs -test -d "$d" 2>/dev/null; then
    printf '  [存在] %s\n' "$d"
    EXISTED=$((EXISTED + 1))
  else
    hdfs dfs -mkdir -p "$d"
    printf '  [创建] %s\n' "$d"
    CREATED=$((CREATED + 1))
  fi
done

echo
echo "  本次新建 $CREATED 个，已存在 $EXISTED 个，共 ${#DIRS[@]} 个"
echo
echo "=== 当前 /spike 结构 ==="
hdfs dfs -ls -R /spike 2>/dev/null | grep '^d' | awk '{print "  " $NF}' || true
echo
echo "初始化完成 ✅"
