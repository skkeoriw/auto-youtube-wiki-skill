#!/usr/bin/env bash
# script service source (auto-domain tunnel)
set -euo pipefail

MODE=""
TOKEN="${YOUTUBE_WIKI_TOKEN:-}"
ENDPOINT_OVERRIDE=""
REPO=""
URL=""
INTENT=""
WATCH=""
TIMEOUT=""
PIPELINE_ID=""
WATCH_ACTIVE=0
API_CONNECT_TIMEOUT=10
REQUEST_TIMEOUT=900
POLL_INTERVAL=3

for arg in "$@"; do
  case "$arg" in
    --mode=*) MODE="${arg#--mode=}" ;;
    --token=*) TOKEN="${arg#--token=}" ;;
    --endpoint=*) ENDPOINT_OVERRIDE="${arg#--endpoint=}" ;;
    --repo=*) REPO="${arg#--repo=}" ;;
    --url=*) URL="${arg#--url=}" ;;
    --intent=*) INTENT="${arg#--intent=}" ;;
    --watch=*) WATCH="${arg#--watch=}" ;;
    --timeout=*) TIMEOUT="${arg#--timeout=}" ;;
    --pipeline-id=*) PIPELINE_ID="${arg#--pipeline-id=}" ;;
    -h|--help)
      echo "Usage: $0 --mode=<mode> [--token=TOKEN] [--endpoint=URL]"
      exit 0
      ;;
  esac
done

# Keep legacy argument auto-inference behavior for compatibility.
if [[ -z "$MODE" ]] && [[ -n "${REPO}" ]]; then
  MODE="init"
fi
if [[ -z "$MODE" ]] && [[ -n "${REPO}" ]] && [[ -n "${URL}" ]] && [[ -n "${INTENT}" ]] && [[ -n "${WATCH}" ]] && [[ -n "${TIMEOUT}" ]]; then
  MODE="trigger"
fi
if [[ -z "$MODE" ]] && [[ -n "${REPO}" ]] && [[ -n "${PIPELINE_ID}" ]] && [[ -n "${WATCH}" ]] && [[ -n "${TIMEOUT}" ]]; then
  MODE="status"
fi
if [[ -z "$MODE" ]] && [[ -n "${URL}" ]] && [[ -n "${TIMEOUT}" ]]; then
  MODE="validate"
fi

if [[ -z "$MODE" ]]; then
  echo "Provide --mode or enough fields to infer one" >&2
  exit 1
fi

TOKEN="${TOKEN#Bearer }"
TOKEN="${TOKEN#bearer }"

ENDPOINT="https://youtube-wiki.chxyka.ccwu.cc"
if [[ -n "$ENDPOINT_OVERRIDE" ]]; then
  ENDPOINT="$ENDPOINT_OVERRIDE"
fi

if [[ "${WATCH,,}" == "true" || "${WATCH,,}" == "1" || "${WATCH,,}" == "yes" || "${WATCH,,}" == "on" ]]; then
  WATCH_ACTIVE=1
fi
if [[ -n "$TIMEOUT" ]]; then
  REQUEST_TIMEOUT="$TIMEOUT"
fi

COMMON_HEADERS=()
if [[ -n "$TOKEN" ]]; then
  COMMON_HEADERS+=(-H "Authorization: Bearer $TOKEN")
fi

request() {
  local method="$1"
  local path="$2"
  local payload="${3:-}"
  local url="${ENDPOINT}${path}"

  if [[ "$method" == "GET" ]]; then
    curl --connect-timeout "$API_CONNECT_TIMEOUT" \
      --max-time "$REQUEST_TIMEOUT" \
      --fail-with-body \
      -sS -L \
      "${COMMON_HEADERS[@]+"${COMMON_HEADERS[@]}"}" \
      "$url"
  else
    curl --connect-timeout "$API_CONNECT_TIMEOUT" \
      --max-time "$REQUEST_TIMEOUT" \
      --fail-with-body \
      -sS -L \
      "${COMMON_HEADERS[@]+"${COMMON_HEADERS[@]}"}" \
      -H "Content-Type: application/json" \
      -X "$method" \
      -d "$payload" \
      "$url"
  fi
}

get_instances_payload() {
  local payload
  if payload="$(request GET "/api/sop/instances" )"; then
    echo "$payload"
    return 0
  fi
  request GET "/api/sop"
}

extract_instance_id() {
  local target="$1"
  local payload="$2"
  python3 - "$target" "$payload" <<'PY'
import json
import sys

target = (sys.argv[1] or "").strip()
payload_text = sys.argv[2] or "{}"
data = json.loads(payload_text)
if isinstance(data, dict):
    instances = data.get("instances") or data.get("sops") or []
else:
    instances = data

selected = ""
target_repo_suffix = target.rsplit("/", 1)[-1] if target else ""

for item in instances:
    if not isinstance(item, dict):
        continue
    instance_id = item.get("id") or item.get("instance_id") or ""
    repo = item.get("repo") or item.get("wiki_repo") or item.get("channel_repo") or ""
    candidates = [
        item.get("id"),
        item.get("instance_id"),
        repo,
        item.get("title"),
        item.get("channel_url"),
        item.get("spi_base_url"),
    ]
    for value in candidates:
        if value and value == target:
            selected = instance_id
            break
    if selected:
        break
    if repo:
        if repo == target:
            selected = instance_id
            break
        if "/" in target and target.rsplit("/", 1)[-1] == repo.rsplit("/", 1)[-1]:
            selected = instance_id
            break
        if target_repo_suffix and repo.endswith("/" + target_repo_suffix):
            selected = instance_id
            break

print(selected or "NONE")
PY
}

