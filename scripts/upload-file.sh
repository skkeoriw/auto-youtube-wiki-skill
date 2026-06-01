#!/usr/bin/env bash
set -euo pipefail

FILE_PATH=""
OBJECT_NAME=""
OBJECT_PATH=""
DOMAIN="${UPLOAD_R2_DOMAIN:-https://skill.vyibc.com}"
API_URL="${UPLOAD_R2_URL:-https://upload-r2.vyibc.com}"
API_TOKEN="${UPLOAD_R2_TOKEN:-yt-research-token-2026}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) FILE_PATH="${2:-}"; shift 2 ;;
    --name) OBJECT_NAME="${2:-}"; shift 2 ;;
    --path) OBJECT_PATH="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$FILE_PATH" || -z "$OBJECT_NAME" ]]; then
  echo "usage: upload-file.sh --file <file> --name <name> [--path <path>]" >&2
  exit 1
fi

if [[ -n "$OBJECT_PATH" ]]; then
  curl -fsS --location "$API_URL" \
    --header "Authorization: Bearer $API_TOKEN" \
    --form "file=@${FILE_PATH}" \
    --form "domain=${DOMAIN}" \
    --form "name=${OBJECT_NAME}" \
    --form "path=${OBJECT_PATH}"
else
  curl -fsS --location "$API_URL" \
    --header "Authorization: Bearer $API_TOKEN" \
    --form "file=@${FILE_PATH}" \
    --form "domain=${DOMAIN}" \
    --form "name=${OBJECT_NAME}"
fi
