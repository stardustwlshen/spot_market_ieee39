#!/usr/bin/env bash
# 启动 Streamlit 网页应用（后台 nohup，长期运行）。
#   bash scripts/run_app.sh            # 启动（已在运行则跳过）
#   bash scripts/run_app.sh restart    # 重启
#   bash scripts/run_app.sh stop       # 停止
#   PORT=8502 bash scripts/run_app.sh  # 换端口
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8501}"
ADDR="${ADDR:-0.0.0.0}"
PIDF="outputs/app.pid"
LOG="outputs/app.log"
mkdir -p outputs
export TZ="${TZ:-America/Los_Angeles}"   # 运行目录时间戳、日志时间用本地时区

stop() {
  if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    kill "$(cat "$PIDF")" && echo "已停止 PID $(cat "$PIDF")"
    sleep 1
  fi
  rm -f "$PIDF"
}

case "${1:-start}" in
  stop) stop; exit 0 ;;
  restart) stop ;;
esac

if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "已在运行：PID $(cat "$PIDF")，http://${ADDR}:${PORT}"
  exit 0
fi
. .venv/bin/activate
nohup streamlit run "app/主页.py" --server.address "$ADDR" --server.port "$PORT" --server.headless true \
  --browser.gatherUsageStats false --server.fileWatcherType none > "$LOG" 2>&1 &
echo $! > "$PIDF"
for i in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:${PORT}/_stcore/health" >/dev/null 2>&1; then
    echo "已启动：PID $(cat "$PIDF")，本机访问 http://127.0.0.1:${PORT}（监听 ${ADDR}），日志 $LOG"
    exit 0
  fi
  sleep 1
done
echo "启动超时，见 $LOG"; tail -20 "$LOG"; exit 1
