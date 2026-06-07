#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT="18121"
NAME="${YOUTUBE_WIKI_PUBLIC_NAME:-youtube-wiki}"
ENDPOINT=""
REPO="${WIKI_GITHUB_REPO:-}"
RUNTIME_ID="${YOUTUBE_WIKI_RUNTIME_ID:-youtube-wiki}"
UI_URL="${SOP_UI_URL:-https://sop-ui-prototype.chxyka.ccwu.cc}"
AUTO_DOMAIN_SERVER="${AUTO_DOMAIN_SERVER:-wss://tunnel-api.chxyka.ccwu.cc}"
AUTO_DOMAIN_ZONE_NAME="${AUTO_DOMAIN_ZONE_NAME:-chxyka.ccwu.cc}"
AUTO_DOMAIN_WORKER_SCRIPT="${AUTO_DOMAIN_WORKER_SCRIPT:-auto-domain-tunnel}"
AUTO_DOMAIN_REPO="${AUTO_DOMAIN_REPO:-https://github.com/skkeoriw/auto-domain-cli.git}"
AUTO_DOMAIN_REF="${AUTO_DOMAIN_REF:-main}"
AUTO_DOMAIN_SOURCE_DIR="${AUTO_DOMAIN_SOURCE_DIR:-$HOME/.cache/youtube-wiki/auto-domain-cli}"
AUTO_DOMAIN_SCRIPT="${AUTO_DOMAIN_SCRIPT:-}"
AUTO_DOMAIN_ALLOW_LOCAL_RUNNER="${AUTO_DOMAIN_ALLOW_LOCAL_RUNNER:-0}"
PUBLIC_VERIFY_PATH="${PUBLIC_VERIFY_PATH:-/api/sop}"

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
command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "node is required" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

if [ -n "${CF_API_KEY:-${CLOUDFLARE_API_KEY:-}}" ] && [ -n "${CF_EMAIL:-${CLOUDFLARE_EMAIL:-}}" ]; then
  bash "$SCRIPT_DIR/ensure-cloudflare-tunnel-routes.sh" \
    --zone-name="$AUTO_DOMAIN_ZONE_NAME" \
    --worker-script="$AUTO_DOMAIN_WORKER_SCRIPT"
else
  echo "[setup-service] Cloudflare route ensure skipped: CF_EMAIL/CF_API_KEY not set"
fi

bash "$SCRIPT_DIR/start-local-service.sh" --stop >/dev/null 2>&1 || true
bash "$SCRIPT_DIR/start-local-service.sh" --port="$PORT" --daemon
if ! curl -sf "http://127.0.0.1:$PORT" >/dev/null; then
  echo "bridge did not start on port $PORT" >&2
  exit 1
fi

AUTO_DOMAIN_SOURCE_MODE=""
AUTO_DOMAIN_SOURCE_REPO=""
AUTO_DOMAIN_SOURCE_REF=""
AUTO_DOMAIN_SOURCE_COMMIT=""
METADATA=""

build_metadata() {
  python3 - "$NAME" "$ENDPOINT" "$REPO" "$RUNTIME_ID" "$UI_URL" \
    "$AUTO_DOMAIN_SOURCE_MODE" "$AUTO_DOMAIN_SOURCE_REPO" "$AUTO_DOMAIN_SOURCE_REF" "$AUTO_DOMAIN_SOURCE_COMMIT" <<'PY'
import json, sys

(
    name,
    endpoint,
    repo,
    runtime_id,
    ui_url,
    source_mode,
    source_repo,
    source_ref,
    source_commit,
) = sys.argv[1:]
metadata = {
    "title": name,
    "type": "sop-runtime",
    "runtime_id": runtime_id,
    "channel_name": name,
    "channel_url": endpoint,
    "spi_base_url": f"{endpoint.rstrip('/')}/api/sop",
    "supported_sop_types": ["runtime-provisioning", "youtube-research-wiki"],
    "ui_url": ui_url,
    "endpoint_url": endpoint,
    "wiki_repo": repo,
    "skill_install_command": "bash <(curl -fsSL 'https://skill.vyibc.com/install-youtube-wiki.sh?ts=20260601121037')",
    "trigger_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=trigger --repo={repo} --url='https://www.youtube.com/watch?v=dQw4w9WgXcQ'",
    "status_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=status --repo={repo} --pipeline-id='<pipeline_id>'",
    "list_command": f"bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --endpoint={endpoint} --mode=list",
}
if source_mode:
    metadata["auto_domain_source"] = {
        "mode": source_mode,
        "repo": source_repo,
        "ref": source_ref,
        "commit": source_commit,
    }
print(json.dumps(metadata, ensure_ascii=False))
PY
}

