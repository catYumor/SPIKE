#!/usr/bin/env bash
# SPIKE 功能性验证：不只是"文件在不在"，而是真正读写一遍各组件
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/spike-env.sh"

PASS=0; FAIL=0
ok()   { printf '  [\033[32m OK \033[0m] %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  [\033[31mFAIL\033[0m] %s\n' "$1"; FAIL=$((FAIL+1)); }
info() { printf '         %s\n' "$1"; }

HDFS=/spike/_smoke_$$
LOCAL=$(mktemp -d)

cleanup() { hdfs dfs -rm -r -f -skipTrash "$HDFS" >/dev/null 2>&1; rm -rf "$LOCAL"; }
trap cleanup EXIT

# ---------------------------------------------------------------- A. HDFS
echo "=== A. HDFS 实际读写 ==="
SAFE=$(hdfs dfsadmin -safemode get 2>&1)
echo "$SAFE" | grep -q OFF && ok "safemode OFF" || bad "safemode: $SAFE"

LIVE=$(hdfs dfsadmin -report 2>/dev/null | grep '^Live datanodes' | head -1)
info "$LIVE"
echo "$LIVE" | grep -qE 'Live datanodes \(1\)' && ok "1 个 DataNode 存活" || bad "DataNode 数量异常"

# 写入含中文与随机串的文件，读回比对 md5
RAND=$(head -c 16 /dev/urandom | md5sum | cut -c1-16)
cat > "$LOCAL/probe.txt" <<EOF
SPIKE 功能性验证
随机串: $RAND
中文行：拍照清晰，但电池续航一般
emoji：📷🔋
ascii: hello hdfs
EOF
M1=$(md5sum "$LOCAL/probe.txt" | awk '{print $1}')

hdfs dfs -mkdir -p "$HDFS" >/dev/null 2>&1
if hdfs dfs -put -f "$LOCAL/probe.txt" "$HDFS/probe.txt" 2>/dev/null; then
  M2=$(hdfs dfs -cat "$HDFS/probe.txt" 2>/dev/null | md5sum | awk '{print $1}')
  if [ "$M1" = "$M2" ]; then
    ok "写入 -> 读回 md5 一致 (${M1:0:12}.., $(stat -c%s "$LOCAL/probe.txt") 字节)"
  else
    bad "md5 不一致: 本地 $M1 / HDFS $M2"
  fi
else
  bad "hdfs dfs -put 失败"
fi

# 目录操作
hdfs dfs -mkdir -p "$HDFS/sub/deep" >/dev/null 2>&1
hdfs dfs -test -d "$HDFS/sub/deep" && ok "多级目录创建" || bad "多级目录创建失败"

# ---------------------------------------------------------------- B. PySpark
echo
echo "=== B. PySpark 实际作业 ==="
info "（首次启动约 30-60 秒，请稍候）"
if "$SPIKE_VENV/bin/python" "$HERE/smoke_pyspark.py" 2>&1 | sed 's/^/       /'; then
  ok "PySpark 作业完成（读 HDFS -> 清洗去重 -> 写 Parquet -> 读回校验）"
else
  bad "PySpark 作业失败"
fi

# ---------------------------------------------------------------- C. MySQL
echo
echo "=== C. MySQL 实际读写 ==="
MY_BASE=(mysql -h "$SPIKE_MYSQL_HOST" -P "$SPIKE_MYSQL_PORT" -u "$SPIKE_MYSQL_USER"
         "-p${SPIKE_MYSQL_PASSWORD}" -N -B "$SPIKE_MYSQL_DB")
if "${MY_BASE[@]}" -e "SELECT 1;" >/dev/null 2>&1; then
  ok "连接成功 ${SPIKE_MYSQL_HOST}:${SPIKE_MYSQL_PORT}"
  info "表: $("${MY_BASE[@]}" -e 'SHOW TABLES;' 2>/dev/null | tr '\n' ' ')"

  # 注意：TEMPORARY TABLE 是连接级的，建表/插入/查询必须在同一次连接内完成
  OUT=$(printf '%s\n' \
    'CREATE TEMPORARY TABLE _smoke (id INT PRIMARY KEY, v VARCHAR(64));' \
    "INSERT INTO _smoke VALUES (1,'ok'),(2,'中文测试'),(3,'emoji📷');" \
    'SELECT COUNT(*) FROM _smoke;' \
    'SELECT v FROM _smoke WHERE id=2;' \
    "SELECT GROUP_CONCAT(v ORDER BY id SEPARATOR '|') FROM _smoke;" \
    | "${MY_BASE[@]}" 2>/dev/null)

  N=$(printf '%s\n' "$OUT" | sed -n 1p)
  CH=$(printf '%s\n' "$OUT" | sed -n 2p)
  ALL=$(printf '%s\n' "$OUT" | sed -n 3p)
  info "插入并读回: $ALL"
  [ "$N" = "3" ] && ok "同一连接内 建临时表/插入/查询 正常（3 行）" || bad "行数异常: '$N'"
  [ "$CH" = "中文测试" ] && ok "中文往返无乱码 (utf8mb4)" || bad "中文乱码: '$CH'"

  for t in products aspect_dictionary; do
    info "  $t: $("${MY_BASE[@]}" -e "SELECT COUNT(*) FROM $t;" 2>/dev/null) 行"
  done
else
  bad "MySQL 连接失败"
fi

# ---------------------------------------------------------------- D. Node
echo
echo "=== D. Node.js / 前端工具链 ==="
NODE_V=$(node --version 2>/dev/null)
info "node $NODE_V / npm $(npm --version 2>/dev/null)"
[ -n "$NODE_V" ] && ok "Node 可用" || bad "Node 不可用"
if npm config get registry >/dev/null 2>&1; then
  info "npm registry: $(npm config get registry)"
fi

# ---------------------------------------------------------------- E. 版本汇总
echo
echo "=== E. 版本汇总 ==="
info "JDK      : $(java -version 2>&1 | head -1)"
info "Hadoop   : $(hadoop version 2>/dev/null | head -1)"
info "Python   : $("$SPIKE_VENV/bin/python" --version 2>&1)"
info "PySpark  : $("$SPIKE_VENV/bin/python" -c 'import pyspark;print(pyspark.__version__)' 2>/dev/null)"
info "MySQL    : $(mysql --version 2>/dev/null | sed 's/.*Distrib //;s/,.*//')"
info "Node     : $NODE_V"

echo
echo "===================================="
printf '  功能性验证：通过 %d 项，失败 %d 项\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && echo "  全部功能可用 ✅" || echo "  有功能不可用 ❌"
echo "===================================="
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
