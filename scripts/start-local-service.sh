#!/usr/bin/env bash
set -euo pipefail

PORT="18121"
DAEMON=0
SERVICE_NAME=""
SERVICE_MODE=""
HOME_DIR="${HOME:-/root}"
export HOME="$HOME_DIR"

PID_FILE="$HOME_DIR/.youtube-wiki-bridge.pid"
LOG_FILE="$HOME_DIR/.youtube-wiki-bridge.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

systemd_supported() {
  if command -v systemctl >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

systemd_scope_is_system() {
  [[ -w /etc/systemd/system ]]
}

systemd_scope() {
  if systemd_scope_is_system; then
    echo "system"
  else
    echo "user"
  fi
}

normalize_service_name() {
  local raw="$1"
  local normalized
  normalized="$(echo "$raw" | sed -E 's/[^a-zA-Z0-9_.-]+/-/g' | sed -E 's/^-+|[^a-zA-Z0-9]+$//g' | sed -E 's/-+/-/g')"
  if [[ -z "$normalized" ]]; then
    normalized="youtube-wiki"
  fi
  echo "$normalized"
}

service_name() {
  local base="${SERVICE_NAME:-${YOUTUBE_WIKI_PUBLIC_NAME:-youtube-wiki}}"
  base="${base%.service}"
  case "$base" in
    youtube-wiki-bridge-*) base="${base#youtube-wiki-bridge-}" ;;
    '') base="youtube-wiki" ;;
  esac
  echo "youtube-wiki-bridge-$(normalize_service_name "$base")"
}

service_unit_path() {
  local name="$1"
  if systemd_scope_is_system; then
    echo "/etc/systemd/system/${name}.service"
  else
    echo "$HOME_DIR/.config/systemd/user/${name}.service"
  fi
}

systemd_unit_name() {
  echo "$(service_name).service"
}

systemctl_call() {
  local action="$1"; shift
  if systemd_scope_is_system; then
    systemctl "$action" "$@"
  else
    systemctl --user "$action" "$@"
  fi
}

systemctl_is_active() {
  local name="$1"
  if systemd_scope_is_system; then
    systemctl is-active --quiet "$name" 2>/dev/null
  else
    systemctl --user is-active --quiet "$name" 2>/dev/null
  fi
}

systemctl_is_enabled() {
  local name="$1"
  if systemd_scope_is_system; then
    systemctl is-enabled --quiet "$name" 2>/dev/null
  else
    systemctl --user is-enabled --quiet "$name" 2>/dev/null
  fi
}

systemd_unit_exists() {
  local path="$1"
  [[ -f "$path" ]]
}

wait_bridge_ready() {
  local label="${1:-bridge}"
  local tries="${2:-30}"
  for _ in $(seq 1 "$tries"); do
    if curl -sf -X GET "http://127.0.0.1:$PORT" -o /dev/null 2>&1; then
      echo "$label ready: http://127.0.0.1:$PORT"
      return 0
    fi
    sleep 0.5
  done
  echo "$label did not become ready on port $PORT." >&2
  if [[ -f "$LOG_FILE" ]]; then
    tail -n 80 "$LOG_FILE" >&2 || true
  fi
  return 1
}

install_service() {
  local name path wanted_by
  name="$(systemd_unit_name)"
  path="$(service_unit_path "${name%.service}")"

  if ! systemd_supported; then
    echo "systemd not available; skip service install." >&2
    return 1
  fi

  if ! systemd_scope_is_system; then
    mkdir -p "$HOME_DIR/.config/systemd/user"
  fi
  mkdir -p "$(dirname "$path")"

cat > "$path" <<EOF
[Unit]
Description=YouTube Wiki Bridge ($name)
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
EnvironmentFile=-$HOME_DIR/.agent-brain-plugins.env
Environment=HOME=$HOME_DIR
ExecStart=$SCRIPT_DIR/start-local-service.sh --name=${name%.service} --port=$PORT --foreground
Restart=always
RestartSec=5
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=20
TimeoutStartSec=20
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE

[Install]
EOF

if systemd_scope_is_system; then
  wanted_by="multi-user.target"
else
  wanted_by="default.target"
fi

cat >> "$path" <<EOF
WantedBy=$wanted_by
EOF

systemctl_call daemon-reload >/dev/null
systemctl_call enable "$name" >/dev/null
echo "Installed service: $path"
return 0
}

start_service() {
  local name
  name="$(systemd_unit_name)"

  if ! systemd_supported; then
    echo "systemd not available, fallback to daemon mode." >&2
    bash "$SCRIPT_DIR/start-local-service.sh" --port="$PORT" --daemon
    return 0
  fi

  if ! systemd_unit_exists "$(service_unit_path "${name%.service}")"; then
    install_service
  fi
  systemctl_call start "$name"
  wait_bridge_ready "$name"
}

stop_service() {
  local name
  name="$(systemd_unit_name)"

  if ! systemd_supported; then
    stop_bridge
    return 0
  fi

  systemctl_call stop "$name" >/dev/null 2>&1 || true
  stop_bridge
}

restart_service() {
  local name
  name="$(systemd_unit_name)"

  if ! systemd_supported; then
    bash "$SCRIPT_DIR/start-local-service.sh" --stop >/dev/null 2>&1 || true
    bash "$SCRIPT_DIR/start-local-service.sh" --port="$PORT" --daemon
    return 0
  fi

  if ! systemd_unit_exists "$(service_unit_path "${name%.service}")"; then
    install_service
  fi
  systemctl_call restart "$name" >/dev/null
  wait_bridge_ready "$name"
}

