#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TUNNEL_API="${TUNNEL_API:-https://tunnel-api.chxyka.ccwu.cc}"
VERIFY_RUNTIME_CHANNEL_SCRIPT="${VERIFY_RUNTIME_CHANNEL_SCRIPT:-$SCRIPT_DIR/verify-runtime-channel.sh}"
VERIFY_RUNTIME_INVENTORY_SCRIPT="${VERIFY_RUNTIME_INVENTORY_SCRIPT:-$SCRIPT_DIR/verify-runtime-inventory.sh}"
VERIFY_TUNNEL_CONTROL_PLANE_SCRIPT="${VERIFY_TUNNEL_CONTROL_PLANE_SCRIPT:-$SCRIPT_DIR/verify-tunnel-control-plane.sh}"
VERIFY_SOP_UI_DISCOVERY_SCRIPT="${VERIFY_SOP_UI_DISCOVERY_SCRIPT:-$SCRIPT_DIR/verify-sop-ui-runtime-discovery.sh}"
VERIFY_RUNTIME_REPO_VERSIONS_SCRIPT="${VERIFY_RUNTIME_REPO_VERSIONS_SCRIPT:-$SCRIPT_DIR/verify-runtime-repo-versions.sh}"
SOP_UI_URL="${SOP_UI_URL:-https://sop-ui-prototype.chxyka.ccwu.cc}"
EXPECT_SOURCE_MODE="${EXPECT_AUTO_DOMAIN_SOURCE_MODE:-managed}"
EXPECT_SOURCE_REPO="${EXPECT_AUTO_DOMAIN_SOURCE_REPO:-https://github.com/ChangfengHU/auto-domain-cli.git}"
EXPECT_SOURCE_REF="${EXPECT_AUTO_DOMAIN_SOURCE_REF:-main}"
EXPECT_SOURCE_COMMIT="${EXPECT_AUTO_DOMAIN_SOURCE_COMMIT:-latest}"
EXPECT_SOP_TYPES="${EXPECT_SOP_TYPES:-runtime-provisioning,youtube-research-wiki}"
CHECK_OPTIONS=1
CHECK_INVENTORY=1
STRICT_INVENTORY=0
CHECK_CONTROL_PLANE=1
CHECK_SOP_UI=1
CHECK_REPO_VERSIONS=0
REPAIR_CONTROL_PLANE=0
ONLY_NAMES=""
REPO_TARGETS=()

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
public /api/sop, OPTIONS, auto-domain source metadata, tunnel control-plane
health, and sop-ui-prototype Runtime discovery.

Options:
  --only=name[,name]                  verify only matching runtime names
  --tunnel-api=https://...            tunnel-admin API base
  --sop-ui-url=https://...            SOP UI URL
  --repo-version-check                check remote Runtime repo versions through SSH
  --repo-target=name|user|host|key    runtime repo SSH target; can be repeated
  --repo-target=name|user|host||ENV   use sshpass with password from ENV
  --repair-control-plane              call /admin/health?repair=1 before runtime checks
  --strict-inventory                  fail if tunnel-admin exposes extra SOP Runtime channels
  --no-inventory                      skip tunnel-admin Runtime inventory check
  --no-control-plane                  skip tunnel-admin/Cloudflare health check
  --no-sop-ui                         skip sop-ui-prototype Runtime discovery check
  --source-mode=managed               expected auto-domain source mode
  --source-repo=https://...           expected auto-domain source repo
  --source-ref=main                   expected auto-domain source ref
  --source-commit=latest              expected auto-domain source commit; "latest" resolves source-ref through git ls-remote
  --sop-types=a,b                     expected metadata.supported_sop_types
  --no-source-check                   skip auto-domain source expectations
  --no-sop-type-check                 skip supported_sop_types expectations
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
    --sop-ui-url=*) SOP_UI_URL="${1#--sop-ui-url=}"; shift ;;
    --repo-version-check) CHECK_REPO_VERSIONS=1; shift ;;
    --repo-target=*) REPO_TARGETS+=("${1#--repo-target=}"); shift ;;
    --repair-control-plane) REPAIR_CONTROL_PLANE=1; shift ;;
    --strict-inventory) STRICT_INVENTORY=1; shift ;;
    --no-inventory) CHECK_INVENTORY=0; shift ;;
    --no-control-plane) CHECK_CONTROL_PLANE=0; shift ;;
    --no-sop-ui) CHECK_SOP_UI=0; shift ;;
    --source-mode=*) EXPECT_SOURCE_MODE="${1#--source-mode=}"; shift ;;
    --source-repo=*) EXPECT_SOURCE_REPO="${1#--source-repo=}"; shift ;;
    --source-ref=*) EXPECT_SOURCE_REF="${1#--source-ref=}"; shift ;;
    --source-commit=*) EXPECT_SOURCE_COMMIT="${1#--source-commit=}"; shift ;;
    --sop-types=*) EXPECT_SOP_TYPES="${1#--sop-types=}"; shift ;;
    --no-source-check) EXPECT_SOURCE_MODE=""; EXPECT_SOURCE_REPO=""; EXPECT_SOURCE_REF=""; EXPECT_SOURCE_COMMIT=""; shift ;;
    --no-sop-type-check) EXPECT_SOP_TYPES=""; shift ;;
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

