#!/usr/bin/env bash
# macOS / Linux counterpart of start.ps1: API server + audit checkpoint worker + scheduled-plan worker
# (+ Feishu long connection when configured). Workers stop when the server exits. Logs go to data/*.log.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

# Python: the bundled runtime (portable package) > the installer's virtualenv > python3 on PATH
if [ -n "${TRUSTA_PYTHON:-}" ]; then PY="$TRUSTA_PYTHON"
elif [ -x runtime/python/bin/python3 ]; then PY=runtime/python/bin/python3
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
else PY=python3; fi
mkdir -p data

profile=$("$PY" -c "from backend.config import Config; print(Config.local().communication_version)") || { echo "TRUSTA configuration failed." >&2; exit 1; }

pids=()
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if [ "$(echo "$profile" | tr -d '[:space:]')" = "3" ]; then
  "$PY" -m scripts.audit_worker --watch >data/audit-worker.log 2>data/audit-worker-error.log & pids+=($!)
  "$PY" -m scripts.scheduled_worker --watch >data/scheduled-worker.log 2>data/scheduled-worker-error.log & pids+=($!)
fi

feishu=$("$PY" -c "from backend.config import Config; Config.local(); from backend.channels.feishu import FeishuConfig; c=FeishuConfig.from_env(); print('1' if c.enabled and c.mode=='longconn' else '0')" 2>/dev/null || echo 0)
if [ "$(echo "$feishu" | tr -d '[:space:]')" = "1" ]; then
  "$PY" -m scripts.feishu_worker >data/feishu-worker.log 2>data/feishu-worker-error.log & pids+=($!)
  echo "飞书长连接已启动（日志：data/feishu-worker.log）。"
fi

echo "另开一个终端运行：$PY -m scripts.chat_cli   即可在终端里和助手对话。"
"$PY" -m backend.server
