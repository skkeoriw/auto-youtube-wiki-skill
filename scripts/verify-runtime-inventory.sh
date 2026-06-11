#!/usr/bin/env bash
set -euo pipefail

TUNNEL_API="${TUNNEL_API:-https://tunnel-api.chxyka.ccwu.cc}"
EXPECT_UI_URL="${SOP_UI_URL:-https://sop-ui-prototype.chxyka.ccwu.cc}"
RUNTIME_NAME_PREFIX="${RUNTIME_NAME_PREFIX:-youtube-wiki}"
STRICT=0
EXPECT_RUNTIMES=()

usage() {
  cat <<'EOF'
Usage:
  scripts/verify-runtime-inventory.sh [options]

Checks tunnel-admin inventory and classifies SOP Runtime channels by
metadata.type=sop-runtime. This does not trigger workflow execution.

Options:
  --tunnel-api=https://...              tunnel-admin API base
  --expect-runtime=name|runtime|url     expected Runtime; can be repeated
  --expect-ui-url=https://...           expected metadata.ui_url
  --runtime-name-prefix=youtube-wiki    runtime-like name prefix to guard
  --strict                              fail if extra SOP Runtime channels exist
  -h, --help                            show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tunnel-api=*) TUNNEL_API="${1#--tunnel-api=}"; shift ;;
    --expect-runtime=*) EXPECT_RUNTIMES+=("${1#--expect-runtime=}"); shift ;;
    --expect-ui-url=*) EXPECT_UI_URL="${1#--expect-ui-url=}"; shift ;;
    --runtime-name-prefix=*) RUNTIME_NAME_PREFIX="${1#--runtime-name-prefix=}"; shift ;;
    --strict) STRICT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

EXPECT_RUNTIMES_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${EXPECT_RUNTIMES[@]}")"

python3 - "$TUNNEL_API" "$EXPECT_UI_URL" "$RUNTIME_NAME_PREFIX" "$STRICT" "$EXPECT_RUNTIMES_JSON" <<'PY'
import json
import sys
import urllib.error
import urllib.request

(
    tunnel_api,
    expect_ui_url,
    runtime_name_prefix,
    strict,
    expect_runtimes_json,
) = sys.argv[1:]

tunnel_api = tunnel_api.rstrip("/")
strict = strict == "1"
expect_ui_url = expect_ui_url.rstrip("/") if expect_ui_url else ""
try:
    expect_runtimes = json.loads(expect_runtimes_json)
except json.JSONDecodeError:
    expect_runtimes = []


def fail(message):
    print(f"[runtime-inventory] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def request_json(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "curl/8 runtime-inventory-verify",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"GET {url} failed: HTTP {exc.code}: {raw[:500]}")
    except urllib.error.URLError as exc:
        fail(f"GET {url} failed: {exc.reason}")
    except json.JSONDecodeError as exc:
        fail(f"GET {url} returned invalid JSON: {exc}")


def parse_metadata(tunnel):
    raw = tunnel.get("metadata")
    if isinstance(raw, dict):
        return raw, None
    if not raw:
        return {}, None
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return {}, str(exc)


def parse_runtime_spec(raw):
    parts = raw.split("|", 2)
    if len(parts) != 3:
        fail(f"invalid --expect-runtime spec: {raw!r}")
    name, runtime_id, endpoint = parts
    if not name or not runtime_id or not endpoint:
        fail(f"invalid --expect-runtime spec: {raw!r}")
    return {
        "name": name,
        "runtime_id": runtime_id,
        "endpoint": endpoint.rstrip("/"),
    }


admin_url = f"{tunnel_api}/admin/tunnels"
body = request_json(admin_url)
tunnels = body.get("tunnels")
if not isinstance(tunnels, list):
    fail(f"{admin_url} did not return a tunnels list")

runtime_like = []
sop_runtimes = {}
duplicates = []

for tunnel in tunnels:
    name = str(tunnel.get("subdomain") or "")
    metadata, metadata_error = parse_metadata(tunnel)
    is_runtime_type = metadata.get("type") == "sop-runtime"
    is_runtime_like_name = bool(runtime_name_prefix and name.startswith(runtime_name_prefix))
    if not is_runtime_type and not is_runtime_like_name:
        continue
    if metadata_error:
        fail(f"{name} metadata is not valid JSON: {metadata_error}")
    if is_runtime_like_name and not is_runtime_type:
        fail(f"{name} looks like a Runtime channel but metadata.type={metadata.get('type')!r}, expected sop-runtime")
    runtime_like.append(name)
    if name in sop_runtimes:
        duplicates.append(name)
    sop_runtimes[name] = {
        "tunnel": tunnel,
        "metadata": metadata,
    }

if duplicates:
    fail(f"duplicate SOP Runtime tunnels: {', '.join(sorted(set(duplicates)))}")

expected = [parse_runtime_spec(item) for item in expect_runtimes]
expected_names = {item["name"] for item in expected}

for item in expected:
    name = item["name"]
    record = sop_runtimes.get(name)
    if record is None:
        fail(f"expected Runtime missing from tunnel-admin inventory: {name}")
    tunnel = record["tunnel"]
    metadata = record["metadata"]
    if tunnel.get("status") != "active":
        fail(f"{name} status={tunnel.get('status')!r}, expected active")
    if tunnel.get("local_status") != "ok":
        print(f"[runtime-inventory] warn: {name} local_status={tunnel.get('local_status')!r}, inventory entry remains valid")
    if not str(tunnel.get("client_ip") or "").strip() or tunnel.get("client_ip") == "unknown":
        fail(f"{name} client_ip is missing")
    if not str(tunnel.get("local_port") or "").strip():
        fail(f"{name} local_port is missing")
    if metadata.get("runtime_id") != item["runtime_id"]:
        fail(f"{name} runtime_id={metadata.get('runtime_id')!r}, expected {item['runtime_id']}")
    if str(metadata.get("channel_url") or "").rstrip("/") != item["endpoint"]:
        fail(f"{name} channel_url={metadata.get('channel_url')!r}, expected {item['endpoint']}")
    if expect_ui_url and str(metadata.get("ui_url") or "").rstrip("/") != expect_ui_url:
        fail(f"{name} ui_url={metadata.get('ui_url')!r}, expected {expect_ui_url}")

extra = sorted(set(sop_runtimes) - expected_names)
if strict and extra:
    fail(f"extra SOP Runtime tunnels: {', '.join(extra)}")

runtime_names = sorted(sop_runtimes)
print("[runtime-inventory] ok")
print(f"[runtime-inventory] tunnel_api: {tunnel_api}")
print(f"[runtime-inventory] total_tunnels: {len(tunnels)}")
print(f"[runtime-inventory] sop_runtimes: {', '.join(runtime_names) if runtime_names else '(none)'}")
if extra:
    print(f"[runtime-inventory] extra_sop_runtimes: {', '.join(extra)}")
if expected:
    print(f"[runtime-inventory] expected_runtimes: {', '.join(item['name'] for item in expected)}")
PY