if [ "$CHECK_CONTROL_PLANE" = "1" ]; then
  [ -x "$VERIFY_TUNNEL_CONTROL_PLANE_SCRIPT" ] || {
    echo "[runtime-fleet] control-plane verifier not found or not executable: $VERIFY_TUNNEL_CONTROL_PLANE_SCRIPT" >&2
    exit 1
  }
fi

if [ "$CHECK_INVENTORY" = "1" ]; then
  [ -x "$VERIFY_RUNTIME_INVENTORY_SCRIPT" ] || {
    echo "[runtime-fleet] inventory verifier not found or not executable: $VERIFY_RUNTIME_INVENTORY_SCRIPT" >&2
    exit 1
  }
fi

if [ "$CHECK_SOP_UI" = "1" ]; then
  [ -x "$VERIFY_SOP_UI_DISCOVERY_SCRIPT" ] || {
    echo "[runtime-fleet] sop-ui discovery verifier not found or not executable: $VERIFY_SOP_UI_DISCOVERY_SCRIPT" >&2
    exit 1
  }
fi

if [ "$CHECK_REPO_VERSIONS" = "1" ]; then
  [ -x "$VERIFY_RUNTIME_REPO_VERSIONS_SCRIPT" ] || {
    echo "[runtime-fleet] runtime repo verifier not found or not executable: $VERIFY_RUNTIME_REPO_VERSIONS_SCRIPT" >&2
    exit 1
  }
  [ "${#REPO_TARGETS[@]}" -gt 0 ] || {
    echo "[runtime-fleet] --repo-version-check requires at least one --repo-target" >&2
    exit 1
  }
fi

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

resolve_expected_source_commit() {
  [ -n "$EXPECT_SOURCE_COMMIT" ] || return 0
  [ "$EXPECT_SOURCE_COMMIT" = "latest" ] || return 0
  [ -n "$EXPECT_SOURCE_REPO" ] || {
    echo "[runtime-fleet] --source-commit=latest requires --source-repo" >&2
    exit 1
  }
  [ -n "$EXPECT_SOURCE_REF" ] || {
    echo "[runtime-fleet] --source-commit=latest requires --source-ref" >&2
    exit 1
  }
  command -v git >/dev/null 2>&1 || {
    echo "[runtime-fleet] git is required for --source-commit=latest" >&2
    exit 1
  }

  local line sha
  line="$(git ls-remote "$EXPECT_SOURCE_REPO" "$EXPECT_SOURCE_REF" 2>/dev/null | head -n 1 || true)"
  if [ -z "$line" ]; then
    line="$(git ls-remote --heads "$EXPECT_SOURCE_REPO" "$EXPECT_SOURCE_REF" 2>/dev/null | head -n 1 || true)"
  fi
  [ -n "$line" ] || {
    echo "[runtime-fleet] could not resolve auto-domain source latest: $EXPECT_SOURCE_REPO $EXPECT_SOURCE_REF" >&2
    exit 1
  }
  sha="${line%%[[:space:]]*}"
  [ -n "$sha" ] || {
    echo "[runtime-fleet] invalid git ls-remote output for $EXPECT_SOURCE_REPO $EXPECT_SOURCE_REF" >&2
    exit 1
  }

  EXPECT_SOURCE_COMMIT="${sha:0:7}"
  echo "[runtime-fleet] resolved auto-domain source latest: $EXPECT_SOURCE_REPO@$EXPECT_SOURCE_REF -> $EXPECT_SOURCE_COMMIT"
}

