#!/usr/bin/env python3

import json
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify-tunnel-control-plane.sh"


class TunnelControlPlaneHandler(BaseHTTPRequestHandler):
    broken_health = False
    broken_page = False

    def log_message(self, _format, *_args):
        return

    def _send_json(self, body, status=200):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, body, status=200):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path.startswith("/admin/health"):
            self._send_json({
                "ok": not self.broken_health,
                "service": "auto-domain-tunnel",
                "zone": "example.com",
                "expected_routes": [
                    "tunnel-api.example.com/*",
                    "*.example.com/*",
                ],
                "api_route": {
                    "ok": not self.broken_health,
                    "pattern": "tunnel-api.example.com/*",
                    "script": "auto-domain-tunnel",
                },
                "gateway_route": {
                    "ok": not self.broken_health,
                    "pattern": "*.example.com/*",
                    "script": "auto-domain-tunnel",
                    "probe_url": f"{base}/__tunnel_health",
                },
                "cloudflare_routes": {
                    "configured": True,
                    "ok": not self.broken_health,
                    "errors": [] if not self.broken_health else ["missing wildcard"],
                },
                "database": {
                    "ok": True,
                    "active_tunnels": 3,
                    "total_tunnels": 3,
                },
            })
            return

        if self.path == "/__tunnel_health":
            self._send_json({
                "ok": True,
                "role": "gateway-route",
            })
            return

        if self.path == "/admin-page":
            if self.broken_page:
                self._send_html("<html><body>No source assets</body></html>")
            else:
                self._send_html("<html><body><th>Source</th><script>function sourceBadge(){}</script></body></html>")
            return

        self.send_response(404)
        self.end_headers()


class VerifyTunnelControlPlaneTest(unittest.TestCase):
    def run_server(self, broken_health=False, broken_page=False):
        class Handler(TunnelControlPlaneHandler):
            pass

        Handler.broken_health = broken_health
        Handler.broken_page = broken_page
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server

    def test_verify_tunnel_control_plane_success(self):
        server = self.run_server()
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--tunnel-api={endpoint}",
                f"--admin-page={endpoint}/admin-page",
                "--zone-name=example.com",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        self.assertIn("[tunnel-control-plane] ok", result.stdout)
        self.assertIn("active_tunnels: 3", result.stdout)

    def test_verify_tunnel_control_plane_rejects_broken_health(self):
        server = self.run_server(broken_health=True)
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--tunnel-api={endpoint}",
                f"--admin-page={endpoint}/admin-page",
                "--zone-name=example.com",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/admin/health ok=False", result.stderr)

    def test_verify_tunnel_control_plane_rejects_missing_source_column(self):
        server = self.run_server(broken_page=True)
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--tunnel-api={endpoint}",
                f"--admin-page={endpoint}/admin-page",
                "--zone-name=example.com",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Source column", result.stderr)


if __name__ == "__main__":
    unittest.main()
