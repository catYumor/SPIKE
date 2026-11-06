#!/usr/bin/env bash
# HDFS 伪分布式状态一览
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/spike-env.sh"

echo "=== 守护进程实例数（各应为 1）==="
echo "  namenode=$(pgrep -cf proc_namenode || true) datanode=$(pgrep -cf proc_datanode || true) secondarynamenode=$(pgrep -cf proc_secondarynamenode || true)"

echo "=== 进程与 cgroup ==="
jps -l 2>/dev/null | sed 's/^/  /'
for p in $(pgrep -f 'org.apache.hadoop.hdfs' 2>/dev/null); do
  printf '  PID %-6s %-24s %s\n' "$p" "$(tr '\0' ' ' < /proc/$p/cmdline | grep -oE 'proc_[a-z]+')" "$(cat /proc/$p/cgroup)"
done

echo "=== 监听端口 ==="
ss -ltn 2>/dev/null | grep -E ':(9000|9870|9864|9866|9868)\b' | awk '{print "  "$4}' || echo "  (无)"

echo "=== 安全模式 ==="; hdfs dfsadmin -safemode get 2>&1 | sed 's/^/  /'
echo "=== 数据节点 ==="; hdfs dfsadmin -report 2>&1 | grep -E '^Live datanodes|^Name:|DFS Used%' | sed 's/^/  /'

echo "=== 关键配置 ==="
echo "  fs.defaultFS    = $(hdfs getconf -confKey fs.defaultFS 2>/dev/null)"
echo "  dfs.replication = $(hdfs getconf -confKey dfs.replication 2>/dev/null)"
echo "  java            = $(java -version 2>&1 | head -1)"

echo "=== 看门狗 ==="
echo "  crontab:"; crontab -l 2>/dev/null | sed 's/^/    /'
echo "  最近日志:"; tail -3 "$SPIKE_LOG_DIR/spike-hdfs-cron.log" 2>/dev/null | sed 's/^/    /'

echo "=== Web UI ==="; echo "  NameNode: http://localhost:9870/"
