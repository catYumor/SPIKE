#!/usr/bin/env bash
# SPIKE HDFS 伪分布式「幂等启动」脚本，由 cron 看门狗调用。
#
# 设计要点（踩过的坑，勿改）：
#  1. 子进程必须用 9>&- 关闭继承的 flock fd，否则 nohup 出去后锁永不释放，看门狗会彻底失效。
#  2. 判定「是否需要启动」要同时看进程与端口：只看端口会把「正在启动中」的守护进程重复拉起。
#  3. 本脚本自包含（不 source 其它文件），因为它是 cron @reboot 的入口，要尽量减少失败点。
set -uo pipefail

export JAVA_HOME=/home/hadoop/jdk-11
export HADOOP_HOME=/usr/local/hadoop
export HADOOP_CONF_DIR="$HADOOP_HOME/etc/hadoop"
export HADOOP_MAPRED_HOME="$HADOOP_HOME"
export PATH="$JAVA_HOME/bin:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

LOG="${SPIKE_LOG_DIR:-/home/hadoop/logs}"
mkdir -p "$LOG"

exec 9>"$LOG/.spike-hdfs-boot.lock"
if ! flock -n 9; then
  echo "[$(date -Is)] 上一次启动仍在进行，跳过"
  exit 0
fi

port_up()    { ss -ltn 2>/dev/null | grep -q ":$1 "; }
proc_alive() { pgrep -f "proc_$1" >/dev/null 2>&1; }

# 已在运行、或正在启动（进程在但端口还没监听）都视为无需处理
ensure() {  # $1=名称 $2=端口 $3=proc 标识 $4..=命令
  local name="$1" port="$2" proc="$3"; shift 3
  if proc_alive "$proc"; then return 0; fi
  if port_up "$port"; then return 0; fi
  echo "[$(date -Is)] 启动 $name（进程与端口 $port 均不存在）"
  nohup "$@" >> "$LOG/hdfs-$name.log" 2>&1 9>&- &
  disown 2>/dev/null || true
}

ensure namenode          9000 namenode           hdfs namenode
for _ in $(seq 1 20); do port_up 9000 && break; sleep 2; done
ensure datanode          9866 datanode           hdfs datanode
ensure secondarynamenode 9868 secondarynamenode  hdfs secondarynamenode

echo "[$(date -Is)] 状态: nn=$(port_up 9000 && echo up || echo starting) dn=$(port_up 9866 && echo up || echo starting) snn=$(port_up 9868 && echo up || echo starting)"
