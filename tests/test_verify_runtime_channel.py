#!/usr/bin/env python3

import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify-runtime-channel.sh"


class RuntimeChannelHandler(BaseHTTPRequestHandler):
    tunnel_metadata = {
        "title": "youtube-wiki-test",
        "type": "sop-runtime",
        "runtime_id": "youtube-wiki-test",
        "channel_name": "youtube-wiki-test",
        "channel_url": None,
        "spi_base_url": None,
        "wiki_repo": "skkeoriw/wiki-test",
    }
    metadata_override = None

    def log_message(self, _format, *_args):
        return

    def _send_json(self, body, status=200):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        if self.path == "/api/sop":
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        endpoint = f"http://127.0.0.1:{self.server.server_port}"
        if self.path == "/admin/tunnels":
            metadata = dict(self.tunnel_metadata)
            metadata["channel_url"] = endpoint
            metadata["spi_base_url"] = f"{endpoint}/api/sop"
            raw_metadata = (
                self.metadata_override
                if self.metadata_override is not None
                else json.dumps(metadata)
            )
            self._send_json({
                "tunnels": [{
                    "subdomain": "youtube-wiki-test",
                    "status": "active",
                    "local_status": "ok",
                    "client_ip": "127.0.0.1",
                    "local_port": "18121",
                    "metadata": raw_metadata,
                }]
            })
            return

        if self.path == "/api/sop":
            self._send_json({
                "runtime": "youtube-wiki-test",
                "runtime_id": "youtube-wiki-test",
                "sops": [{
                    "instance_id": "wiki-test",
                    "repo": "skkeoriw/wiki-test",
                    "enabled": True,
                }],
            })
            return

        self.send_response(404)
        self.end_headers()


class VerifyRuntimeChannelTest(unittest.TestCase):
    def run_server(self, metadata_override=None):
        class Handler(RuntimeChannelHandler):
            pass

        Handler.metadata_override = metadata_override
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server

    def test_verify_runtime_channel_success(self):
        server = self.run_server()
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                "--name=youtube-wiki-test",
                f"--endpoint={endpoint}",
                f"--tunnel-api={endpoint}",
                "--expect-runtime-id=youtube-wiki-test",
                "--expect-repo=skkeoriw/wiki-test",
                "--expect-port=18121",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        self.assertIn("[runtime-channel] ok: youtube-wiki-test", result.stdout)
        self.assertIn("repo: skkeoriw/wiki-test", result.stdout)

    def test_verify_runtime_channel_rejects_truncated_metadata(self):
        server = self.run_server(metadata_override='{"title":')
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                "--name=youtube-wiki-test",
                f"--endpoint={endpoint}",
                f"--tunnel-api={endpoint}",
                "--expect-runtime-id=youtube-wiki-test",
                "--expect-repo=skkeoriw/wiki-test",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata is not valid JSON", result.stderr)


if __name__ == "__main__":
    unittest.main()
