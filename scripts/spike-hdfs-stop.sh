#!/usr/bin/env bash
# 停止 HDFS 伪分布式。
# 注意：cron 看门狗每分钟会把它重新拉起。要长时间停机，先注释掉 crontab 里的看门狗两行。
set -uo pipefail

pkill -f 'org.apache.hadoop.hdfs.server.namenode.NameNode'
pkill -f 'org.apache.hadoop.hdfs.server.datanode.DataNode'
pkill -f 'org.apache.hadoop.hdfs.server.namenode.SecondaryNameNode'
sleep 4

echo "剩余 HDFS 进程:"
pgrep -af 'org.apache.hadoop.hdfs' || echo "  (已全部停止)"
echo
echo "提示：看门狗将在 ≤60 秒内自动重启它们。"
echo "要真正停机：crontab -e 注释掉 spike-hdfs-boot.sh 那两行，然后重跑本脚本。"
