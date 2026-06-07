#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TUNNEL_API="${TUNNEL_API:-https://tunnel-api.chxyka.ccwu.cc}"
VERIFY_RUNTIME_CHANNEL_SCRIPT="${VERIFY_RUNTIME_CHANNEL_SCRIPT:-$SCRIPT_DIR/verify-runtime-channel.sh}"
EXPECT_SOURCE_MODE="${EXPECT_AUTO_DOMAIN_SOURCE_MODE:-managed}"
EXPECT_SOURCE_REPO="${EXPECT_AUTO_DOMAIN_SOURCE_REPO:-https://github.com/skkeoriw/auto-domain-cli.git}"
EXPECT_SOURCE_COMMIT="${EXPECT_AUTO_DOMAIN_SOURCE_COMMIT:-8738556}"
CHECK_OPTIONS=1
ONLY_NAMES=""

DEFAULT_FLEET=(
  "youtube-wiki|https://youtube-wiki.chxyka.ccwu.cc|youtube-wiki|skkeoriw/wiki-sop-210-registry-smoke|18121"
  "youtube-wiki-168|https://youtube-wiki-168.chxyka.ccwu.cc|youtube-wiki-168|skkeoriw/wiki-sop-168-registry-smoke|18121"
  "youtube-wiki-222|https://youtube-wiki-222.chxyka.ccwu.cc|youtube-wiki-222|skkeoriw/wiki-sop-222-provision-smoke|18121"
)

usage() {
  cat <<'EOF'
Usage:
  scripts/verify-runtime-fleet.sh [options]

Checks all known SOP Runtime public channels through tunnel-admin metadata,
public /api/sop, OPTIONS, and auto-domain source metadata.

Options:
  --only=name[,name]                  verify only matching runtime names
  --tunnel-api=https://...            tunnel-admin API base
  --source-mode=managed               expected auto-domain source mode
  --source-repo=https://...           expected auto-domain source repo
  --source-commit=8738556             expected auto-domain source commit
  --no-source-check                   skip auto-domain source expectations
  --no-options                        skip public OPTIONS check
  -h, --help                          show this help

Custom fleet:
  RUNTIME_FLEET_SPEC can override the default fleet. Use newline-separated rows:
  name|endpoint|runtime_id|repo|port
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --only=*) ONLY_NAMES="${1#--only=}"; shift ;;
    --tunnel-api=*) TUNNEL_API="${1#--tunnel-api=}"; shift ;;
    --source-mode=*) EXPECT_SOURCE_MODE="${1#--source-mode=}"; shift ;;
    --source-repo=*) EXPECT_SOURCE_REPO="${1#--source-repo=}"; shift ;;
    --source-commit=*) EXPECT_SOURCE_COMMIT="${1#--source-commit=}"; shift ;;
    --no-source-check) EXPECT_SOURCE_MODE=""; EXPECT_SOURCE_REPO=""; EXPECT_SOURCE_COMMIT=""; shift ;;
    --no-options) CHECK_OPTIONS=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

[ -x "$VERIFY_RUNTIME_CHANNEL_SCRIPT" ] || {
  echo "[runtime-fleet] verifier not found or not executable: $VERIFY_RUNTIME_CHANNEL_SCRIPT" >&2
  exit 1
}

should_verify() {
  local name="$1"
  [ -z "$ONLY_NAMES" ] && return 0
  local item
  IFS=',' read -r -a items <<< "$ONLY_NAMES"
  for item in "${items[@]}"; do
    [ "$name" = "$item" ] && return 0
  done
  return 1
}

load_specs() {
  if [ -n "${RUNTIME_FLEET_SPEC:-}" ]; then
    printf '%s\n' "$RUNTIME_FLEET_SPEC"
  else
    printf '%s\n' "${DEFAULT_FLEET[@]}"
  fi
}

total=0
passed=0
failed=0
failures=()

while IFS='|' read -r name endpoint runtime_id repo port; do
  [ -n "${name:-}" ] || continue
  should_verify "$name" || continue
  total=$((total + 1))
  echo "[runtime-fleet] verifying $name -> $endpoint"

  args=(
    --name="$name"
    --endpoint="$endpoint"
    --tunnel-api="$TUNNEL_API"
    --expect-runtime-id="$runtime_id"
    --expect-repo="$repo"
    --expect-port="$port"
  )
  if [ "$CHECK_OPTIONS" = "0" ]; then
    args+=(--no-options)
  fi
  if [ -n "$EXPECT_SOURCE_MODE" ]; then
    args+=(--expect-auto-domain-source-mode="$EXPECT_SOURCE_MODE")
  fi
  if [ -n "$EXPECT_SOURCE_REPO" ]; then
    args+=(--expect-auto-domain-source-repo="$EXPECT_SOURCE_REPO")
  fi
  if [ -n "$EXPECT_SOURCE_COMMIT" ]; then
    args+=(--expect-auto-domain-source-commit="$EXPECT_SOURCE_COMMIT")
  fi

  if "$VERIFY_RUNTIME_CHANNEL_SCRIPT" "${args[@]}"; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
    failures+=("$name")
  fi
done < <(load_specs)

echo "[runtime-fleet] summary: passed=$passed failed=$failed total=$total"
if [ "$failed" -gt 0 ]; then
  printf '[runtime-fleet] failed runtimes: %s\n' "${failures[*]}" >&2
  exit 1
fi
if [ "$total" -eq 0 ]; then
  echo "[runtime-fleet] no runtimes matched" >&2
  exit 2
fi