total=0
passed=0
failed=0
failures=()
ui_expected_runtimes=()
inventory_expected_runtimes=()

resolve_expected_source_commit

if [ "$CHECK_CONTROL_PLANE" = "1" ]; then
  echo "[runtime-fleet] verifying tunnel control plane -> $TUNNEL_API"
  control_plane_args=(--tunnel-api="$TUNNEL_API")
  if [ "$REPAIR_CONTROL_PLANE" = "1" ]; then
    control_plane_args+=(--repair)
  fi
  "$VERIFY_TUNNEL_CONTROL_PLANE_SCRIPT" "${control_plane_args[@]}"
fi

if [ "$CHECK_INVENTORY" = "1" ]; then
  inventory_args=(--tunnel-api="$TUNNEL_API" --expect-ui-url="$SOP_UI_URL")
  if [ "$STRICT_INVENTORY" = "1" ]; then
    inventory_args+=(--strict)
  fi
  while IFS='|' read -r name endpoint runtime_id repo port; do
    [ -n "${name:-}" ] || continue
    should_verify "$name" || continue
    inventory_expected_runtimes+=("$name|$runtime_id|$endpoint")
  done < <(load_specs)
  for item in "${inventory_expected_runtimes[@]}"; do
    inventory_args+=(--expect-runtime="$item")
  done
  echo "[runtime-fleet] verifying runtime inventory -> $TUNNEL_API"
  "$VERIFY_RUNTIME_INVENTORY_SCRIPT" "${inventory_args[@]}"
fi

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
    --expect-ui-url="$SOP_UI_URL"
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
  if [ -n "$EXPECT_SOURCE_REF" ]; then
    args+=(--expect-auto-domain-source-ref="$EXPECT_SOURCE_REF")
  fi
  if [ -n "$EXPECT_SOURCE_COMMIT" ]; then
    args+=(--expect-auto-domain-source-commit="$EXPECT_SOURCE_COMMIT")
  fi
  if [ -n "$EXPECT_SOP_TYPES" ]; then
    IFS=',' read -r -a sop_type_items <<< "$EXPECT_SOP_TYPES"
    for sop_type in "${sop_type_items[@]}"; do
      [ -n "$sop_type" ] && args+=(--expect-sop-type="$sop_type")
    done
  fi

  if "$VERIFY_RUNTIME_CHANNEL_SCRIPT" "${args[@]}"; then
    passed=$((passed + 1))
    ui_expected_runtimes+=("$name|$runtime_id|$endpoint")
  else
    failed=$((failed + 1))
    failures+=("$name")
  fi
done < <(load_specs)

if [ "$CHECK_SOP_UI" = "1" ] && [ "$failed" -eq 0 ]; then
  echo "[runtime-fleet] verifying sop-ui discovery -> $SOP_UI_URL"
  ui_args=(--ui-url="$SOP_UI_URL" --tunnel-api="$TUNNEL_API")
  for item in "${ui_expected_runtimes[@]}"; do
    ui_args+=(--expect-runtime="$item")
  done
  "$VERIFY_SOP_UI_DISCOVERY_SCRIPT" "${ui_args[@]}"
fi

if [ "$CHECK_REPO_VERSIONS" = "1" ] && [ "$failed" -eq 0 ]; then
  echo "[runtime-fleet] verifying runtime repo versions"
  repo_args=()
  for item in "${REPO_TARGETS[@]}"; do
    repo_args+=(--target="$item")
  done
  "$VERIFY_RUNTIME_REPO_VERSIONS_SCRIPT" "${repo_args[@]}"
fi

echo "[runtime-fleet] summary: passed=$passed failed=$failed total=$total"
if [ "$failed" -gt 0 ]; then
  printf '[runtime-fleet] failed runtimes: %s\n' "${failures[*]}" >&2
  exit 1
fi
if [ "$total" -eq 0 ]; then
  echo "[runtime-fleet] no runtimes matched" >&2
  exit 2
fi
