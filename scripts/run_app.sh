#!/usr/bin/env bash
# Developer-only background entry. Never signal an unverified legacy PID.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "未找到开发环境 .venv/bin/python；普通用户请使用 bash start.sh。" >&2
  exit 1
fi
export TZ="${TZ:-America/Los_Angeles}"
exec .venv/bin/python scripts/manage_app.py "$@"