status_service() {
  local name unit
  name="$(systemd_unit_name)"
  unit="$(service_unit_path "${name%.service}")"

  if systemd_supported && systemd_unit_exists "$unit"; then
    if systemctl_is_active "$name"; then
      if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Bridge service is active: $name (PID: $(cat "$PID_FILE"))"
      else
        echo "Bridge service is active: $name"
      fi
      systemctl_is_enabled "$name" && echo "Service enabled: yes" || echo "Service enabled: no"
      return 0
    fi
    echo "Bridge service is inactive: $name"
    systemctl_call --no-pager -l status "$name" 2>/dev/null || true
    return 2
  fi

  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Bridge process running: $(cat "$PID_FILE")"
    return 0
  fi
  if curl -sf -X GET "http://127.0.0.1:$PORT" -o /dev/null 2>&1; then
    echo "Bridge endpoint online"
    return 0
  fi
  echo "Bridge is not running"
  return 1
}

uninstall_service() {
  local name unit
  name="$(systemd_unit_name)"
  unit="$(service_unit_path "${name%.service}")"
  if ! systemd_supported; then
    echo "systemd not available; skip uninstall." >&2
    stop_bridge
    return 0
  fi
  systemctl_call stop "$name" >/dev/null 2>&1 || true
  systemctl_call disable "$name" >/dev/null 2>&1 || true
  rm -f "$unit"
  systemctl_call daemon-reload >/dev/null 2>&1 || true
  echo "Removed service definition: $unit"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port=*) PORT="${1#--port=}"; shift ;;
    --name=*) SERVICE_NAME="${1#--name=}"; shift ;;
    --daemon) DAEMON=1; shift ;;
    --foreground) DAEMON=0; shift ;;
    --install-service) SERVICE_MODE="install-service"; shift ;;
    --start-service) SERVICE_MODE="start-service"; shift ;;
    --stop-service) SERVICE_MODE="stop-service"; shift ;;
    --restart-service) SERVICE_MODE="restart-service"; shift ;;
    --status-service) SERVICE_MODE="status-service"; shift ;;
    --uninstall-service) SERVICE_MODE="uninstall-service"; shift ;;
    --stop)
      stop_bridge
      exit 0
      ;;
    *) echo "unknown: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$SERVICE_MODE" ]]; then
  case "$SERVICE_MODE" in
    install-service) install_service; exit $? ;;
    start-service) start_service; exit $? ;;
    stop-service) stop_service; exit $? ;;
    restart-service) restart_service; exit $? ;;
    status-service) status_service; exit $? ;;
    uninstall-service) uninstall_service; exit $? ;;
  esac
fi

BRIDGE_PY="$SCRIPT_DIR/bridge.py"
LOCAL_SCRIPT="$SCRIPT_DIR/local-script.sh"

if [[ ! -f "$BRIDGE_PY" ]]; then
  echo "bridge.py not found at $BRIDGE_PY" >&2; exit 1
fi
if [[ ! -f "$LOCAL_SCRIPT" ]]; then
  echo "local-script.sh not found at $LOCAL_SCRIPT" >&2; exit 1
fi

ENV_FILE="${YOUTUBE_WIKI_ENV_FILE:-$HOME_DIR/.agent-brain-plugins.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

normalize_managed_hermes_host() {
  local zone="${AUTO_DOMAIN_ZONE_NAME:-chxyka.ccwu.cc}"
  local host="${WEBHOOK_PUBLIC_HOST:-}"
  local public_name="${HERMES_PUBLIC_NAME:-}"

  host="${host#http://}"
  host="${host#https://}"
  host="${host%%/*}"

  if [[ -z "$public_name" ]]; then
    if [[ -n "${YOUTUBE_WIKI_PUBLIC_NAME:-}" ]]; then
      public_name="hermes-${YOUTUBE_WIKI_PUBLIC_NAME}"
    elif [[ -n "${YOUTUBE_WIKI_RUNTIME_ID:-}" ]]; then
      public_name="hermes-${YOUTUBE_WIKI_RUNTIME_ID}"
    fi
  fi

  if [[ -n "$host" && -n "$public_name" && -z "${HERMES_WEBHOOK_URL:-}" && "$host" != *".${zone}" ]]; then
    export HERMES_PUBLIC_NAME="$public_name"
    export WEBHOOK_PUBLIC_HOST="${public_name}.${zone}"
  fi
}

normalize_managed_hermes_host

export BRIDGE_PORT BRIDGE_SCRIPT BRIDGE_PY_FILE
BRIDGE_PORT="$PORT"
BRIDGE_SCRIPT="$LOCAL_SCRIPT"
BRIDGE_PY_FILE="$BRIDGE_PY"

if [[ "$DAEMON" == "1" ]]; then
  > "$LOG_FILE"
  nohup python3 "$BRIDGE_PY_FILE" >> "$LOG_FILE" 2>&1 &
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
      echo "Bridge ready: http://127.0.0.1:$PORT"
      exit 0
    fi
    sleep 0.5
  done
  echo "Bridge did not become ready on port $PORT." >&2
  tail -n 80 "$LOG_FILE" >&2 || true
  exit 1
else
  exec python3 "$BRIDGE_PY_FILE"
fi
