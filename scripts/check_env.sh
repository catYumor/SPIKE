#!/usr/bin/env bash
# SPIKE 环境自检：逐项确认依赖是否就绪
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/spike-env.sh"

PASS=0; FAIL=0
ok()   { printf '  [\033[32m OK \033[0m] %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  [\033[31mFAIL\033[0m] %s\n' "$1"; FAIL=$((FAIL+1)); }
info() { printf '         %s\n' "$1"; }

echo "=== 1. 项目路径 ==="
[ -d "$SPIKE_HOME" ] && ok "SPIKE_HOME = $SPIKE_HOME" || bad "SPIKE_HOME 不存在: $SPIKE_HOME"
info "日志目录 = $SPIKE_LOG_DIR"

echo "=== 2. JDK ==="
if [ -x "$JAVA_HOME/bin/java" ]; then
  V=$("$JAVA_HOME/bin/java" -version 2>&1 | head -1)
  case "$V" in
    *'"11'*) ok "JDK 11: $V" ;;
    *)       bad "JAVA_HOME 指向的不是 JDK 11: $V" ;;
  esac
else
  bad "找不到 $JAVA_HOME/bin/java"
fi

echo "=== 3. Hadoop / HDFS ==="
command -v hdfs >/dev/null && ok "hdfs 命令可用" || bad "hdfs 命令不可用"
[ -d "$HADOOP_CONF_DIR" ] && ok "HADOOP_CONF_DIR = $HADOOP_CONF_DIR" || bad "配置目录不存在"
info "fs.defaultFS    = $(hdfs getconf -confKey fs.defaultFS 2>/dev/null)"
info "dfs.replication = $(hdfs getconf -confKey dfs.replication 2>/dev/null)"

SAFE=""
for _ in $(seq 1 20); do
  SAFE=$(hdfs dfsadmin -safemode get 2>&1)
  echo "$SAFE" | grep -q OFF && break
  sleep 3
done
if echo "$SAFE" | grep -q OFF; then
  ok "HDFS 就绪（safemode OFF）"
elif echo "$SAFE" | grep -qi 'ON'; then
  bad "HDFS 仍处安全模式（已等待 60 秒）；发行版刚启动时属正常，稍后重跑本脚本"
else
  bad "HDFS 连不上：$SAFE"
fi
N=$(pgrep -cf proc_namenode || true); D=$(pgrep -cf proc_datanode || true); S=$(pgrep -cf proc_secondarynamenode || true)
if [ "$N" = 1 ] && [ "$D" = 1 ] && [ "$S" = 1 ]; then
  ok "守护进程实例数正确 nn=1 dn=1 snn=1"
else
  bad "守护进程实例数异常 nn=$N dn=$D snn=$S（期望各 1）"
fi

echo "=== 4. HDFS 目录 ==="
for d in /spike/raw/reviews /spike/staging/cleaned /spike/staging/aspects /spike/output/aggregation /spike/checkpoint; do
  hdfs dfs -test -d "$d" 2>/dev/null && ok "$d" || bad "$d 缺失"
done

echo "=== 5. Python 虚拟环境 ==="
if [ -x "$SPIKE_VENV/bin/python" ]; then
  ok "venv = $SPIKE_VENV ($("$SPIKE_VENV/bin/python" --version 2>&1))"
  for m in pyspark pandas numpy pyarrow jieba snownlp flask pymysql yaml requests; do
    if "$SPIKE_VENV/bin/python" -c "import $m" 2>/dev/null; then
      V=$("$SPIKE_VENV/bin/python" -c "import $m;print(getattr($m,'__version__',''))" 2>/dev/null)
      ok "python 包 $m $V"
    else
      bad "python 包 $m 缺失"
    fi
  done
else
  bad "venv 不存在: $SPIKE_VENV"
fi

echo "=== 6. MySQL ==="
if command -v mysql >/dev/null; then
  R=$(mysql -h "$SPIKE_MYSQL_HOST" -P "$SPIKE_MYSQL_PORT" -u "$SPIKE_MYSQL_USER" -p"$SPIKE_MYSQL_PASSWORD" \
        -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$SPIKE_MYSQL_DB';" 2>/dev/null)
  if [ "$R" = "6" ]; then
    ok "MySQL 可连接（${SPIKE_MYSQL_HOST}:${SPIKE_MYSQL_PORT}），$SPIKE_MYSQL_DB 有 6 张表"
  else
    bad "MySQL 连接或表数异常：$R"
  fi
  info "服务状态: $(systemctl is-active mysql 2>&1) / $(systemctl is-enabled mysql 2>&1)"
else
  bad "mysql 客户端不可用"
fi

echo "=== 7. Node.js ==="
if command -v node >/dev/null; then
  ok "node $(node --version) / npm $(npm --version 2>/dev/null)"
else
  bad "node 不可用"
fi

echo "=== 8. cron 看门狗 ==="
if crontab -l 2>/dev/null | grep -q 'spike-hdfs-boot.sh'; then
  ok "crontab 已配置看门狗"
  crontab -l 2>/dev/null | grep 'spike-hdfs-boot.sh' | sed 's/^/         /'
else
  bad "crontab 里没有看门狗，HDFS 不会自动拉起"
fi

echo
echo "===================================="
printf '  通过 %d 项，失败 %d 项\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && echo "  环境就绪 ✅" || echo "  有项目未通过，见上方 FAIL ❌"
echo "===================================="
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
