#!/usr/bin/env bash
# 启动 HDFS 伪分布式。
# 注意：手动启动的进程挂在当前会话的 /init.scope 下，wsl.exe 调用结束会被回收。
# 常驻请依赖 cron 看门狗，通常无需手动执行本脚本。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/spike-env.sh"

bash "$HERE/spike-hdfs-boot.sh"

for _ in $(seq 1 40); do
  hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF && break
  sleep 3
done

exec bash "$HERE/spike-hdfs-status.sh"
