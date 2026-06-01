#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_NAME="youtube-wiki"
WORK_DIR="$(mktemp -d /tmp/youtube-wiki-skill-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT
TS="$(date +%Y%m%d%H%M%S)"
RELEASE_PATH="youtube-wiki/release"
PUBLISH_SKILL_INSTALL_URL="${PUBLISH_SKILL_INSTALL_URL:-https://skill.vyibc.com/install-publish-skill.sh}"

cp -R "$ROOT_DIR/skills/$SKILL_NAME" "$WORK_DIR/$SKILL_NAME"

ZIP_FILE="$WORK_DIR/${SKILL_NAME}-${TS}.zip"
python3 - "$WORK_DIR" "$SKILL_NAME" "$ZIP_FILE" <<'PY'
import os
import sys
import zipfile

root = sys.argv[1]
skill = sys.argv[2]
zip_path = sys.argv[3]
base = os.path.join(root, skill)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for current, _, files in os.walk(base):
        for name in files:
            path = os.path.join(current, name)
            arc = os.path.relpath(path, root)
            z.write(path, arc)
PY

ZIP_JSON="$("$ROOT_DIR/scripts/upload-file.sh" --file "$ZIP_FILE" --name "${SKILL_NAME}-${TS}.zip" --path "$RELEASE_PATH")"
ZIP_URL="$(printf '%s' "$ZIP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("image_url",""))')"
ZIP_URL_TS="${ZIP_URL}?ts=${TS}"

INSTALL_SCRIPT="$WORK_DIR/install-${SKILL_NAME}.sh"
PUBLISH_TEMPLATE="$WORK_DIR/install-publish-skill.sh"
curl -fsSL "$PUBLISH_SKILL_INSTALL_URL" -o "$PUBLISH_TEMPLATE"

python3 - "$PUBLISH_TEMPLATE" "$INSTALL_SCRIPT" "$SKILL_NAME" "$ZIP_URL_TS" <<'PY'
import pathlib
import re
import sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
skill_name = sys.argv[3]
zip_url = sys.argv[4]
text = src.read_text()
text = re.sub(r'^SKILL_NAME="[^"]*"$', f'SKILL_NAME="{skill_name}"', text, flags=re.M)
text = re.sub(r'^ZIP_URL="[^"]*"$', f'ZIP_URL="{zip_url}"', text, flags=re.M)
text = re.sub(r'^(# Auto-generated one-click install script for: ).*$', rf'\1{skill_name}', text, flags=re.M)
dst.write_text(text)
PY

chmod +x "$INSTALL_SCRIPT"

"$ROOT_DIR/scripts/upload-file.sh" --file "$INSTALL_SCRIPT" --name "install-${SKILL_NAME}.sh" >/dev/null
"$ROOT_DIR/scripts/upload-file.sh" --file "$ROOT_DIR/skills/youtube-wiki/scripts/run.sh" --name "${SKILL_NAME}.sh" >/dev/null
echo "SKILL_INSTALL_COMMAND=bash <(curl -fsSL 'https://skill.vyibc.com/install-${SKILL_NAME}.sh?ts=${TS}')"
echo "CLI_COMMAND=bash <(curl -fsSL https://skill.vyibc.com/${SKILL_NAME}.sh) --mode=trigger --repo=skkeoriw/llm-wiki-210-smoke --url="https://www.youtube.com/watch?v=dQw4w9WgXcQ""
