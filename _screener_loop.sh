#!/bin/bash
cd /c/Users/dogki/.claude/sessions/cloud_bot
for i in 1 2 3 4 5 6 7 8; do
  echo "=== 第 $i 輪 $(date '+%H:%M') ===" >> double_screener_run.log
  PYTHONIOENCODING=utf-8 python double_screener.py >> double_screener_run.log 2>&1
  if ! grep -q "額度用盡" <(tail -3 double_screener_run.log); then
    echo "=== 完成於第 $i 輪 ===" >> double_screener_run.log; break
  fi
  sleep 3900
done
