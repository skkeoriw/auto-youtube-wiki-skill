#!/usr/bin/env bash
# Local heavy script — edit this file to implement your logic
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"
source "$HOME/.agent-brain-plugins.env" 2>/dev/null || true
mode="${MODE:-trigger}"
repo="${REPO:-${WIKI_GITHUB_REPO:-}}"
url="${URL:-}"
intent="${INTENT:-youtube-wiki remote trigger}"
pipeline_id="${PIPELINE_ID:-}"
timeout="${TIMEOUT:-900}"
watch="${WATCH:-false}"
case "$mode" in
  init)
    [ -n "$repo" ] || { echo "repo is required" >&2; exit 2; }
    youtube-wiki init --repo "$repo"
    ;;
  trigger)
    [ -n "$repo" ] || { echo "repo is required" >&2; exit 2; }
    [ -n "$url" ] || { echo "url is required" >&2; exit 2; }
    args=(trigger --repo "$repo" --url "$url" --intent "$intent")
    case "$watch" in true|1|yes|on) args+=(--watch --timeout "$timeout") ;; esac
    youtube-wiki "${args[@]}"
    ;;
  status)
    [ -n "$repo" ] || { echo "repo is required" >&2; exit 2; }
    [ -n "$pipeline_id" ] || { echo "pipeline_id is required" >&2; exit 2; }
    args=(status --repo "$repo" --pipeline-id "$pipeline_id")
    case "$watch" in true|1|yes|on) args+=(--watch --timeout "$timeout") ;; esac
    youtube-wiki "${args[@]}"
    ;;
  validate)
    args=(validate)
    [ -n "$url" ] && args+=(--url "$url")
    [ -n "$timeout" ] && args+=(--timeout "$timeout")
    youtube-wiki "${args[@]}"
    ;;
  list)
    youtube-wiki list
    ;;
  *)
    echo "unsupported mode: $mode" >&2
    exit 2
    ;;
esac
