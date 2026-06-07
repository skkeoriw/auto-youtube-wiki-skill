#!/usr/bin/env bash
set -euo pipefail

PORT="18121"
DAEMON=0
PID_FILE="$HOME/.youtube-wiki-bridge.pid"
LOG_FILE="$HOME/.youtube-wiki-bridge.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stop_bridge() {
  local stopped=0

  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    stopped=1
  fi
  rm -f "$PID_FILE"

  if command -v fuser >/dev/null 2>&1 && fuser "$PORT/tcp" >/dev/null 2>&1; then
    fuser -k "$PORT/tcp" >/dev/null 2>&1 || true
    stopped=1
  fi

  for _ in $(seq 1 20); do
    if ! curl -sf -X GET "http://127.0.0.1:$PORT" -o /dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done

  if [[ "$stopped" == "1" ]]; then
    echo "Bridge stopped."
  else
    echo "No bridge running."
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port=*) PORT="${1#--port=}"; shift ;;
    --daemon) DAEMON=1; shift ;;
    --stop)
      stop_bridge
      exit 0
      ;;
    *) echo "unknown: $1" >&2; exit 1 ;;
  esac
done

BRIDGE_PY="$SCRIPT_DIR/bridge.py"
LOCAL_SCRIPT="$SCRIPT_DIR/local-script.sh"

if [[ ! -f "$BRIDGE_PY" ]]; then
  echo "bridge.py not found at $BRIDGE_PY" >&2; exit 1
fi
if [[ ! -f "$LOCAL_SCRIPT" ]]; then
  echo "local-script.sh not found at $LOCAL_SCRIPT" >&2; exit 1
fi

export BRIDGE_PORT BRIDGE_SCRIPT BRIDGE_PY_FILE
BRIDGE_PORT="$PORT"
BRIDGE_SCRIPT="$LOCAL_SCRIPT"
BRIDGE_PY_FILE="$BRIDGE_PY"

if [[ "$DAEMON" == "1" ]]; then
  > "$LOG_FILE"
  nohup bash -lc 'python3 "$BRIDGE_PY_FILE"' >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "Bridge started on port $PORT (PID: $(cat "$PID_FILE"))"
  echo "  Logs : tail -f $LOG_FILE"
  for i in $(seq 1 20); do
    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Bridge exited before becoming ready." >&2
      tail -n 80 "$LOG_FILE" >&2 || true
      rm -f "$PID_FILE"
      exit 1
    fi
    if curl -sf -X GET "http://127.0.0.1:$PORT" -o /dev/null 2>&1; then
      echo "  Ready: http://127.0.0.1:$PORT"
      exit 0
    fi
    sleep 0.5
  done
  echo "Bridge did not become ready on port $PORT." >&2
  tail -n 80 "$LOG_FILE" >&2 || true
  exit 1
else
  exec bash -lc 'python3 "$BRIDGE_PY_FILE"'
fi
