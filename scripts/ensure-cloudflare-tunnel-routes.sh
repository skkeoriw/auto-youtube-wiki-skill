#!/usr/bin/env bash
set -euo pipefail

ZONE_NAME="${AUTO_DOMAIN_ZONE_NAME:-chxyka.ccwu.cc}"
WORKER_SCRIPT="${AUTO_DOMAIN_WORKER_SCRIPT:-auto-domain-tunnel}"
APPLY=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --zone-name=*) ZONE_NAME="${1#--zone-name=}"; shift ;;
    --worker-script=*) WORKER_SCRIPT="${1#--worker-script=}"; shift ;;
    --check) APPLY=0; shift ;;
    -h|--help)
      cat <<'EOF'
Usage:
  scripts/ensure-cloudflare-tunnel-routes.sh [--zone-name=chxyka.ccwu.cc] [--worker-script=auto-domain-tunnel] [--check]

Requires CF_EMAIL/CF_API_KEY or CLOUDFLARE_EMAIL/CLOUDFLARE_API_KEY.
Ensures these Worker routes exist:
  tunnel-api.<zone>/* -> <worker>
  *.<zone>/*          -> <worker>
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

CF_EMAIL="${CF_EMAIL:-${CLOUDFLARE_EMAIL:-}}"
CF_API_KEY="${CF_API_KEY:-${CLOUDFLARE_API_KEY:-}}"

if [ -z "$CF_EMAIL" ] || [ -z "$CF_API_KEY" ]; then
  echo "CF_EMAIL/CF_API_KEY are required" >&2
  exit 2
fi

python3 - "$ZONE_NAME" "$WORKER_SCRIPT" "$APPLY" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

zone_name, worker_script, apply_flag = sys.argv[1], sys.argv[2], sys.argv[3]
apply_changes = apply_flag == "1"
email = os.environ.get("CF_EMAIL") or os.environ.get("CLOUDFLARE_EMAIL")
api_key = os.environ.get("CF_API_KEY") or os.environ.get("CLOUDFLARE_API_KEY")
zone_id = os.environ.get("CF_ZONE_ID", "").strip()

base = "https://api.cloudflare.com/client/v4"
headers = {
    "X-Auth-Email": email,
    "X-Auth-Key": api_key,
    "Content-Type": "application/json",
}


def request(method, path, payload=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Cloudflare API {method} {path} failed: HTTP {exc.code}: {raw}")
    body = json.loads(raw)
    if not body.get("success"):
        raise SystemExit(f"Cloudflare API {method} {path} failed: {body.get('errors')}")
    return body


if not zone_id:
    qs = urllib.parse.urlencode({"name": zone_name})
    zones = request("GET", f"/zones?{qs}")["result"]
    if not zones:
        raise SystemExit(f"Cloudflare zone not found: {zone_name}")
    zone_id = zones[0]["id"]

routes_body = request("GET", f"/zones/{zone_id}/workers/routes")
routes = routes_body.get("result", [])
required = [
    f"tunnel-api.{zone_name}/*",
    f"*.{zone_name}/*",
]

errors = []
for pattern in required:
    found = next((route for route in routes if route.get("pattern") == pattern), None)
    if found and found.get("script") == worker_script:
        print(f"[cloudflare-routes] ok: {pattern} -> {worker_script}")
        continue

    if found and found.get("script") != worker_script:
        errors.append(
            f"route conflict: {pattern} is bound to {found.get('script')}, expected {worker_script}"
        )
        continue

    if not apply_changes:
        errors.append(f"missing route: {pattern} -> {worker_script}")
        continue

    created = request("POST", f"/zones/{zone_id}/workers/routes", {
        "pattern": pattern,
        "script": worker_script,
    })["result"]
    print(f"[cloudflare-routes] created: {created.get('pattern')} -> {created.get('script')}")

if errors:
    for error in errors:
        print(f"[cloudflare-routes] ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