resolve_instance_id() {
  local target="$1"
  local payload
  local instance_id
  if ! payload="$(get_instances_payload)"; then
    return 1
  fi
  instance_id="$(extract_instance_id "$target" "$payload" | tr -d '\n')"
  if [[ "$instance_id" == "NONE" || -z "$instance_id" ]]; then
    return 1
  fi
  echo "$instance_id"
  return 0
}

extract_status() {
  local payload="$1"
  python3 - "$payload" <<'PY'
import json
import sys
print((json.loads(sys.argv[1] or "{}").get("status") or "").lower())
PY
}

extract_pipeline_id() {
  local payload="$1"
  python3 - "$payload" <<'PY'
import json
import sys
data = json.loads(sys.argv[1] or "{}")
print(data.get("pipeline_id") or data.get("execution_id") or "")
PY
}

poll_run_status() {
  local instance="$1"
  local pipeline_id="$2"
  local start_ts
  start_ts="$(date +%s)"

  while true; do
    local payload
    if ! payload="$(request GET "/api/sop/$instance/runs/$pipeline_id")"; then
      return 1
    fi
    echo "$payload"
    local status
    status="$(extract_status "$payload" | tr -d '\n')"
    case "$status" in
      done|failed|cancelled)
        break
        ;;
    esac
    if (( REQUEST_TIMEOUT > 0 )); then
      local now
      now="$(date +%s)"
      if (( now - start_ts >= REQUEST_TIMEOUT )); then
        echo "{\"status\":\"timeout\",\"pipeline_id\":\"$pipeline_id\",\"instance_id\":\"$instance\"}" >&2
        return 124
      fi
    fi
    sleep "$POLL_INTERVAL"
  done
}

payload_trigger() {
  REPO="$REPO" URL="$URL" INTENT="$INTENT" python3 - <<'PY'
import json
import os

data = {
  "mode": "trigger",
  "repo": os.environ.get("REPO", ""),
  "url": os.environ.get("URL", "")
}
intent = os.environ.get("INTENT", "")
if intent:
  data["intent"] = intent
print(json.dumps(data))
PY
}

payload_validate() {
  REPO="$REPO" URL="$URL" python3 - <<'PY'
import json
import os

print(json.dumps({
  "mode": "validate",
  "repo": os.environ.get("REPO", ""),
  "url": os.environ.get("URL", "")
}))
PY
}

do_trigger() {
  local instance="$1"
  local response
  response="$(request POST "/api/sop/$instance/runs" "$(payload_trigger)")"
  echo "$response"
  if [[ "$WATCH_ACTIVE" -eq 1 ]]; then
    local pipeline_id
    pipeline_id="$(extract_pipeline_id "$response" | tr -d '\n')"
    if [[ -n "$pipeline_id" ]]; then
      poll_run_status "$instance" "$pipeline_id"
    fi
  fi
}

do_status() {
  local instance="$1"
  if [[ "$WATCH_ACTIVE" -eq 1 ]]; then
    poll_run_status "$instance" "$PIPELINE_ID"
  else
    request GET "/api/sop/$instance/runs/$PIPELINE_ID"
  fi
}

do_validate() {
  local instance="$1"
  request POST "/api/sop/$instance/runs" "$(payload_validate)"
}

do_list() {
  get_instances_payload
}

echo "Calling youtube-wiki..." >&2

case "$MODE" in
  init)
    echo "init mode is not available on remote SPI; initialize instance on runtime machine directly." >&2
    exit 2
    ;;
  trigger)
    [[ -n "$REPO" ]] || { echo "repo is required for trigger mode" >&2; exit 2; }
    [[ -n "$URL" ]] || { echo "url is required for trigger mode" >&2; exit 2; }
    instance="$(resolve_instance_id "$REPO")" || {
      echo "repo '$REPO' not found in /api/sop/instances" >&2
      exit 2
    }
    do_trigger "$instance"
    ;;
  status)
    [[ -n "$REPO" ]] || { echo "repo is required for status mode" >&2; exit 2; }
    [[ -n "$PIPELINE_ID" ]] || { echo "pipeline-id is required for status mode" >&2; exit 2; }
    instance="$(resolve_instance_id "$REPO")" || {
      echo "repo '$REPO' not found in /api/sop/instances" >&2
      exit 2
    }
    do_status "$instance"
    ;;
  validate)
    [[ -n "$REPO" ]] || { echo "repo is required for validate mode" >&2; exit 2; }
    [[ -n "$URL" ]] || { echo "url is required for validate mode" >&2; exit 2; }
    instance="$(resolve_instance_id "$REPO")" || {
      echo "repo '$REPO' not found in /api/sop/instances" >&2
      exit 2
    }
    do_validate "$instance"
    ;;
  list)
    do_list
    ;;
  *)
    echo "Unsupported mode: $MODE" >&2
    exit 1
    ;;
esac
