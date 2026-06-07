#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT="18121"
NAME="${YOUTUBE_WIKI_PUBLIC_NAME:-youtube-wiki}"
ENDPOINT=""
REPO="${WIKI_GITHUB_REPO:-}"
RUNTIME_ID="${YOUTUBE_WIKI_RUNTIME_ID:-youtube-wiki}"
UI_URL="${SOP_UI_URL:-https://sop-ui.chxyka.ccwu.cc}"
AUTO_DOMAIN_SERVER="${AUTO_DOMAIN_SERVER:-wss://tunnel-api.chxyka.ccwu.cc}"
AGENT_URL="${AGENT_URL:-https://skill.vyibc.com/agent.js}"
AUTO_DOMAIN_SCRIPT="${AUTO_DOMAIN_SCRIPT:-$HOME/auto-domain-cli/skills/auto-domain/scripts/run.sh}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --port=*) PORT="${1#--port=}" ; shift ;;
    --name=*) NAME="${1#--name=}" ; shift ;;
    --endpoint=*) ENDPOINT="${1#--endpoint=}" ; shift ;;
    --repo=*) REPO="${1#--repo=}" ; shift ;;
    --runtime-id=*) RUNTIME_ID="${1#--runtime-id=}" ; shift ;;
    --ui-url=*) UI_URL="${1#--ui-url=}" ; shift ;;
    -h|--help)
      cat <<'EOF'
Usage:
  scripts/setup-service.sh --name=youtube-wiki --repo=owner/repo [--port=18121]

Starts the local bridge and registers the auto-domain tunnel with JSON metadata.
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

[ -n "$NAME" ] || { echo "--name is required" >&2; exit 2; }
[ -n "$REPO" ] || { echo "--repo is required" >&2; exit 2; }
if [ -z "$ENDPOINT" ]; then
  ENDPOINT="https://${NAME}.chxyka.ccwu.cc"
fi

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "node is required" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

bash "$SCRIPT_DIR/start-local-service.sh" --stop >/dev/null 2>&1 || true
bash "$SCRIPT_DIR/start-local-service.sh" --port="$PORT" --daemon
if ! curl -sf "http://127.0.0.1:$PORT" >/dev/null; then
  echo "bridge did not start on port $PORT" >&2
  exit 1
fi

METADATA="$(
  python3 - "$NAME" "$ENDPOINT" "$REPO" "$RUNTIME_ID" "$UI_URL" <<'PY'
import json, sys

name, endpoint, repo, runtime_id, ui_url = sys.argv[1:]
print(json.dumps({
    "title": name,
    "type": "sop-runtime",
    "runtime_id": runtime_id,
    "channel_name": name,
    "channel_url": endpoint,
    "spi_base_url": f"{endpoint.rstrip('/')}/api/sop",
    "supported_sop_types": ["youtube-research-wiki"],
    "ui_url": ui_url,
    "endpoint_url": endpoint,
    "wiki_repo": repo,
    "skill_install_command": "bash <(curl -fsSL 'https://skill.vyibc.com/install-youtube-wiki.sh?ts=20260601121037')",
    "trigger_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=trigger --repo={repo} --url='https://www.youtube.com/watch?v=dQw4w9WgXcQ'",
    "status_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=status --repo={repo} --pipeline-id='<pipeline_id>'",
    "list_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=list",
}, ensure_ascii=False))
PY
)"

cleanup_auto_domain() {
  local pattern="$1"
  if pgrep -af "$pattern" >/dev/null 2>&1; then
    pkill -f "$pattern" || true
  fi
}

cleanup_auto_domain "--name=$NAME"
cleanup_auto_domain "agent.js .*--name=$NAME"

if [ -f "$AUTO_DOMAIN_SCRIPT" ]; then
  echo "[setup-service] using local auto-domain-cli runner: $AUTO_DOMAIN_SCRIPT"
  bash "$AUTO_DOMAIN_SCRIPT" --stop >/dev/null 2>&1 || true
  bash "$AUTO_DOMAIN_SCRIPT" \
    --port="$PORT" \
    --name="$NAME" \
    --replace \
    --daemon \
    --metadata="$METADATA" \
    --server="$AUTO_DOMAIN_SERVER"
  for _ in $(seq 1 40); do
    if [ -f "$HOME/.auto-domain/agent.log" ] && grep -q "Public URL" "$HOME/.auto-domain/agent.log" 2>/dev/null; then
      echo "Public URL : https://$NAME.chxyka.ccwu.cc"
      echo "Logs: $HOME/.auto-domain/agent.log"
      exit 0
    fi
    if [ -f "$HOME/.auto-domain/agent.pid" ] && ! kill -0 "$(cat "$HOME/.auto-domain/agent.pid")" 2>/dev/null; then
      echo "auto-domain daemon exited before ready" >&2
      tail -n 80 "$HOME/.auto-domain/agent.log" >&2 || true
      exit 1
    fi
    if [ -f "$HOME/.auto-domain/agent.log" ] && grep -q "WebSocket error" "$HOME/.auto-domain/agent.log" 2>/dev/null; then
      # keep retrying, but surface logs when not recoverable
      sleep 1
      continue
    fi
    sleep 1
  done
  echo "timed out waiting for public channel (auto-domain-cli)" >&2
  tail -n 120 "$HOME/.auto-domain/agent.log" >&2 || true
  exit 1
fi

CHANNEL_DIR="$HOME/.auto-domain-$NAME"
mkdir -p "$CHANNEL_DIR"
curl -fsSL "$AGENT_URL" -o "$CHANNEL_DIR/agent.js"
python3 - "$CHANNEL_DIR/agent.js" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "body: (method !== 'GET' && method !== 'HEAD') ? Buffer.from(body, 'base64') : undefined,"
new = "body: (method !== 'GET' && method !== 'HEAD' && body) ? Buffer.from(body, 'base64') : undefined,"
if old in text:
    path.write_text(text.replace(old, new), encoding="utf-8")
PY
printf '%s\n' '{"name":"auto-domain-youtube-wiki","private":true,"dependencies":{"ws":"^8.18.0"}}' > "$CHANNEL_DIR/package.json"
(cd "$CHANNEL_DIR" && npm install --silent --prefer-offline)

if [ -f "$CHANNEL_DIR/agent.pid" ] && kill -0 "$(cat "$CHANNEL_DIR/agent.pid")" 2>/dev/null; then
  kill "$(cat "$CHANNEL_DIR/agent.pid")" || true
  sleep 1
fi

(
  cd "$CHANNEL_DIR"
  setsid node agent.js \
    --port="$PORT" \
    --name="$NAME" \
    --replace \
    --metadata="$METADATA" \
    --server="$AUTO_DOMAIN_SERVER" \
    >> agent.log 2>&1 < /dev/null &
  echo $! > agent.pid
)

for _ in $(seq 1 30); do
  if grep -q "Public URL" "$CHANNEL_DIR/agent.log" 2>/dev/null; then
    echo "Public channel ready: $ENDPOINT"
    echo "Logs: $CHANNEL_DIR/agent.log"
    exit 0
  fi
  if ! kill -0 "$(cat "$CHANNEL_DIR/agent.pid")" 2>/dev/null; then
    tail -80 "$CHANNEL_DIR/agent.log" >&2 || true
    exit 1
  fi
  sleep 1
done

tail -80 "$CHANNEL_DIR/agent.log" >&2 || true
echo "timed out waiting for public channel" >&2
exit 1
