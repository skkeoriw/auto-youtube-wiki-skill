#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT="18121"
NAME="${YOUTUBE_WIKI_PUBLIC_NAME:-youtube-wiki}"
ENDPOINT=""
REPO="${WIKI_GITHUB_REPO:-}"
AUTO_DOMAIN_SERVER="${AUTO_DOMAIN_SERVER:-wss://tunnel-api.chxyka.ccwu.cc}"
AGENT_URL="${AGENT_URL:-https://skill.vyibc.com/agent.js}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --port=*) PORT="${1#--port=}" ; shift ;;
    --name=*) NAME="${1#--name=}" ; shift ;;
    --endpoint=*) ENDPOINT="${1#--endpoint=}" ; shift ;;
    --repo=*) REPO="${1#--repo=}" ; shift ;;
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
curl -sf "http://127.0.0.1:$PORT" >/dev/null || {
  echo "bridge did not start on port $PORT" >&2
  exit 1
}

CHANNEL_DIR="$HOME/.auto-domain-$NAME"
mkdir -p "$CHANNEL_DIR"
curl -fsSL "$AGENT_URL" -o "$CHANNEL_DIR/agent.js"
printf '%s\n' '{"name":"auto-domain-youtube-wiki","private":true,"dependencies":{"ws":"^8.18.0"}}' > "$CHANNEL_DIR/package.json"
(cd "$CHANNEL_DIR" && npm install --silent --prefer-offline)

if [ -f "$CHANNEL_DIR/agent.pid" ] && kill -0 "$(cat "$CHANNEL_DIR/agent.pid")" 2>/dev/null; then
  kill "$(cat "$CHANNEL_DIR/agent.pid")" || true
  sleep 1
fi

METADATA="$(
  python3 - "$NAME" "$ENDPOINT" "$REPO" <<'PY'
import json, sys

name, endpoint, repo = sys.argv[1:]
print(json.dumps({
    "title": name,
    "endpoint_url": endpoint,
    "wiki_repo": repo,
    "skill_install_command": "bash <(curl -fsSL 'https://skill.vyibc.com/install-youtube-wiki.sh?ts=20260601121037')",
    "trigger_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=trigger --repo={repo} --url='https://www.youtube.com/watch?v=dQw4w9WgXcQ'",
    "status_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=status --repo={repo} --pipeline-id='<pipeline_id>'",
    "list_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=list",
}, ensure_ascii=False))
PY
)"

: > "$CHANNEL_DIR/agent.log"
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