cleanup_auto_domain() {
  local pattern="$1"
  if pgrep -af "$pattern" >/dev/null 2>&1; then
    pkill -f "$pattern" || true
  fi
}

auto_domain_script_supports_safe_metadata() {
  local file="$1"
  [ -f "$file" ] || return 1
  grep -q 'ARGS=(' "$file" && grep -q '"${ARGS\[@\]}"' "$file"
}

prepare_auto_domain_source_agent() {
  local source_dir="$AUTO_DOMAIN_SOURCE_DIR"
  local tmp_dir="${source_dir}.tmp"
  local agent_js="$source_dir/skills/auto-domain/agent/agent.js"

  mkdir -p "$(dirname "$source_dir")"

  if [ -e "$source_dir" ] && [ ! -d "$source_dir/.git" ]; then
    echo "[setup-service] auto-domain source cache is not a git repo; rebuilding: $source_dir" >&2
    rm -rf "$source_dir"
  fi

  if [ -d "$source_dir/.git" ]; then
    local current_repo
    current_repo="$(git -C "$source_dir" remote get-url origin 2>/dev/null || true)"
    if [ "$current_repo" != "$AUTO_DOMAIN_REPO" ]; then
      echo "[setup-service] auto-domain source cache repo changed; rebuilding: ${current_repo:-unknown} -> $AUTO_DOMAIN_REPO" >&2
      rm -rf "$source_dir"
    fi
  fi

  if [ -d "$source_dir/.git" ]; then
    git -C "$source_dir" remote set-url origin "$AUTO_DOMAIN_REPO"
    git -C "$source_dir" fetch --quiet --depth 1 origin "$AUTO_DOMAIN_REF"
    git -C "$source_dir" checkout --quiet -B "$AUTO_DOMAIN_REF" FETCH_HEAD
    git -C "$source_dir" reset --quiet --hard FETCH_HEAD
    git -C "$source_dir" clean --quiet -ffd
  else
    rm -rf "$tmp_dir"
    git clone --quiet --depth 1 --branch "$AUTO_DOMAIN_REF" "$AUTO_DOMAIN_REPO" "$tmp_dir"
    rm -rf "$source_dir"
    mv "$tmp_dir" "$source_dir"
  fi

  local actual_repo
  actual_repo="$(git -C "$source_dir" remote get-url origin 2>/dev/null || true)"
  if [ "$actual_repo" != "$AUTO_DOMAIN_REPO" ]; then
    echo "[setup-service] auto-domain source repo mismatch: ${actual_repo:-unknown}, expected $AUTO_DOMAIN_REPO" >&2
    return 1
  fi

  local dirty
  dirty="$(git -C "$source_dir" status --short 2>/dev/null || true)"
  if [ -n "$dirty" ]; then
    echo "[setup-service] auto-domain source cache is dirty after sync: $source_dir" >&2
    printf '%s\n' "$dirty" >&2
    return 1
  fi

  [ -f "$agent_js" ] || {
    echo "[setup-service] auto-domain agent source not found: $agent_js" >&2
    return 1
  }

  local commit
  commit="$(git -C "$source_dir" rev-parse --short HEAD 2>/dev/null || true)"
  echo "[setup-service] using latest auto-domain source: $AUTO_DOMAIN_REPO@$commit" >&2
  printf '%s\n' "$agent_js"
}

