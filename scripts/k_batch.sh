#!/usr/bin/env bash
# 安全批跑入口：不执行 Git 恢复，不改写配置/报价/曲线，不复用历史输出。
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${K_PYTHON:-"$ROOT/.venv/bin/python"}
if [ ! -x "$PYTHON" ]; then
  printf '未找到可执行 Python：%s\n请安装开发环境，或用 K_PYTHON 指定解释器。\n' "$PYTHON" >&2
  exit 2
fi
exec "$PYTHON" "$ROOT/scripts/k_batch.py" "$@"
