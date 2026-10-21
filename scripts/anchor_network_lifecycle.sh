#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/../data/anchor-integration/network" && pwd)"
test -f "$TASK_ROOT/INITIALIZED"
cd "$TASK_ROOT/nodes/127.0.0.1"
case "${1:-}" in
  start) bash start_all.sh ;;
  run)
    bash start_all.sh
    trap 'bash stop_all.sh' EXIT INT TERM
    while sleep 30; do :; done
    ;;
  stop) bash stop_all.sh ;;
  *) echo 'Specify start, run, or stop for the isolated TRUSTA chain.' >&2; exit 2 ;;
esac
