#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python scripts/launch.py "$@"
fi
exec python3 scripts/launch.py "$@"
