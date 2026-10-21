#!/usr/bin/env bash
# SDK jars are public dependencies. Keep no keys in this cache.
set -euo pipefail
TASK_WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
TASK_SOURCE="$TASK_WORKSPACE/data/anchor-integration/toolchain/weid-lib"
TASK_CACHE="$HOME/.cache/trusta-anchor-sdk"
mkdir -p "$TASK_CACHE"
chmod 700 "$TASK_CACHE"
for TASK_FILE in "$TASK_SOURCE"/*.jar; do
  TASK_NAME="$(basename "$TASK_FILE")"
  if [[ ! -f "$TASK_CACHE/$TASK_NAME" ]] || ! cmp -s "$TASK_FILE" "$TASK_CACHE/$TASK_NAME"; then
    cp "$TASK_FILE" "$TASK_CACHE/$TASK_NAME"
    chmod 600 "$TASK_CACHE/$TASK_NAME"
  fi
done
printf '%s' "$TASK_CACHE"
