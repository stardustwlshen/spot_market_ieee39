#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .streamlit/config.toml && -f config/streamlit_theme.toml ]]; then
  mkdir -p .streamlit
  cp config/streamlit_theme.toml .streamlit/config.toml
fi
export TZ="${TZ:-UTC}"
PYTHON="${PYTHON:-.venv/bin/python}"
exec "$PYTHON" -m streamlit run "app/主页.py" --server.address "${ADDR:-127.0.0.1}" --server.port "${PORT:-8501}" --server.headless true --browser.gatherUsageStats false --server.fileWatcherType none