verify_public_channel() {
  local url="${ENDPOINT%/}${PUBLIC_VERIFY_PATH}"
  echo "[setup-service] verifying public channel: $url"
  for _ in $(seq 1 20); do
    if curl -fsS --connect-timeout 8 --max-time 20 "$url" >/dev/null 2>&1; then
      echo "[setup-service] public channel verified: $url"
      return 0
    fi
    sleep 2
  done

  echo "[setup-service] public channel verification failed: $url" >&2
  curl -k -i --connect-timeout 8 --max-time 20 "$url" >&2 || true
  return 1
}

verify_runtime_channel() {
  local verifier="$SCRIPT_DIR/verify-runtime-channel.sh"
  [ -x "$verifier" ] || {
    echo "[setup-service] runtime channel verifier not found or not executable: $verifier" >&2
    return 1
  }

  echo "[setup-service] verifying runtime channel metadata: $NAME"
  for _ in $(seq 1 10); do
    local args=(
      --name="$NAME" \
      --endpoint="$ENDPOINT" \
      --expect-runtime-id="$RUNTIME_ID" \
      --expect-repo="$REPO" \
      --expect-ui-url="$UI_URL" \
      --expect-port="$PORT"
    )
    if [ -n "$AUTO_DOMAIN_SOURCE_MODE" ]; then
      args+=(--expect-auto-domain-source-mode="$AUTO_DOMAIN_SOURCE_MODE")
    fi
    if [ -n "$AUTO_DOMAIN_SOURCE_REPO" ]; then
      args+=(--expect-auto-domain-source-repo="$AUTO_DOMAIN_SOURCE_REPO")
    fi
    if [ -n "$AUTO_DOMAIN_SOURCE_COMMIT" ]; then
      args+=(--expect-auto-domain-source-commit="$AUTO_DOMAIN_SOURCE_COMMIT")
    fi
    if "$verifier" "${args[@]}"; then
      echo "[setup-service] runtime channel metadata verified: $NAME"
      return 0
    fi
    sleep 2
  done

  echo "[setup-service] runtime channel metadata verification failed: $NAME" >&2
  return 1
}

fix_agent_ws_host() {
  local file="$1"
  [ -f "$file" ] || return 0
  python3 - "$file" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding='utf-8', errors='ignore')

old = re.search(r"async function buildWsUrl\(\) \{.*?\n\}\n\n// ── Connect", text, flags=re.S)
if not old:
  print(f"[setup-service] WARN: buildWsUrl not found in {path}")
  raise SystemExit(0)

replacement = '''async function buildWsUrl() {
  let host = SERVER.trim();
  let normalized = host;

  try {
    const parsed = new URL(host.includes('://') ? host : `https://${host}`);
    normalized = parsed.hostname;
    if (!normalized) throw new Error('invalid-host');
  } catch {
    normalized = host.replace(/^wss?:\/\/+/, '').replace(/^https?:\/\/+/, '');
    normalized = normalized.split(/[\/?#]/, 1)[0];
    if (!normalized) {
      normalized = 'tunnel-api.chxyka.ccwu.cc';
    }
  }

  const u = new URL(`wss://${normalized}/websocket`);
  if (TOKEN) u.searchParams.set('token', TOKEN);
  u.searchParams.set('port', String(PORT));
  if (NAME) u.searchParams.set('name', NAME);
  if (AUTO_NAME) u.searchParams.set('auto', '1');
  if (METADATA) u.searchParams.set('metadata', METADATA);
  if (REPLACE) u.searchParams.set('replace', '1');

  const ip = await getPublicIPv4();
  if (ip) u.searchParams.set('client_ip', ip);

  return u.toString();
}

// ── Connect'''

