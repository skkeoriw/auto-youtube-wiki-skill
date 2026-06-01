#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="${REPO_OWNER:-skkeoriw}"
REPO_NAME="${REPO_NAME:-auto-youtube-wiki-skill}"
REPO_REF="${REPO_REF:-main}"
TARBALL_URL="${TARBALL_URL:-https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/refs/heads/${REPO_REF}}"

WORK_DIR="$(mktemp -d /tmp/publish-auto-youtube-wiki-skill-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Fetching ${REPO_OWNER}/${REPO_NAME}@${REPO_REF} ..."
curl -fsSL "$TARBALL_URL" -o "$WORK_DIR/repo.tar.gz"
tar -xzf "$WORK_DIR/repo.tar.gz" -C "$WORK_DIR"

REPO_DIR="$(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -type d | head -1)"
if [[ -z "$REPO_DIR" || ! -x "$REPO_DIR/scripts/publish-skill.sh" ]]; then
  echo "publish-skill.sh not found in fetched repository" >&2
  exit 1
fi

exec "$REPO_DIR/scripts/publish-skill.sh"
