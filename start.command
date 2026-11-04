#!/usr/bin/env bash
# Finder double-click entry; keep failures visible instead of closing the window.
cd -- "$(dirname -- "$0")" || exit 1
bash ./start.sh "$@"
status=$?
if [ "$status" -ne 0 ]; then
  printf '\n启动未完成。请保留上方错误，按回车关闭窗口。\n'
  read -r _ || true
fi
exit "$status"
