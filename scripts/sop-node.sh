#!/usr/bin/env bash
set -euo pipefail

ENDPOINT=""
INSTANCE=""
PIPELINE_ID=""
NODE_ID=""
ACTION="inspect"
PAYLOAD_JSON="{}"
DRY_RUN=0
CONFIRM=0

usage() {
  cat <<'EOF'
Usage:
  sop-node.sh --endpoint=URL --instance=ID --node=NODE --action=inspect
  sop-node.sh --endpoint=URL --instance=ID --node=NODE --action=actions
  sop-node.sh --endpoint=URL --instance=ID --node=NODE --pipeline-id=PIPE --action=status
  sop-node.sh --endpoint=URL --instance=ID --node=NODE --pipeline-id=PIPE --action=retry --dry-run

This CLI is an HTTP client for SOP Runtime Node Action APIs. It never executes
skill scripts locally.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint=*) ENDPOINT="${1#--endpoint=}"; shift ;;
    --instance=*) INSTANCE="${1#--instance=}"; shift ;;
    --pipeline-id=*) PIPELINE_ID="${1#--pipeline-id=}"; shift ;;
    --node=*) NODE_ID="${1#--node=}"; shift ;;
    --action=*) ACTION="${1#--action=}"; shift ;;
    --payload-json=*) PAYLOAD_JSON="${1#--payload-json=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --confirm) CONFIRM=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

ENDPOINT="${ENDPOINT%/}"
if [[ -z "$ENDPOINT" || -z "$INSTANCE" || -z "$NODE_ID" ]]; then
  echo "--endpoint, --instance and --node are required" >&2
  usage >&2
  exit 2
fi

METHOD="GET"
PATH=""
case "$ACTION" in
  inspect)
    PATH="/api/sop/$INSTANCE/nodes/$NODE_ID"
    ;;
  actions)
    PATH="/api/sop/$INSTANCE/nodes/$NODE_ID/actions"
    ;;
  status)
    [[ -n "$PIPELINE_ID" ]] || { echo "--pipeline-id is required for status" >&2; exit 2; }
    PATH="/api/sop/$INSTANCE/runs/$PIPELINE_ID/nodes/$NODE_ID"
    ;;
  retry|cancel)
    [[ -n "$PIPELINE_ID" ]] || { echo "--pipeline-id is required for $ACTION" >&2; exit 2; }
    METHOD="POST"
    PATH="/api/sop/$INSTANCE/runs/$PIPELINE_ID/nodes/$NODE_ID/actions/$ACTION"
    ;;
  *)
    echo "unsupported action: $ACTION" >&2
    exit 2
    ;;
esac

URL="$ENDPOINT$PATH"
if [[ "$DRY_RUN" == "1" ]]; then
  printf '{"dry_run":true,"method":"%s","url":"%s","payload":%s}\n' "$METHOD" "$URL" "$PAYLOAD_JSON"
  exit 0
fi

if [[ "$ACTION" =~ ^(retry|cancel)$ && "$CONFIRM" != "1" ]]; then
  echo "Refusing to execute destructive action '$ACTION' without --confirm. Use --dry-run to preview." >&2
  exit 3
fi

if [[ "$METHOD" == "GET" ]]; then
  curl -fsSL "$URL"
else
  curl -fsSL -X POST -H "Content-Type: application/json" --data "$PAYLOAD_JSON" "$URL"
fi
echo
