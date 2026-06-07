#!/usr/bin/env bash
set -euo pipefail

NAME=""
ENDPOINT=""
TUNNEL_API="${TUNNEL_API:-https://tunnel-api.chxyka.ccwu.cc}"
EXPECT_RUNTIME_ID=""
EXPECT_REPO=""
EXPECT_PORT=""
CHECK_OPTIONS=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --name=*) NAME="${1#--name=}"; shift ;;
    --endpoint=*) ENDPOINT="${1#--endpoint=}"; shift ;;
    --tunnel-api=*) TUNNEL_API="${1#--tunnel-api=}"; shift ;;
    --expect-runtime-id=*) EXPECT_RUNTIME_ID="${1#--expect-runtime-id=}"; shift ;;
    --expect-repo=*) EXPECT_REPO="${1#--expect-repo=}"; shift ;;
    --expect-port=*) EXPECT_PORT="${1#--expect-port=}"; shift ;;
    --no-options) CHECK_OPTIONS=0; shift ;;
    -h|--help)
      cat <<'EOF'
Usage:
  scripts/verify-runtime-channel.sh \
    --name=youtube-wiki \
    --endpoint=https://youtube-wiki.chxyka.ccwu.cc \
    [--expect-runtime-id=youtube-wiki] \
    [--expect-repo=skkeoriw/wiki-sop-210-registry-smoke] \
    [--expect-port=18121]

Checks tunnel-admin metadata, public /api/sop, and OPTIONS for a SOP Runtime.
Does not require jq.
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

[ -n "$ENDPOINT" ] || { echo "--endpoint is required" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

python3 - "$NAME" "$ENDPOINT" "$TUNNEL_API" "$EXPECT_RUNTIME_ID" "$EXPECT_REPO" "$EXPECT_PORT" "$CHECK_OPTIONS" <<'PY'
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


name, endpoint, tunnel_api, expect_runtime_id, expect_repo, expect_port, check_options = sys.argv[1:]
endpoint = endpoint.rstrip("/")
tunnel_api = tunnel_api.rstrip("/")
check_options = check_options == "1"

if not name:
    host = urllib.parse.urlparse(endpoint).hostname or ""
    name = host.split(".", 1)[0]

expect_runtime_id = expect_runtime_id or name


def fail(message):
    print(f"[runtime-channel] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def request_json(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={
        "Accept": "application/json",
        "User-Agent": "curl/8 runtime-channel-verify",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"{method} {url} failed: HTTP {exc.code}: {raw[:500]}")
    except urllib.error.URLError as exc:
        fail(f"{method} {url} failed: {exc.reason}")
    except json.JSONDecodeError as exc:
        fail(f"{method} {url} returned invalid JSON: {exc}")


def request_status(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={
        "Accept": "application/json",
        "User-Agent": "curl/8 runtime-channel-verify",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            return resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"{method} {url} failed: HTTP {exc.code}: {raw[:500]}")
    except urllib.error.URLError as exc:
        fail(f"{method} {url} failed: {exc.reason}")


def metadata_json(tunnel):
    raw = tunnel.get("metadata")
    if not raw:
        fail(f"{name} metadata is empty")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{name} metadata is not valid JSON: {exc}")


def repos_from_sop(body):
    repos = set()
    for key in ("sops", "instances"):
        value = body.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("repo"):
                    repos.add(str(item["repo"]))
    return repos


admin_url = f"{tunnel_api}/admin/tunnels"
_, admin_body = request_json(admin_url)
tunnels = admin_body.get("tunnels")
if not isinstance(tunnels, list):
    fail(f"{admin_url} did not return a tunnels list")

tunnel = next((item for item in tunnels if item.get("subdomain") == name), None)
if tunnel is None:
    fail(f"tunnel not found: {name}")

if tunnel.get("status") != "active":
    fail(f"{name} status={tunnel.get('status')!r}, expected active")
if tunnel.get("local_status") != "ok":
    fail(f"{name} local_status={tunnel.get('local_status')!r}, expected ok")
if not str(tunnel.get("client_ip") or "").strip() or tunnel.get("client_ip") == "unknown":
    fail(f"{name} client_ip is missing")
if expect_port:
    if str(tunnel.get("local_port") or "") != expect_port:
        fail(f"{name} local_port={tunnel.get('local_port')!r}, expected {expect_port}")
elif not str(tunnel.get("local_port") or "").strip():
    fail(f"{name} local_port is missing")

metadata = metadata_json(tunnel)
if metadata.get("type") != "sop-runtime":
    fail(f"{name} metadata.type={metadata.get('type')!r}, expected sop-runtime")
if metadata.get("runtime_id") != expect_runtime_id:
    fail(f"{name} metadata.runtime_id={metadata.get('runtime_id')!r}, expected {expect_runtime_id}")
if str(metadata.get("channel_url") or "").rstrip("/") != endpoint:
    fail(f"{name} metadata.channel_url={metadata.get('channel_url')!r}, expected {endpoint}")
if expect_repo and metadata.get("wiki_repo") != expect_repo:
    fail(f"{name} metadata.wiki_repo={metadata.get('wiki_repo')!r}, expected {expect_repo}")

sop_url = f"{endpoint}/api/sop"
_, sop_body = request_json(sop_url)
runtime_id = sop_body.get("runtime_id") or sop_body.get("runtime")
if runtime_id != expect_runtime_id:
    fail(f"{sop_url} runtime_id={runtime_id!r}, expected {expect_runtime_id}")
if expect_repo and expect_repo not in repos_from_sop(sop_body):
    fail(f"{sop_url} does not expose repo {expect_repo}")

if check_options:
    status = request_status(sop_url, method="OPTIONS")
    if status not in {200, 204}:
        fail(f"OPTIONS {sop_url} returned {status}, expected 200 or 204")

print(f"[runtime-channel] ok: {name}")
print(f"[runtime-channel] endpoint: {endpoint}")
print(f"[runtime-channel] runtime_id: {expect_runtime_id}")
print(f"[runtime-channel] repo: {expect_repo or '(not checked)'}")
print(f"[runtime-channel] client_ip: {tunnel.get('client_ip')}")
print(f"[runtime-channel] local_port: {tunnel.get('local_port')}")
PY
