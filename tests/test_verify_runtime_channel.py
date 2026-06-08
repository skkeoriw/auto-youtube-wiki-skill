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
        "ui_url": "https://sop-ui-prototype.chxyka.ccwu.cc",
        "wiki_repo": "skkeoriw/wiki-test",
        "supported_sop_types": ["runtime-provisioning", "youtube-research-wiki"],
        "auto_domain_source": {
            "mode": "managed",
            "repo": "https://github.com/ChangfengHU/auto-domain-cli.git",
            "ref": "main",
            "commit": "1d4d9aa",
        },
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
            override = self.metadata_override
            if override is not None:
                try:
                    override_data = json.loads(override)
                except (TypeError, json.JSONDecodeError):
                    override_data = None
                if isinstance(override_data, dict):
                    override_data["channel_url"] = endpoint
                    override_data["spi_base_url"] = f"{endpoint}/api/sop"
                    override = json.dumps(override_data)
            raw_metadata = (
                override
                if override is not None
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
                "--expect-ui-url=https://sop-ui-prototype.chxyka.ccwu.cc",
                "--expect-auto-domain-source-mode=managed",
                "--expect-auto-domain-source-repo=https://github.com/ChangfengHU/auto-domain-cli.git",
                "--expect-auto-domain-source-ref=main",
                "--expect-auto-domain-source-commit=1d4d9aa",
                "--expect-sop-type=runtime-provisioning",
                "--expect-sop-type=youtube-research-wiki",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        self.assertIn("[runtime-channel] ok: youtube-wiki-test", result.stdout)
        self.assertIn("repo: skkeoriw/wiki-test", result.stdout)
        self.assertIn("ui_url: https://sop-ui-prototype.chxyka.ccwu.cc", result.stdout)
        self.assertIn("auto_domain_source: managed https://github.com/ChangfengHU/auto-domain-cli.git@1d4d9aa", result.stdout)
        self.assertIn("supported_sop_types: runtime-provisioning, youtube-research-wiki", result.stdout)

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

    def test_verify_runtime_channel_rejects_auto_domain_source_mismatch(self):
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
                "--expect-auto-domain-source-commit=oldcommit",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata.auto_domain_source.commit", result.stderr)

    def test_verify_runtime_channel_rejects_auto_domain_ref_mismatch(self):
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
                "--expect-auto-domain-source-ref=release",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata.auto_domain_source.ref", result.stderr)

    def test_verify_runtime_channel_rejects_ui_url_mismatch(self):
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
                "--expect-ui-url=https://sop-ui.chxyka.ccwu.cc",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata.ui_url", result.stderr)

    def test_verify_runtime_channel_rejects_missing_sop_type(self):
        metadata = dict(RuntimeChannelHandler.tunnel_metadata)
        metadata["supported_sop_types"] = ["youtube-research-wiki"]
        server = self.run_server(metadata_override=json.dumps(metadata))
        endpoint = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [
                str(VERIFY_SCRIPT),
                "--name=youtube-wiki-test",
                f"--endpoint={endpoint}",
                f"--tunnel-api={endpoint}",
                "--expect-runtime-id=youtube-wiki-test",
                "--expect-repo=skkeoriw/wiki-test",
                "--expect-sop-type=runtime-provisioning",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata.supported_sop_types missing: runtime-provisioning", result.stderr)


if __name__ == "__main__":
    unittest.main()