text = re.sub(r"async function buildWsUrl\(\) \{.*?\n\}\n\n// ── Connect", replacement, text, flags=re.S)
path.write_text(text, encoding='utf-8')
print(f"[setup-service] fixed buildWsUrl host logic in {path}")
PY
}

cleanup_auto_domain "--name=$NAME"
cleanup_auto_domain "agent.js .*--name=$NAME"

if [ "$AUTO_DOMAIN_ALLOW_LOCAL_RUNNER" = "1" ] && [ -f "$AUTO_DOMAIN_SCRIPT" ] && auto_domain_script_supports_safe_metadata "$AUTO_DOMAIN_SCRIPT"; then
  AUTO_DOMAIN_SOURCE_MODE="local-runner"
  AUTO_DOMAIN_SOURCE_REPO="$AUTO_DOMAIN_SCRIPT"
  AUTO_DOMAIN_SOURCE_REF=""
  AUTO_DOMAIN_SOURCE_COMMIT="$(git -C "$(dirname "$AUTO_DOMAIN_SCRIPT")" rev-parse --short HEAD 2>/dev/null || true)"
  METADATA="$(build_metadata)"
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
    if [ -f "$HOME/.auto-domain/agent.js" ]; then
      # 防御式修复：旧版本自动修正会把 websocket URL 指向子域名，导致 522
      # 统一改回直连 tunnel-api 的逻辑，避免端到端注册链路失败。
      fix_agent_ws_host "$HOME/.auto-domain/agent.js"
    fi
    if [ -f "$HOME/.auto-domain/agent.log" ] && grep -q "Public URL" "$HOME/.auto-domain/agent.log" 2>/dev/null; then
      echo "Public URL : https://$NAME.chxyka.ccwu.cc"
      echo "Logs: $HOME/.auto-domain/agent.log"
      verify_public_channel
      verify_runtime_channel
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
elif [ -f "$AUTO_DOMAIN_SCRIPT" ]; then
  if [ "$AUTO_DOMAIN_ALLOW_LOCAL_RUNNER" = "1" ]; then
    echo "[setup-service] local auto-domain-cli runner is too old for JSON metadata; using managed latest source instead: $AUTO_DOMAIN_SCRIPT"
  else
    echo "[setup-service] local auto-domain-cli runner ignored; using managed latest source: $AUTO_DOMAIN_SCRIPT"
  fi
fi

AUTO_DOMAIN_AGENT_JS="$(prepare_auto_domain_source_agent)"
AUTO_DOMAIN_SOURCE_MODE="managed"
AUTO_DOMAIN_SOURCE_REPO="$AUTO_DOMAIN_REPO"
AUTO_DOMAIN_SOURCE_REF="$AUTO_DOMAIN_REF"
AUTO_DOMAIN_SOURCE_COMMIT="$(git -C "$AUTO_DOMAIN_SOURCE_DIR" rev-parse --short HEAD 2>/dev/null || true)"
METADATA="$(build_metadata)"

CHANNEL_DIR="$HOME/.auto-domain-$NAME"
mkdir -p "$CHANNEL_DIR"
printf '%s\n' '{"name":"auto-domain-youtube-wiki","private":true,"dependencies":{"ws":"^8.18.0"}}' > "$CHANNEL_DIR/package.json"
(cd "$CHANNEL_DIR" && npm install --silent --prefer-offline)

if [ -f "$CHANNEL_DIR/agent.pid" ] && kill -0 "$(cat "$CHANNEL_DIR/agent.pid")" 2>/dev/null; then
  kill "$(cat "$CHANNEL_DIR/agent.pid")" || true
  sleep 1
fi

(
  cd "$CHANNEL_DIR"
  NODE_PATH="$CHANNEL_DIR/node_modules${NODE_PATH:+:$NODE_PATH}" setsid node "$AUTO_DOMAIN_AGENT_JS" \
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
    verify_public_channel
    verify_runtime_channel
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
