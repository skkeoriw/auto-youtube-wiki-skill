#!/usr/bin/env bash
set -euo pipefail

TUNNEL_API="${TUNNEL_API:-https://tunnel-api.chxyka.ccwu.cc}"
ADMIN_PAGE="${TUNNEL_ADMIN_PAGE:-https://tunnel-admin-9vt.pages.dev/}"
ZONE_NAME="${AUTO_DOMAIN_ZONE_NAME:-chxyka.ccwu.cc}"
WORKER_SCRIPT="${AUTO_DOMAIN_WORKER_SCRIPT:-auto-domain-tunnel}"
CHECK_ADMIN_PAGE=1
CHECK_SOURCE_COLUMN=1
CHECK_STALE_THRESHOLD=1
REPAIR=0

usage() {
  cat <<'EOF'
Usage:
  scripts/verify-tunnel-control-plane.sh [options]

Checks auto-domain/tunnel-admin management health without triggering SOP runs.

Options:
  --tunnel-api=https://...            tunnel-admin API base
  --admin-page=https://...            tunnel-admin page URL
  --zone-name=chxyka.ccwu.cc          expected Cloudflare zone
  --worker-script=auto-domain-tunnel  expected Worker script
  --repair                            call /admin/health?repair=1 before checking
  --no-admin-page                     skip tunnel-admin page check
  --no-source-column                  skip Source column check on admin page
  --no-stale-threshold                skip tunnel-admin stale threshold check
  -h, --help                          show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tunnel-api=*) TUNNEL_API="${1#--tunnel-api=}"; shift ;;
    --admin-page=*) ADMIN_PAGE="${1#--admin-page=}"; shift ;;
    --zone-name=*) ZONE_NAME="${1#--zone-name=}"; shift ;;
    --worker-script=*) WORKER_SCRIPT="${1#--worker-script=}"; shift ;;
    --repair) REPAIR=1; shift ;;
    --no-admin-page) CHECK_ADMIN_PAGE=0; shift ;;
    --no-source-column) CHECK_SOURCE_COLUMN=0; shift ;;
    --no-stale-threshold) CHECK_STALE_THRESHOLD=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

python3 - "$TUNNEL_API" "$ADMIN_PAGE" "$ZONE_NAME" "$WORKER_SCRIPT" \
  "$CHECK_ADMIN_PAGE" "$CHECK_SOURCE_COLUMN" "$CHECK_STALE_THRESHOLD" "$REPAIR" <<'PY'
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

(
    tunnel_api,
    admin_page,
    zone_name,
    worker_script,
    check_admin_page,
    check_source_column,
    check_stale_threshold,
    repair,
) = sys.argv[1:]

tunnel_api = tunnel_api.rstrip("/")
check_admin_page = check_admin_page == "1"
check_source_column = check_source_column == "1"
check_stale_threshold = check_stale_threshold == "1"
repair = repair == "1"


def fail(message):
    print(f"[tunnel-control-plane] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def request(url, accept="application/json"):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": "curl/8 tunnel-control-plane-verify",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"GET {url} failed: HTTP {exc.code}: {raw[:500]}")
    except urllib.error.URLError as exc:
        fail(f"GET {url} failed: {exc.reason}")


def request_json(url):
    status, raw = request(url)
    if status != 200:
        fail(f"GET {url} returned {status}, expected 200")
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        fail(f"GET {url} returned invalid JSON: {exc}")


def require_ok(body, key):
    value = body.get(key)
    if not isinstance(value, dict):
        fail(f"/admin/health missing object: {key}")
    if value.get("ok") is not True:
        fail(f"/admin/health {key}.ok={value.get('ok')!r}, expected true")
    return value


health_url = f"{tunnel_api}/admin/health"
if repair:
    health_url = f"{health_url}?repair=1"
health = request_json(health_url)

if health.get("ok") is not True:
    fail(f"/admin/health ok={health.get('ok')!r}, expected true")
if health.get("service") not in ("auto-domain-tunnel", None):
    fail(f"/admin/health service={health.get('service')!r}, expected auto-domain-tunnel")
if zone_name and health.get("zone") not in (zone_name, None):
    fail(f"/admin/health zone={health.get('zone')!r}, expected {zone_name}")

api_route = require_ok(health, "api_route")
gateway_route = require_ok(health, "gateway_route")
database = require_ok(health, "database")

if worker_script:
    for key, route in (("api_route", api_route), ("gateway_route", gateway_route)):
        script = route.get("script")
        if script not in (worker_script, None):
            fail(f"/admin/health {key}.script={script!r}, expected {worker_script}")

expected_routes = set(health.get("expected_routes") or [])
if zone_name:
    required_routes = {f"tunnel-api.{zone_name}/*", f"*.{zone_name}/*"}
    missing_routes = sorted(required_routes - expected_routes)
    if missing_routes:
        fail(f"/admin/health expected_routes missing: {', '.join(missing_routes)}")

cloudflare_routes = health.get("cloudflare_routes")
if isinstance(cloudflare_routes, dict):
    if cloudflare_routes.get("configured") is not True:
        fail("/admin/health cloudflare_routes.configured is not true")
    if cloudflare_routes.get("ok") is not True:
        fail(f"/admin/health cloudflare_routes.ok={cloudflare_routes.get('ok')!r}, expected true")
    errors = cloudflare_routes.get("errors") or []
    if errors:
        fail(f"/admin/health cloudflare_routes.errors is not empty: {errors}")

probe_url = gateway_route.get("probe_url")
if probe_url:
    probe = request_json(probe_url)
    if probe.get("ok") is not True:
        fail(f"gateway probe ok={probe.get('ok')!r}, expected true")
    if probe.get("role") not in ("gateway-route", None):
        fail(f"gateway probe role={probe.get('role')!r}, expected gateway-route")

if check_admin_page:
    status, html = request(admin_page, accept="text/html")
    if status != 200:
        fail(f"GET {admin_page} returned {status}, expected 200")
    if check_source_column and ("Source" not in html or "sourceBadge" not in html):
        fail("tunnel-admin page does not include Source column/sourceBadge assets")
    if check_stale_threshold:
        match = re.search(r"const\s+TUNNEL_STALE_AFTER_MS\s*=\s*([^;]+);", html)
        if not match:
            fail("tunnel-admin page does not declare TUNNEL_STALE_AFTER_MS")
        expression = match.group(1).strip()
        if not re.fullmatch(r"[0-9\s+\-*/().]+", expression):
            fail(f"tunnel-admin stale threshold expression is not numeric: {expression!r}")
        try:
            threshold = float(eval(expression, {"__builtins__": {}}, {}))
        except Exception as exc:
            fail(f"could not evaluate TUNNEL_STALE_AFTER_MS: {exc}")
        if threshold < 600000:
            fail(f"TUNNEL_STALE_AFTER_MS={threshold:g}, expected at least 600000")

print("[tunnel-control-plane] ok")
print(f"[tunnel-control-plane] api: {tunnel_api}")
print(f"[tunnel-control-plane] zone: {zone_name}")
print(f"[tunnel-control-plane] worker: {worker_script}")
print(f"[tunnel-control-plane] active_tunnels: {database.get('active_tunnels', '(unknown)')}")
if probe_url:
    print(f"[tunnel-control-plane] gateway_probe: {probe_url}")
if check_admin_page:
    print(f"[tunnel-control-plane] admin_page: {admin_page}")
PY
