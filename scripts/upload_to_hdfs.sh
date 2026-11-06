#!/usr/bin/env bash
# 把本地 JSONL 上传到 HDFS（对应规格 §5.4 HDFS 写入规范）
#
# 用法:
#   bash scripts/upload_to_hdfs.sh          # 上传 data/raw/*.jsonl
#   bash scripts/upload_to_hdfs.sh --dry-run
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/spike-env.sh"

PROJECT_ROOT="$(dirname "$HERE")"
RAW_DIR="$PROJECT_ROOT/data/raw"
HDFS_RAW="/spike/raw/reviews"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

echo "=== 上传评论到 HDFS ==="
echo "  本地: $RAW_DIR"
echo "  远端: $HDFS_RAW"
[ "$DRY_RUN" -eq 1 ] && echo "  [DRY-RUN] 只打印不执行"
echo

if [ ! -d "$RAW_DIR" ]; then
  echo "  [FAIL] 目录不存在: $RAW_DIR" >&2
  echo "         先跑: python crawler/jd_review_crawler.py --provider mock" >&2
  exit 1
fi

shopt -s nullglob
FILES=("$RAW_DIR"/*.jsonl)
shopt -u nullglob

if [ ${#FILES[@]} -eq 0 ]; then
  echo "  [FAIL] $RAW_DIR 下没有 .jsonl 文件" >&2
  exit 1
fi

# safemode 期间不能写，必须等它退出（刚启动的 HDFS 会短暂处于 safemode）
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

# 本地有哪些商品
declare -A WANT=()
for f in "${FILES[@]}"; do
  WANT["$(basename "$f" .jsonl)"]=1
done

# 清理 HDFS 上「本地已经没有」的商品目录。
# 换竞品时如果不删，旧商品的目录会一直留在 /spike/raw/reviews 下，
# 下游 Spark 还是会把它们读进来，导致分析里冒出已经下线的产品。
if [ "$DRY_RUN" -eq 0 ] && hdfs dfs -test -d "$HDFS_RAW" 2>/dev/null; then
  while read -r line; do
    dir="${line##* }"
    pid="${dir##*product_id=}"
    if [ -z "${WANT[$pid]:-}" ]; then
      echo "  [清理] HDFS 上有本地已移除的商品目录: $dir"
      hdfs dfs -rm -r -f "$dir" >/dev/null 2>&1 || true
    fi
  done < <(hdfs dfs -ls "$HDFS_RAW" 2>/dev/null | grep '^d' || true)
fi

TOTAL=0
for f in "${FILES[@]}"; do
  base=$(basename "$f" .jsonl)
  dest="$HDFS_RAW/product_id=$base"
  lines=$(wc -l < "$f")
  size=$(stat -c%s "$f")
  echo "  $base: $lines 行 / $size 字节"
  echo "    -> $dest/reviews.jsonl"

  if [ "$DRY_RUN" -eq 1 ]; then
    continue
  fi

  # 幂等：先建目录再覆盖写入，避免重复上传产生 _COPY_
  hdfs dfs -mkdir -p "$dest"
  hdfs dfs -put -f "$f" "$dest/reviews.jsonl"
  TOTAL=$((TOTAL + lines))
done

echo
if [ "$DRY_RUN" -eq 1 ]; then
  echo "  DRY-RUN 结束"
  exit 0
fi

echo "  上传完成，共 $TOTAL 行"
echo
echo "=== HDFS 现状 ==="
hdfs dfs -ls -R "$HDFS_RAW" 2>/dev/null | grep -v '^d' | awk '{print "  " $5 " 字节  " $NF}' || true
