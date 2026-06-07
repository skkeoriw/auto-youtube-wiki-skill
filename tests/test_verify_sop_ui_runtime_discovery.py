#!/usr/bin/env python3

import json
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify-sop-ui-runtime-discovery.sh"


class SopUiDiscoveryHandler(BaseHTTPRequestHandler):
    missing_runtime = False
    bad_bundle = False

    def log_message(self, _format, *_args):
        return

    def _send(self, body: str, content_type="text/plain", status=200):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, body, status=200):
        self._send(json.dumps(body), "application/json", status)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(
                '<!doctype html><html><head><script type="module" '
                'src="/assets/index-test.js"></script></head><body><div id="root"></div></body></html>',
                "text/html",
            )
            return

        if self.path == "/assets/index-test.js":
            body = "console.log('admin/tunnels sop-runtime runtime discovery')" if not self.bad_bundle else "console.log('app')"
            self._send(body, "application/javascript")
            return

        if self.path.startswith("/admin/tunnels"):
            tunnels = []
            if not self.missing_runtime:
                tunnels.append({
                    "subdomain": "youtube-wiki-222",
                    "status": "active",
                    "local_status": "ok",
                    "client_ip": "34.29.222.183",
                    "local_port": "18121",
                    "metadata": json.dumps({
                        "type": "sop-runtime",
                        "runtime_id": "youtube-wiki-222",
                        "channel_url": "https://youtube-wiki-222.chxyka.ccwu.cc",
                    }),
                })
            self._send_json({"tunnels": tunnels})
            return

        self.send_response(404)
        self.end_headers()


class VerifySopUiRuntimeDiscoveryTest(unittest.TestCase):
    def run_server(self, missing_runtime=False, bad_bundle=False):
        class Handler(SopUiDiscoveryHandler):
            pass

        Handler.missing_runtime = missing_runtime
        Handler.bad_bundle = bad_bundle
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server

    def test_verify_sop_ui_runtime_discovery_success(self):
        server = self.run_server()
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--ui-url={endpoint}",
                f"--tunnel-api={endpoint}",
                "--expect-runtime=youtube-wiki-222|youtube-wiki-222|https://youtube-wiki-222.chxyka.ccwu.cc",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        self.assertIn("[sop-ui-discovery] ok", result.stdout)
        self.assertIn("expected_runtimes: youtube-wiki-222", result.stdout)

    def test_verify_sop_ui_runtime_discovery_rejects_missing_runtime(self):
        server = self.run_server(missing_runtime=True)
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--ui-url={endpoint}",
                f"--tunnel-api={endpoint}",
                "--expect-runtime=youtube-wiki-222|youtube-wiki-222|https://youtube-wiki-222.chxyka.ccwu.cc",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Runtime not discoverable", result.stderr)

    def test_verify_sop_ui_runtime_discovery_rejects_bad_bundle(self):
        server = self.run_server(bad_bundle=True)
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--ui-url={endpoint}",
                f"--tunnel-api={endpoint}",
                "--expect-runtime=youtube-wiki-222|youtube-wiki-222|https://youtube-wiki-222.chxyka.ccwu.cc",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing runtime discovery markers", result.stderr)


if __name__ == "__main__":
    unittest.main()
