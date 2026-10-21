#!/usr/bin/env bash
set -euo pipefail
TASK_WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
TASK_LAB="$TASK_WORKSPACE/data/anchor-integration/weidentity-lab"
TASK_LIB="$(bash "$TASK_WORKSPACE/scripts/anchor_sdk_cache.sh")"
if [[ "${1:-}" == compile ]]; then
  mkdir -p "$TASK_LAB/classes"
  javac -encoding UTF-8 -cp "$TASK_LIB/*" -d "$TASK_LAB/classes" "$TASK_WORKSPACE/integrations/weidentity/src/main/java/org/trusta/AnchorWeIdentity.java"
  exit
fi
cd "$TASK_LAB"
exec timeout --kill-after=5s 75s java -Dlog4j.configurationFile="$TASK_LAB/log4j2.xml" -cp "$TASK_LIB/*:$TASK_LAB/classes:$TASK_LAB/conf" org.trusta.AnchorWeIdentity "$TASK_LAB"
