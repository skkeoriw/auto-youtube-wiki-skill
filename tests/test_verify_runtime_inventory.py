#!/usr/bin/env python3

import json
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify-runtime-inventory.sh"


class RuntimeInventoryHandler(BaseHTTPRequestHandler):
    mode = "success"

    def log_message(self, _format, *_args):
        return

    def _send_json(self, body, status=200):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path != "/admin/tunnels":
            self.send_response(404)
            self.end_headers()
            return

        endpoint = f"http://127.0.0.1:{self.server.server_port}"
        tunnels = [{
            "subdomain": "youtube-wiki-222",
            "status": "active",
            "local_status": "ok",
            "client_ip": "34.29.222.183",
            "local_port": "18121",
            "metadata": json.dumps({
                "type": "sop-runtime",
                "runtime_id": "youtube-wiki-222",
                "channel_url": f"{endpoint}/runtime",
                "ui_url": "https://sop-ui-prototype.chxyka.ccwu.cc",
            }),
        }]
        if self.mode == "missing":
            tunnels = []
        elif self.mode == "bad_runtime_like":
            tunnels = [{
                "subdomain": "youtube-wiki-old",
                "status": "active",
                "local_status": "ok",
                "client_ip": "127.0.0.1",
                "local_port": "18121",
                "metadata": json.dumps({"type": "legacy"}),
            }]
        elif self.mode == "extra":
            tunnels.append({
                "subdomain": "youtube-wiki-extra",
                "status": "active",
                "local_status": "ok",
                "client_ip": "127.0.0.1",
                "local_port": "18121",
                "metadata": json.dumps({
                    "type": "sop-runtime",
                    "runtime_id": "youtube-wiki-extra",
                    "channel_url": "https://youtube-wiki-extra.example.com",
                    "ui_url": "https://sop-ui-prototype.chxyka.ccwu.cc",
                }),
            })

        self._send_json({"tunnels": tunnels})


class VerifyRuntimeInventoryTest(unittest.TestCase):
    def run_server(self, mode="success"):
        class Handler(RuntimeInventoryHandler):
            pass

        Handler.mode = mode
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server

    def test_verify_runtime_inventory_success(self):
        server = self.run_server()
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--tunnel-api={endpoint}",
                f"--expect-runtime=youtube-wiki-222|youtube-wiki-222|{endpoint}/runtime",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        self.assertIn("[runtime-inventory] ok", result.stdout)
        self.assertIn("sop_runtimes: youtube-wiki-222", result.stdout)
        self.assertIn("expected_runtimes: youtube-wiki-222", result.stdout)

    def test_verify_runtime_inventory_rejects_missing_expected_runtime(self):
        server = self.run_server(mode="missing")
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--tunnel-api={endpoint}",
                f"--expect-runtime=youtube-wiki-222|youtube-wiki-222|{endpoint}/runtime",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected Runtime missing", result.stderr)

    def test_verify_runtime_inventory_rejects_runtime_like_bad_metadata(self):
        server = self.run_server(mode="bad_runtime_like")
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [str(VERIFY_SCRIPT), f"--tunnel-api={endpoint}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("looks like a Runtime channel", result.stderr)

    def test_verify_runtime_inventory_strict_rejects_extra_runtime(self):
        server = self.run_server(mode="extra")
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                f"--tunnel-api={endpoint}",
                f"--expect-runtime=youtube-wiki-222|youtube-wiki-222|{endpoint}/runtime",
                "--strict",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("extra SOP Runtime tunnels", result.stderr)


if __name__ == "__main__":
    unittest.main()
