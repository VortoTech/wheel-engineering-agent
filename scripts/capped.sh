#!/bin/bash
# Run a command and kill it above $CAP_KB resident memory (default 4 GB); print the peak.
# CAD builds need it: one OCC boolean once grew past 90 GB (2026-09-24).
#   CAP_KB=20000000 scripts/capped.sh .venv/bin/python experiments/forged-blank/benchmark.py ...
CAP_KB=${CAP_KB:-4000000}
"$@" & PID=$!; PEAK=0
while kill -0 $PID 2>/dev/null; do
  R=$(ps -o rss= -p $PID); R=${R:-0}; [ $R -gt $PEAK ] && PEAK=$R
  if [ $R -gt $CAP_KB ]; then kill -9 $PID; echo "KILLED at ${R} KB"; fi
  sleep 1
done
wait $PID; STATUS=$?; echo "exit $STATUS peak_MB $((PEAK/1024))"; exit $STATUS
