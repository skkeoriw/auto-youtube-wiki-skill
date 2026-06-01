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

ENDPOINT=https://youtube-wiki.chxyka.ccwu.cc
if [[ -n "$ENDPOINT_OVERRIDE" ]]; then
  ENDPOINT="$ENDPOINT_OVERRIDE"
fi

COMMON_HEADERS=()
if [[ -n "$TOKEN" ]]; then
  COMMON_HEADERS+=(-H "Authorization: Bearer $TOKEN")
fi

echo "Calling youtube-wiki..." >&2

case "$MODE" in
init)
  PAYLOAD=$(REPO="${REPO}" python3 -c 'import json, os; keys = ["repo"]; data = {"mode": "init"}; [data.__setitem__(key, os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_"))) for key in keys if (os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_")))]; print(json.dumps(data))')
  curl --connect-timeout 10 --max-time 900 --fail-with-body -sS -L "$ENDPOINT" ${COMMON_HEADERS[@]+"${COMMON_HEADERS[@]}"} -H "Content-Type: application/json" -d "$PAYLOAD"
  ;;
trigger)
  PAYLOAD=$(REPO="${REPO}" URL="${URL}" INTENT="${INTENT}" WATCH="${WATCH}" TIMEOUT="${TIMEOUT}" python3 -c 'import json, os; keys = ["repo", "url", "intent", "watch", "timeout"]; data = {"mode": "trigger"}; [data.__setitem__(key, os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_"))) for key in keys if (os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_")))]; print(json.dumps(data))')
  curl --connect-timeout 10 --max-time 900 --fail-with-body -sS -L "$ENDPOINT" ${COMMON_HEADERS[@]+"${COMMON_HEADERS[@]}"} -H "Content-Type: application/json" -d "$PAYLOAD"
  ;;
status)
  PAYLOAD=$(REPO="${REPO}" PIPELINE_ID="${PIPELINE_ID}" WATCH="${WATCH}" TIMEOUT="${TIMEOUT}" python3 -c 'import json, os; keys = ["repo", "pipeline_id", "watch", "timeout"]; data = {"mode": "status"}; [data.__setitem__(key, os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_"))) for key in keys if (os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_")))]; print(json.dumps(data))')
  curl --connect-timeout 10 --max-time 900 --fail-with-body -sS -L "$ENDPOINT" ${COMMON_HEADERS[@]+"${COMMON_HEADERS[@]}"} -H "Content-Type: application/json" -d "$PAYLOAD"
  ;;
validate)
  PAYLOAD=$(URL="${URL}" TIMEOUT="${TIMEOUT}" python3 -c 'import json, os; keys = ["url", "timeout"]; data = {"mode": "validate"}; [data.__setitem__(key, os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_"))) for key in keys if (os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_")))]; print(json.dumps(data))')
  curl --connect-timeout 10 --max-time 900 --fail-with-body -sS -L "$ENDPOINT" ${COMMON_HEADERS[@]+"${COMMON_HEADERS[@]}"} -H "Content-Type: application/json" -d "$PAYLOAD"
  ;;
list)
  PAYLOAD=$( python3 -c 'import json, os; keys = []; data = {"mode": "list"}; [data.__setitem__(key, os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_"))) for key in keys if (os.environ.get(key.upper().replace("-", "_").replace(".", "_")) or os.environ.get(key.upper().replace("-", "_")))]; print(json.dumps(data))')
  curl --connect-timeout 10 --max-time 900 --fail-with-body -sS -L "$ENDPOINT" ${COMMON_HEADERS[@]+"${COMMON_HEADERS[@]}"} -H "Content-Type: application/json" -d "$PAYLOAD"
  ;;
  *)
    echo "Unsupported mode: $MODE" >&2
    exit 1
    ;;
esac
