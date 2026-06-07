#!/usr/bin/env bash
set -euo pipefail

UI_URL="${SOP_UI_URL:-https://sop-ui-prototype.chxyka.ccwu.cc}"
TUNNEL_API="${TUNNEL_API:-https://tunnel-api.chxyka.ccwu.cc}"
EXPECT_RUNTIMES=()
CHECK_BUNDLE=1

usage() {
  cat <<'EOF'
Usage:
  scripts/verify-sop-ui-runtime-discovery.sh [options]

Checks that sop-ui-prototype can discover active SOP Runtime channels through
the tunnel-admin API. This does not trigger workflow execution.

Options:
  --ui-url=https://...                SOP UI URL, default SOP_UI_URL or prototype URL
  --tunnel-api=https://...            tunnel-admin API base
  --expect-runtime=name[|id[|url]]    required Runtime; can be repeated
  --no-bundle-check                   skip checking the deployed Vite bundle
  -h, --help                          show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ui-url=*) UI_URL="${1#--ui-url=}"; shift ;;
    --tunnel-api=*) TUNNEL_API="${1#--tunnel-api=}"; shift ;;
    --expect-runtime=*) EXPECT_RUNTIMES+=("${1#--expect-runtime=}"); shift ;;
    --no-bundle-check) CHECK_BUNDLE=0; shift ;;
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

python3 - "$UI_URL" "$TUNNEL_API" "$CHECK_BUNDLE" "$EXPECT_RUNTIMES_JSON" <<'PY'
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


ui_url, tunnel_api, check_bundle, expect_runtimes_json = sys.argv[1:]
ui_url = ui_url.rstrip("/") + "/"
tunnel_api = tunnel_api.rstrip("/")
check_bundle = check_bundle == "1"
try:
    expect_runtimes = json.loads(expect_runtimes_json)
except json.JSONDecodeError:
    expect_runtimes = []


def fail(message):
    print(f"[sop-ui-discovery] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def request_text(url):
    req = urllib.request.Request(url, headers={
        "Accept": "text/html,application/javascript,text/javascript,*/*",
        "User-Agent": "curl/8 sop-ui-discovery-verify",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"GET {url} failed: HTTP {exc.code}: {raw[:500]}")
    except urllib.error.URLError as exc:
        fail(f"GET {url} failed: {exc.reason}")


def request_json(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "curl/8 sop-ui-discovery-verify",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"GET {url} failed: HTTP {exc.code}: {raw[:500]}")
    except urllib.error.URLError as exc:
        fail(f"GET {url} failed: {exc.reason}")
    except json.JSONDecodeError as exc:
        fail(f"GET {url} returned invalid JSON: {exc}")


def parse_runtime_spec(value):
    parts = value.split("|")
    name = parts[0].strip()
    runtime_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else name
    endpoint = parts[2].rstrip("/") if len(parts) > 2 and parts[2].strip() else ""
    if not name:
        fail(f"invalid --expect-runtime value: {value!r}")
    return name, runtime_id, endpoint


def metadata_json(tunnel):
    raw = tunnel.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


html_status, html = request_text(ui_url)
if html_status != 200:
    fail(f"{ui_url} returned status {html_status}")
if 'id="root"' not in html:
    fail(f"{ui_url} does not contain React root")

bundle_url = ""
bundle_ok = True
if check_bundle:
    matches = re.findall(r'<script[^>]+type=["\']module["\'][^>]+src=["\']([^"\']+)["\']', html)
    if not matches:
        fail(f"{ui_url} does not reference a Vite module bundle")
    bundle_url = urllib.parse.urljoin(ui_url, matches[0])
    _, bundle = request_text(bundle_url)
    required_markers = ["admin/tunnels", "sop-runtime"]
    missing = [marker for marker in required_markers if marker not in bundle]
    if missing:
        fail(f"{bundle_url} missing runtime discovery markers: {', '.join(missing)}")
else:
    bundle_ok = False

admin_url = f"{tunnel_api}/admin/tunnels?limit=200"
admin_status, admin = request_json(admin_url)
if admin_status != 200:
    fail(f"{admin_url} returned status {admin_status}")
tunnels = admin.get("tunnels")
if not isinstance(tunnels, list):
    fail(f"{admin_url} did not return a tunnels list")

sop_runtimes = {}
for item in tunnels:
    if not isinstance(item, dict):
        continue
    metadata = metadata_json(item)
    if item.get("status") == "active" and metadata.get("type") == "sop-runtime":
        sop_runtimes[str(item.get("subdomain") or "")] = (item, metadata)

for spec in expect_runtimes:
    name, runtime_id, endpoint = parse_runtime_spec(spec)
    if name not in sop_runtimes:
        fail(f"Runtime not discoverable by sop-ui source: {name}")
    tunnel, metadata = sop_runtimes[name]
    if metadata.get("runtime_id") != runtime_id:
        fail(f"{name} metadata.runtime_id={metadata.get('runtime_id')!r}, expected {runtime_id}")
    if endpoint and str(metadata.get("channel_url") or "").rstrip("/") != endpoint:
        fail(f"{name} metadata.channel_url={metadata.get('channel_url')!r}, expected {endpoint}")
    if tunnel.get("local_status") != "ok":
        fail(f"{name} local_status={tunnel.get('local_status')!r}, expected ok")

print("[sop-ui-discovery] ok")
print(f"[sop-ui-discovery] ui_url: {ui_url.rstrip('/')}")
if check_bundle:
    print(f"[sop-ui-discovery] bundle: {bundle_url}")
    print("[sop-ui-discovery] bundle_markers: admin/tunnels, sop-runtime")
else:
    print(f"[sop-ui-discovery] bundle_check: {bundle_ok}")
print(f"[sop-ui-discovery] tunnel_api: {tunnel_api}")
print(f"[sop-ui-discovery] discovered_sop_runtimes: {len(sop_runtimes)}")
if expect_runtimes:
    print(f"[sop-ui-discovery] expected_runtimes: {', '.join(parse_runtime_spec(item)[0] for item in expect_runtimes)}")
PY
