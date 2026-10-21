#!/usr/bin/env bash
set -euo pipefail
for TASK_PID in $(pgrep -f '^java .*org.trusta.AnchorWeIdentity'); do
  jstack "$TASK_PID" | sed -n '1,60p'
done
