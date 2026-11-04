#!/usr/bin/env bash
# First install and subsequent launches use the same entry point.
set -u
cd -- "$(dirname -- "$0")" || exit 1
for candidate in python3 python python3.13 python3.12; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys, venv, ensurepip; raise SystemExit(sys.implementation.name != "cpython" or sys.version_info[:2] not in ((3,12),(3,13)))' >/dev/null 2>&1; then
    exec "$candidate" scripts/launch_app.py "$@"
  fi
done
printf '%s\n' '没有找到包含 venv/ensurepip 的 CPython 3.12 或 3.13。请从 https://www.python.org/downloads/ 安装后重试。' >&2
printf '%s\n' 'Debian/Ubuntu 如已安装 Python，还可能需要安装对应版本的 python3-venv。' >&2
printf '%s\n' 'Python 3.11 与当前 scipy/networkx 版本不兼容；无需删除场景或结果。' >&2
exit 1
