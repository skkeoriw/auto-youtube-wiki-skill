#!/usr/bin/env python3

import importlib.util
import http.server
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "bridge", Path(__file__).resolve().parents[1] / "scripts" / "bridge.py"
)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class ArtifactResolutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.wiki = Path(self.temp.name)
        self.original_runtime_management_config_path = bridge.RUNTIME_MANAGEMENT_CONFIG_PATH
        bridge.RUNTIME_MANAGEMENT_CONFIG_PATH = self.wiki / "runtime-management-config.json"
        self.addCleanup(lambda: setattr(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", self.original_runtime_management_config_path))
        (self.wiki / "raw/pipeline-runs/pipe-1/nodes").mkdir(parents=True)
        (self.wiki / "raw/notebooklm-analysis").mkdir(parents=True)
        (self.wiki / "wiki/entities").mkdir(parents=True)
        (self.wiki / "raw/notebooklm-analysis/report.md").write_text("# Report\nhello", encoding="utf-8")
        (self.wiki / "wiki/entities/Agent.md").write_text("# Agent", encoding="utf-8")
        (self.wiki / "index.md").write_text("# Index", encoding="utf-8")
        (self.wiki / "raw/pipeline-runs/pipe-1/nodes/notebooklm-research.json").write_text(
            json.dumps({"status": "done", "run_id": "run-b"}), encoding="utf-8"
        )
        (self.wiki / "raw/pipeline-runs/pipe-1/nodes/wiki-build.json").write_text(
            json.dumps({"status": "done", "run_id": "run-c"}), encoding="utf-8"
        )
        (self.wiki / "raw/pipeline-runs/pipe-1/context.json").write_text(
            json.dumps({
                "pipeline_id": "pipe-1",
                "source_url": "https://example.com/video",
                "stage_b": {"output_files": ["raw/notebooklm-analysis/report.md"]},
                "stage_c": {"file_paths": ["wiki/entities/Agent.md"]},
            }),
            encoding="utf-8",
        )
        (self.wiki / "raw/pipeline-runs/pipe-1/nodes/wiki-build").mkdir()
        (self.wiki / "raw/pipeline-runs/pipe-1/nodes/wiki-build/input.json").write_text(
            json.dumps({"resolved_inputs": {"reports": ["frozen-report.md"]}}), encoding="utf-8"
        )
        (self.wiki / "raw/pipeline-runs/pipe-1/nodes/wiki-build/capabilities.json").write_text(
            json.dumps({"git": {"status": "done", "commit": "abc123"}}), encoding="utf-8"
        )
        (self.wiki / "raw/pipeline-runs/pipe-1/nodes/wiki-build/plan.json").write_text(
            json.dumps({"status": "done", "max_pages": 40}), encoding="utf-8"
        )
        (self.wiki / "raw/pipeline-runs/pipe-1/run.json").write_text(
            json.dumps({
                "pipeline_id": "pipe-1",
                "status": "done",
                "nodes": {"notebooklm-research": "done", "wiki-build": "done"},
                "started_at": "2026-06-05T00:00:00Z",
                "updated_at": "2026-06-05T00:02:00Z",
            }), encoding="utf-8"
        )
        (self.wiki / "raw/pipeline-runs/pipe-1/artifacts.json").write_text(
            json.dumps([{"id": "artifact-1", "path": "wiki/entities/Agent.md"}]), encoding="utf-8"
        )
        (self.wiki / "raw/pipeline-runs/pipe-1/events.jsonl").write_text(
            '{"sequence":1,"event":"git.committed"}\n'
            '{"sequence":2,"event":"telegram.sent"}\n',
            encoding="utf-8",
        )
        self.sop = {
            "id": "test",
            "wiki_local_path": str(self.wiki),
            "nodes": {
                "notebooklm-research": {
                    "inputs": {"source_url": "context.source_url"},
                    "outputs": {"reports": "raw/notebooklm-analysis/*.md"},
                },
                "youtube-fetch": {
                    "outputs": {"source_url": "context.source_url"},
                },
                "wiki-build": {
                    "title": "Wiki Build",
                    "skill": "sop-wiki-build",
                    "webhook_route": "sop-wiki-build",
                    "inputs": {"reports": "notebooklm-research.outputs.reports"},
                    "outputs": {"index": "index.md", "pages": "wiki/**"},
                },
                "retry": {
                    "title": "Retry",
                    "mode": "manual",
                    "outputs": {},
                },
            },
        }
        run_file = self.wiki / "raw/pipeline-runs/pipe-1/run.json"
        run = json.loads(run_file.read_text(encoding="utf-8"))
        run["nodes"]["retry"] = "done"
        run_file.write_text(json.dumps(run), encoding="utf-8")
        (self.wiki / "raw/pipeline-runs/pipe-1/dag.json").write_text(
            json.dumps({
                "pipeline_id": "pipe-1",
                "nodes": {
                    "notebooklm-research": self.sop["nodes"]["notebooklm-research"],
                    "wiki-build": self.sop["nodes"]["wiki-build"],
                    "retry": self.sop["nodes"]["retry"],
                },
                "edges": [
                    {"source": "notebooklm-research", "target": "wiki-build"},
                    {"source": "wiki-build", "target": "retry"},
                ],
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_resolves_context_and_upstream_artifacts(self):
        detail = bridge.node_runtime_detail(self.sop, "pipe-1", "wiki-build")
        self.assertEqual(detail["resolved_inputs"]["reports"], ["frozen-report.md"])
        self.assertEqual(detail["actual_outputs"]["pages"], ["wiki/entities/Agent.md"])
        self.assertEqual(detail["validation"]["status"], "passed")
        self.assertEqual(detail["artifacts"][0]["producer"], "wiki-build")
        self.assertIn("preview", detail["artifacts"][0])
        self.assertEqual(detail["capabilities"]["git"]["commit"], "abc123")
        self.assertEqual(detail["plan"]["max_pages"], 40)
        self.assertIn("definition", detail)
        self.assertIn("inputs", detail)
        self.assertIn("actions", detail)
        self.assertIn("outputs", detail)
        self.assertIn("troubleshooting", detail)
        self.assertEqual(detail["definition"]["title"], "Wiki Build")
        self.assertEqual(detail["inputs"]["resolved"]["reports"], ["frozen-report.md"])
        self.assertIn("pages", detail["outputs"]["artifact_explanations"])
        self.assertTrue(detail["troubleshooting"]["failure_hints"])

    def test_runtime_management_node_explanation_fallback(self):
        (self.wiki / "raw/pipeline-runs/pipe-1/nodes/clone-runtime-repos.json").write_text(
            json.dumps({
                "pipeline_id": "pipe-1",
                "node_id": "clone-runtime-repos",
                "status": "done",
                "title": "Clone Runtime Repos",
                "purpose": "Clone or fast-forward repos",
                "declared_inputs": {"github_token": "env/request secret"},
                "resolved_inputs": {"runtime_id": "runtime-34-29-222-183"},
                "declared_outputs": {"repo_checkout_report": "raw/provision/pipe-1/repo_checkout_report.json"},
                "actual_outputs": {},
                "validation": {"status": "passed"},
            }),
            encoding="utf-8",
        )
        self.sop["nodes"]["clone-runtime-repos"] = {"title": "Clone Runtime Repos", "outputs": {}}
        detail = bridge.node_runtime_detail(self.sop, "pipe-1", "clone-runtime-repos")

        self.assertEqual(detail["definition"]["title_zh"], "拉取 Runtime 仓库")
        self.assertIn("GitHub", " ".join(detail["troubleshooting"]["failure_hints"]))
        self.assertEqual(detail["inputs"]["secrets"][0]["key"], "github_token")
        self.assertIn("repo_checkout_report", detail["outputs"]["artifact_explanations"])

    def test_missing_outputs_are_reported(self):
        (self.wiki / "index.md").unlink()
        detail = bridge.node_runtime_detail(self.sop, "pipe-1", "wiki-build")
        self.assertEqual(detail["validation"]["status"], "warning")
        self.assertEqual(detail["validation"]["missing_outputs"], ["index"])

    def test_pattern_scan_is_only_a_discovered_candidate(self):
        context_file = self.wiki / "raw/pipeline-runs/pipe-1/context.json"
        context = json.loads(context_file.read_text(encoding="utf-8"))
        context["stage_c"]["file_paths"] = []
        context_file.write_text(json.dumps(context), encoding="utf-8")
        detail = bridge.node_runtime_detail(self.sop, "pipe-1", "wiki-build")
        self.assertEqual(detail["actual_outputs"]["pages"], [])
        self.assertEqual(detail["discovered_candidates"][0]["path"], "wiki/entities/Agent.md")
        self.assertEqual(detail["discovered_candidates"][0]["ownership"], "unconfirmed")

    def test_historical_pattern_scan_never_becomes_actual_output(self):
        context_file = self.wiki / "raw/pipeline-runs/pipe-1/context.json"
        context_file.unlink()
        detail = bridge.node_runtime_detail(self.sop, "pipe-1", "notebooklm-research")
        self.assertEqual(detail["actual_outputs"]["reports"], [])
        self.assertEqual(detail["artifacts"], [])
        self.assertEqual(
            [candidate["path"] for candidate in detail["discovered_candidates"]],
            ["raw/notebooklm-analysis/report.md"],
        )

    def test_recorded_outputs_take_precedence(self):
        node_file = self.wiki / "raw/pipeline-runs/pipe-1/nodes/wiki-build.json"
        node_file.write_text(json.dumps({
            "status": "done",
            "run_id": "run-c",
            "actual_outputs": {"pages": ["wiki/entities/Agent.md"], "index": ["index.md"]},
            "validation": {"status": "passed", "missing_outputs": [], "unexpected_outputs": []},
        }), encoding="utf-8")
        detail = bridge.node_runtime_detail(self.sop, "pipe-1", "wiki-build")
        self.assertTrue(all(artifact["resolution"] == "recorded" for artifact in detail["artifacts"]))
        self.assertEqual(detail["validation"]["status"], "passed")

    def test_resolves_scalar_context_output(self):
        detail = bridge.node_runtime_detail(self.sop, "pipe-1", "youtube-fetch")
        self.assertEqual(detail["actual_outputs"]["source_url"], "https://example.com/video")
        self.assertEqual(detail["validation"]["status"], "passed")

    def test_path_traversal_is_rejected(self):
        self.assertIsNone(bridge.safe_artifact_path(self.wiki, "../secret.txt"))

    def test_runtime_inheritance_preview_masks_secret_values(self):
        env_file = self.wiki / ".agent-brain-plugins.env"
        env_file.write_text(
            "GITHUB_TOKEN=test_visible_secret_value\n"
            "NOTEBOOKLM_BRIDGE_URL=https://notebooklm-bridge.example/run\n"
            "NOTEBOOKLM_BRIDGE_TOKEN=bridge-secret-token\n"
            "WIKI_VERTEX_MODEL=gemini-1.5-pro\n",
            encoding="utf-8",
        )
        sop = {"id": "runtime-management", "instance_id": "runtime-management", "sop_type": "runtime-management"}
        config_path = self.wiki / ".sop/runtime-management/config.json"
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path), patch.dict(os.environ, {
            "YOUTUBE_WIKI_ENV_FILE": str(env_file),
            "GITHUB_TOKEN": "",
            "NOTEBOOKLM_BRIDGE_URL": "",
            "NOTEBOOKLM_BRIDGE_TOKEN": "",
            "WIKI_VERTEX_MODEL": "",
            "SOP_UI_URL": "",
        }, clear=False):
            preview = bridge.runtime_config_inheritance_preview(sop)

        by_key = {item["key"]: item for item in preview["items"]}
        self.assertEqual(by_key["GITHUB_TOKEN"]["source"], "env_file")
        self.assertTrue(by_key["GITHUB_TOKEN"]["secret"])
        self.assertNotIn("visible_secret_value", json.dumps(preview))
        self.assertEqual(by_key["NOTEBOOKLM_BRIDGE_URL"]["masked_value"], "https://notebooklm-bridge.example/run")
        self.assertEqual(by_key["WIKI_VERTEX_MODEL"]["masked_value"], "gemini-1.5-pro")
        self.assertEqual(by_key["SOP_UI_URL"]["source"], "missing")

    def test_runtime_management_config_is_inherited_and_injected(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"
        sop = {"id": "runtime-management", "instance_id": "runtime-management", "sop_type": "runtime-management"}
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path), patch.dict(os.environ, {
            "CLOUDFLARE_API_KEY": "",
            "SOP_UI_URL": "",
        }, clear=False):
            changed = bridge.save_runtime_management_config({
                "CLOUDFLARE_API_KEY": "cloudflare-secret-value",
                "RUNTIME_SETTINGS_BACKEND": "d1",
                "RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID": "account-id",
                "RUNTIME_SETTINGS_D1_DATABASE_ID": "database-id",
                "SOP_UI_URL": "https://sop-ui.example",
            })
            preview = bridge.runtime_config_inheritance_preview(sop)
            merged = bridge.inject_runtime_management_config({"action": "create-runtime"})

        by_key = {item["key"]: item for item in preview["items"]}
        self.assertEqual(sorted(changed.keys()), [
            "CLOUDFLARE_API_KEY",
            "RUNTIME_SETTINGS_BACKEND",
            "RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID",
            "RUNTIME_SETTINGS_D1_DATABASE_ID",
            "SOP_UI_URL",
        ])
        self.assertEqual(by_key["CLOUDFLARE_API_KEY"]["source"], "management_config")
        self.assertEqual(by_key["CLOUDFLARE_API_KEY"]["masked_value"], "clo***lue")
        self.assertEqual(by_key["RUNTIME_SETTINGS_BACKEND"]["category"], "settings")
        self.assertEqual(by_key["RUNTIME_SETTINGS_D1_DATABASE_ID"]["source"], "management_config")
        self.assertEqual(by_key["SOP_UI_URL"]["masked_value"], "https://sop-ui.example")
        self.assertEqual(merged["CLOUDFLARE_API_KEY"], "cloudflare-secret-value")
        self.assertEqual(merged["RUNTIME_SETTINGS_BACKEND"], "d1")
        self.assertEqual(merged["RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID"], "account-id")
        self.assertEqual(merged["RUNTIME_SETTINGS_D1_DATABASE_ID"], "database-id")
        self.assertEqual(merged["SOP_UI_URL"], "https://sop-ui.example")
        self.assertIn("CLOUDFLARE_API_KEY", merged["_management_config_injected"])
        self.assertIn("RUNTIME_SETTINGS_D1_DATABASE_ID", merged["_management_config_injected"])

    def test_runtime_management_config_save_cf_api_key_alias_is_canonicalized(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"
        sop = {"id": "runtime-management", "instance_id": "runtime-management", "sop_type": "runtime-management"}
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path), patch.dict(os.environ, {
            "CF_API_KEY": "cf-alias-key",
            "CLOUDFLARE_API_KEY": "",
        }, clear=False):
            changed = bridge.save_runtime_management_config({
                "CF_API_KEY": "cf-alias-key",
            })
            preview = bridge.runtime_management_config_preview(sop)
            merged = bridge.inject_runtime_management_config({"action": "create-runtime"})

        by_key = {item["key"]: item for item in preview["items"]}
        self.assertEqual(changed, {"CLOUDFLARE_API_KEY": "cf-alias-key"})
        self.assertEqual(by_key["CLOUDFLARE_API_KEY"]["source"], "management_config")
        self.assertNotIn("CF_API_KEY", [item["key"] for item in preview["items"]])
        self.assertEqual(merged["CLOUDFLARE_API_KEY"], "cf-alias-key")

    def test_runtime_management_config_injects_target_ssh_defaults(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"
        sop = {"id": "runtime-management", "instance_id": "runtime-management", "sop_type": "runtime-management"}
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path):
            changed = bridge.save_runtime_management_config({
                "RUNTIME_TARGET_SSH_COMMAND": "ssh -i ~/.ssh/id_ed25519 user@34.29.222.183",
                "RUNTIME_TARGET_PRIVATE_KEY": "target-private-key",
                "RUNTIME_TARGET_RUNTIME_ID": "runtime-34-29-222-183",
            })
            preview = bridge.runtime_management_config_preview(sop)
            merged = bridge.inject_runtime_management_config({"action": "delete-runtime"})

        by_key = {item["key"]: item for item in preview["items"]}
        self.assertEqual(sorted(changed.keys()), [
            "RUNTIME_TARGET_PRIVATE_KEY",
            "RUNTIME_TARGET_RUNTIME_ID",
            "RUNTIME_TARGET_SSH_COMMAND",
        ])
        self.assertEqual(by_key["RUNTIME_TARGET_SSH_COMMAND"]["source"], "management_config")
        self.assertTrue(by_key["RUNTIME_TARGET_PRIVATE_KEY"]["secret"])
        self.assertEqual(merged["ssh_command"], "ssh -i ~/.ssh/id_ed25519 user@34.29.222.183")
        self.assertEqual(merged["private_key"], "target-private-key")
        self.assertEqual(merged["runtime_id"], "runtime-34-29-222-183")
        self.assertIn("ssh_command", merged["_management_config_injected"])

    def test_runtime_management_config_does_not_override_request_machine_key(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path):
            bridge.save_runtime_management_config({
                "RUNTIME_TARGET_SSH_COMMAND": "ssh -i ~/.ssh/id_ed25519 default@34.29.222.183",
                "RUNTIME_TARGET_PRIVATE_KEY": "stale-management-private-key",
                "RUNTIME_TARGET_PRIVATE_KEY_B64": "STALEB64",
                "RUNTIME_TARGET_RUNTIME_ID": "runtime-34-29-222-183",
            })
            merged = bridge.inject_runtime_management_config({
                "action": "delete-runtime",
                "ssh_command": "ssh -i ~/.ssh/id_ed25519 machine@34.134.172.74",
                "private_key_b64": "MACHINEB64",
                "runtime_id": "runtime-34-134-172-74",
            })

        self.assertEqual(merged["ssh_command"], "ssh -i ~/.ssh/id_ed25519 machine@34.134.172.74")
        self.assertEqual(merged["private_key_b64"], "MACHINEB64")
        self.assertEqual(merged["runtime_id"], "runtime-34-134-172-74")
        self.assertNotIn("private_key", merged)
        self.assertNotIn("ssh_private_key", merged)
        self.assertNotIn("private_key_b64", merged.get("_management_config_injected", []))
        self.assertNotIn("private_key", merged.get("_management_config_injected", []))

    def test_runtime_management_config_does_not_inject_key_when_password_provided(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path):
            bridge.save_runtime_management_config({
                "RUNTIME_TARGET_PRIVATE_KEY": "stale-management-private-key",
            })
            merged = bridge.inject_runtime_management_config({
                "action": "delete-runtime",
                "ssh_password": "machine-password",
            })

        self.assertEqual(merged["ssh_password"], "machine-password")
        self.assertNotIn("private_key", merged)
        self.assertNotIn("ssh_private_key", merged)

    def test_runtime_management_config_machine_id_secret_wins_over_default_key(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            self.assertIn("/api/sop/v1/machines/machine-34/resolve", request.full_url)
            self.assertEqual(request.get_header("User-agent"), "sop-runtime-bridge/1.0")
            return FakeResponse({
                "ok": True,
                "machine": {
                    "id": "machine-34",
                    "host": "34.134.172.74",
                    "user": "runner",
                    "ssh_command": "ssh -i ~/.ssh/id_ed25519 runner@34.134.172.74",
                    "auth_type": "private_key",
                    "private_key": "machine-private-key",
                },
            })

        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path), \
             patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
            bridge.save_runtime_management_config({
                "RUNTIME_TARGET_PRIVATE_KEY": "stale-management-private-key",
                "RUNTIME_TARGET_PRIVATE_KEY_B64": "STALEB64",
            })
            merged = bridge.inject_runtime_management_config({
                "action": "delete-runtime",
                "machine_id": "machine-34",
                "ssh_command": "ssh stale@34.134.172.74",
            })

        self.assertEqual(merged["machine_id"], "machine-34")
        self.assertEqual(merged["ssh_command"], "ssh -i ~/.ssh/id_ed25519 runner@34.134.172.74")
        self.assertEqual(merged["target_host"], "34.134.172.74")
        self.assertEqual(merged["private_key_b64"], "bWFjaGluZS1wcml2YXRlLWtleQ==")
        self.assertNotEqual(merged.get("private_key"), "stale-management-private-key")
        self.assertNotIn("private_key", merged.get("_management_config_injected", []))
        self.assertIn("private_key_b64", merged.get("_management_config_injected", []))

    def test_runtime_management_config_can_resolve_machine_by_target_host_without_secret(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"
        requested_urls = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            requested_urls.append(request.full_url)
            self.assertEqual(request.get_header("User-agent"), "sop-runtime-bridge/1.0")
            if request.full_url.endswith("/api/sop/v1/machines?page=1&page_size=200"):
                return FakeResponse({
                    "machines": [
                        {"id": "machine-34", "host": "34.134.172.74", "status": "active"},
                    ],
                })
            if request.full_url.endswith("/api/sop/v1/machines/machine-34/resolve"):
                return FakeResponse({
                    "machine": {
                        "id": "machine-34",
                        "host": "34.134.172.74",
                        "ssh_command": "ssh runner@34.134.172.74",
                        "auth_type": "password",
                        "password": "machine-password",
                    },
                })
            raise AssertionError(request.full_url)

        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path), \
             patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
            bridge.save_runtime_management_config({})
            merged = bridge.inject_runtime_management_config({
                "action": "delete-runtime",
                "target_host": "34.134.172.74",
            })

        self.assertEqual(len(requested_urls), 2)
        self.assertEqual(merged["machine_id"], "machine-34")
        self.assertEqual(merged["ssh_command"], "ssh runner@34.134.172.74")
        self.assertEqual(merged["ssh_password"], "machine-password")
        self.assertNotIn("private_key", merged)
        self.assertNotIn("private_key_b64", merged)

    def test_create_runtime_does_not_inject_saved_runtime_identity(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path):
            bridge.save_runtime_management_config({
                "RUNTIME_TARGET_SSH_COMMAND": "ssh -i ~/.ssh/id_ed25519 user@34.57.174.2",
                "RUNTIME_TARGET_PRIVATE_KEY": "target-private-key",
                "RUNTIME_TARGET_RUNTIME_ID": "runtime-34-29-222-183",
                "RUNTIME_TARGET_CHANNEL_URL": "https://runtime-34-29-222-183.example.test",
            })
            merged = bridge.inject_runtime_management_config({"action": "create-runtime"})

        self.assertEqual(merged["ssh_command"], "ssh -i ~/.ssh/id_ed25519 user@34.57.174.2")
        self.assertEqual(merged["private_key"], "target-private-key")
        self.assertNotIn("runtime_id", merged)
        self.assertNotIn("channel_url", merged)
        self.assertIn("ssh_command", merged["_management_config_injected"])
        self.assertNotIn("runtime_id", merged["_management_config_injected"])
        self.assertNotIn("channel_url", merged["_management_config_injected"])

    def test_runtime_management_config_accepts_global_repo_defaults(self):
        config_path = self.wiki / ".sop/runtime-management/config.json"
        sop = {"id": "runtime-management", "instance_id": "runtime-management", "sop_type": "runtime-management"}
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path):
            changed = bridge.save_runtime_management_config({
                "GITHUB_CHANGFENGHU_TOKEN": "github-owner-token",
                "GITHUB_SKKEORIW_TOKEN": "github-runtime-token",
                "AGENT_REPO": "https://github.com/skkeoriw/agent-brain-plugins",
                "AUTO_DOMAIN_REPO": "https://github.com/ChangfengHU/auto-domain-cli",
                "AUTO_DOMAIN_TUNNEL_REPO": "https://github.com/ChangfengHU/cloudflare-youtube-pipeline/tree/main/auto-domain-tunnel",
                "SKILL_PUBLISHER_REPO": "https://github.com/ChangfengHU/skill-publisher",
            })
            preview = bridge.runtime_management_config_preview(sop)
            merged = bridge.inject_runtime_management_config({"action": "create-runtime"})

        by_key = {item["key"]: item for item in preview["items"]}
        self.assertIn("GITHUB_CHANGFENGHU_TOKEN", changed)
        self.assertTrue(by_key["GITHUB_CHANGFENGHU_TOKEN"]["secret"])
        self.assertEqual(by_key["AGENT_REPO"]["category"], "repo")
        self.assertEqual(merged["agent_repo"], "https://github.com/skkeoriw/agent-brain-plugins")
        self.assertEqual(merged["auto_domain_repo"], "https://github.com/ChangfengHU/auto-domain-cli")
        self.assertNotIn("github-owner-token", json.dumps(preview))

    def test_runtime_management_config_initializes_from_current_runtime(self):
        env_file = self.wiki / ".agent-brain-plugins.env"
        env_file.write_text(
            "GITHUB_TOKEN=test-token-from-env-file\n"
            "NOTEBOOKLM_BRIDGE_URL=https://bridge.example\n"
            "CLOUDFLARE_API_KEY=cloudflare-from-env-file\n",
            encoding="utf-8",
        )
        config_path = self.wiki / ".sop/runtime-management/config.json"
        with patch.object(bridge, "RUNTIME_MANAGEMENT_CONFIG_PATH", config_path), patch.dict(os.environ, {
            "YOUTUBE_WIKI_ENV_FILE": str(env_file),
            "GITHUB_TOKEN": "",
            "NOTEBOOKLM_BRIDGE_URL": "",
            "CLOUDFLARE_API_KEY": "",
        }, clear=False):
            changed = bridge.initialize_runtime_management_config()
            preview = bridge.runtime_management_config_preview({"id": "runtime-management"})

        by_key = {item["key"]: item for item in preview["items"]}
        self.assertIn("GITHUB_TOKEN", changed)
        self.assertIn("CLOUDFLARE_API_KEY", changed)
        self.assertEqual(by_key["NOTEBOOKLM_BRIDGE_URL"]["masked_value"], "https://bridge.example")
        self.assertEqual(by_key["CLOUDFLARE_API_KEY"]["masked_value"], "clo***ile")
        self.assertNotIn("cloudflare-from-env-file", json.dumps(preview))

    def test_runtime_management_config_preview_uses_canonical_cloudflare_keys(self):
        sop = {"id": "runtime-management", "instance_id": "runtime-management", "sop_type": "runtime-management"}
        with patch.object(bridge, "read_runtime_management_config", return_value={
            "values": {
                "CLOUDFLARE_EMAIL": "cf@example.com",
                "CLOUDFLARE_API_KEY": "cloudflare-secret",
                "CF_EMAIL": "alias@example.com",
                "CF_API_KEY": "alias-secret",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }):
            preview = bridge.runtime_management_config_preview(sop)

        keys = [item["key"] for item in preview["items"]]
        self.assertIn("CLOUDFLARE_EMAIL", keys)
        self.assertIn("CLOUDFLARE_API_KEY", keys)
        self.assertNotIn("CF_EMAIL", keys)
        self.assertNotIn("CF_API_KEY", keys)

    def test_runtime_settings_d1_save_uses_cloudflare_raw_batch(self):
        requests = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = json.dumps(payload).encode("utf-8")

            def read(self):
                return self.payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout=30):
            body = json.loads(request.data.decode("utf-8"))
            requests.append(body)
            sql = body.get("sql", "")
            if "SELECT key, value, category, secret, source, updated_at, updated_by, version" in sql:
                return FakeResponse({
                    "success": True,
                    "result": [{
                        "results": {
                            "columns": ["key", "value", "category", "secret", "source", "updated_at", "updated_by", "version"],
                            "rows": [["CLOUDFLARE_API_KEY", "old-secret", "cloudflare", 1, "management_config", "2026-01-01T00:00:00Z", "seed", 2]],
                        }
                    }],
                })
            return FakeResponse({"success": True, "result": [{"success": True}]})

        with patch.object(bridge, "RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID", "acct"), \
             patch.object(bridge, "RUNTIME_SETTINGS_D1_DATABASE_ID", "db"), \
             patch.object(bridge, "runtime_settings_cloudflare_headers", return_value={"Authorization": "Bearer token"}), \
             patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
            changed = bridge.runtime_settings_d1_save({
                "CLOUDFLARE_API_KEY": "new-secret",
                "AGENT_REPO": "https://github.com/skkeoriw/agent-brain-plugins",
            }, updated_by="unit-test")

        self.assertEqual(sorted(changed.keys()), ["AGENT_REPO", "CLOUDFLARE_API_KEY"])
        self.assertEqual(len(requests), 3)
        self.assertIn("CREATE TABLE IF NOT EXISTS global_settings", requests[0]["sql"])
        self.assertIn("SELECT key, value, category, secret, source, updated_at, updated_by, version", requests[1]["sql"])
        self.assertIn("INSERT INTO global_settings", requests[2]["batch"][0]["sql"])
        self.assertEqual(requests[2]["batch"][0]["params"][0], "CLOUDFLARE_API_KEY")
        self.assertEqual(requests[2]["batch"][0]["params"][7], 3)
        self.assertEqual(requests[2]["batch"][2]["params"][0], "AGENT_REPO")

    def test_runtime_settings_load_bootstraps_d1_from_local_file(self):
        calls = []

        with patch.object(bridge, "RUNTIME_SETTINGS_BACKEND", "d1"), \
             patch.object(bridge, "RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID", "acct"), \
             patch.object(bridge, "RUNTIME_SETTINGS_D1_DATABASE_ID", "db"), \
             patch.object(bridge, "runtime_settings_cloudflare_headers", return_value={"Authorization": "Bearer token"}), \
             patch.object(bridge, "runtime_settings_load_from_file", return_value={"values": {"CLOUDFLARE_API_KEY": "seed-secret"}, "updated_at": "2026-01-01T00:00:00Z"}), \
             patch.object(bridge, "runtime_settings_d1_has_rows", side_effect=[False, True]), \
             patch.object(bridge, "runtime_settings_d1_save", side_effect=lambda values, updated_by="runtime-management": calls.append((values, updated_by)) or values), \
             patch.object(bridge, "runtime_settings_d1_values", return_value={"CLOUDFLARE_API_KEY": "seed-secret"}):
            data = bridge.runtime_settings_load()

        self.assertEqual(calls, [({"CLOUDFLARE_API_KEY": "seed-secret"}, "bootstrap-from-file")])
        self.assertEqual(data["values"]["CLOUDFLARE_API_KEY"], "seed-secret")
        self.assertEqual(data["backend"], "d1")

    def test_runtime_management_config_save_does_not_require_authorization(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        responses = []

        with (
            patch.object(bridge, "find_sop", return_value={"id": "runtime-management", "instance_id": "runtime-management", "sop_type": "runtime-management"}),
            patch.object(bridge, "save_runtime_management_config", return_value={"SOP_UI_URL": "https://sop-ui-prototype.chxyka.ccwu.cc"}) as save_mock,
            patch.object(bridge, "runtime_management_config_preview", return_value={"backend": "d1", "items": []}),
        ):
            thread.start()
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/sop/runtime-management/config/management",
                data=json.dumps({"values": {"SOP_UI_URL": "https://sop-ui-prototype.chxyka.ccwu.cc"}}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                responses.append((resp.status, json.loads(resp.read().decode("utf-8"))))
        server.shutdown()
        server.server_close()

        self.assertEqual(responses[0][0], 200)
        self.assertEqual(responses[0][1]["status"], "saved")
        self.assertEqual(save_mock.call_count, 1)
        self.assertNotIn("Authorization", save_mock.call_args.kwargs)

    def test_indexed_artifact_preview_is_backfilled(self):
        artifact = {
            "id": "indexed-1",
            "path": "wiki/entities/Agent.md",
            "format": "markdown",
            "mime_type": "text/markdown",
        }
        enriched = bridge.artifact_with_preview(self.sop, artifact)
        self.assertEqual(enriched["preview"], "# Agent")
        self.assertFalse(enriched["preview_truncated"])
        self.assertNotIn("preview", artifact)

    def test_sse_event_replay_and_format(self):
        events_file = self.wiki / "raw/pipeline-runs/pipe-1/events.jsonl"
        events_file.write_text(
            '{"sequence":1,"event":"node.started"}\n{"sequence":2,"event":"node.completed"}\n',
            encoding="utf-8",
        )
        events = bridge.read_run_events(events_file, after_sequence=1)
        self.assertEqual([event["sequence"] for event in events], [2])
        formatted = bridge.format_sse_event(events[0]).decode()
        self.assertIn("id: 2", formatted)
        self.assertIn("event: node.completed", formatted)
        self.assertLessEqual(bridge.SSE_HEARTBEAT_SECONDS, 15)
        self.assertGreater(bridge.SSE_STREAM_WINDOW_SECONDS, 0)

    def test_static_config_returns_manifest_executor(self):
        plugin = self.wiki / "plugin"
        skill = plugin / "skills/sop-wiki-build"
        skill.mkdir(parents=True)
        (skill / "node.yaml").write_text(
            "id: wiki-build\nexecutor:\n  type: agent-skill\n  agent: hermes\n"
            "capabilities:\n  git: {enabled: true, required: false}\n"
            "ui:\n  category: build\n  icon: network\n  stage_letter: C\n  order: 40\n",
            encoding="utf-8",
        )
        sop = {"nodes": {"wiki-build": {"title": "Build", "skill": "sop-wiki-build"}}}
        with patch.dict(os.environ, {"YOUTUBE_WIKI_PLUGIN_DIR": str(plugin)}):
            config = bridge.node_static_config(sop, "wiki-build")
        self.assertEqual(config["executor"]["type"], "agent-skill")
        self.assertEqual(config["executor"]["agent"], "hermes")
        self.assertTrue(config["manifest"]["capabilities"]["git"]["enabled"])
        self.assertEqual(config["ui"]["category"], "build")

    def test_run_summary_exposes_control_console_metrics(self):
        summary = bridge.run_summary(
            self.sop,
            json.loads((self.wiki / "raw/pipeline-runs/pipe-1/run.json").read_text()),
        )
        self.assertEqual(summary["node_count"], 2)
        self.assertEqual(summary["done_count"], 2)
        self.assertEqual(summary["progress"], 100)
        self.assertEqual(summary["artifact_count"], 1)
        self.assertEqual(summary["git_event_count"], 1)
        self.assertEqual(summary["telegram_event_count"], 1)
        self.assertEqual(summary["page_count"], 1)
        self.assertEqual(summary["duration_s"], 120)

    def test_run_dag_snapshot_is_normalized_to_node_list(self):
        snapshot = bridge.normalized_run_dag(self.sop, "pipe-1")
        self.assertEqual(
            [node["id"] for node in snapshot["nodes"]],
            ["notebooklm-research", "wiki-build"],
        )
        self.assertEqual(snapshot["edges"][0]["target"], "wiki-build")

    def test_business_dag_excludes_manual_action_nodes(self):
        dag = bridge.sop_dag(self.sop)
        self.assertNotIn("retry", [node["id"] for node in dag["nodes"]])
        self.assertFalse(any(edge["target"] == "retry" for edge in dag["edges"]))

    def test_root_health_route_supports_auto_domain_local_check(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "runtime_info", return_value={"runtime_id": "test-runtime"}):
            thread.start()
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
                data = json.loads(response.read())
        server.shutdown()
        server.server_close()

        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "sop-bridge")
        self.assertEqual(data["runtime"]["runtime_id"], "test-runtime")

    def test_hermes_smoke_check_uses_server_side_hmac_signature(self):
        captured = {}

        class FakeResponse:
            status = 202
            headers = {"content-type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"status":"accepted"}'

        def fake_urlopen(request, timeout=0):
            captured["request"] = request
            captured["body"] = request.data
            captured["timeout"] = timeout
            return FakeResponse()

        with (
            patch.dict(os.environ, {
                "HERMES_WEBHOOK_URL": "https://hermes.example",
                "HERMES_SMOKE_ROUTE": "sop-runtime-hermes-smoke",
                "HERMES_WEBHOOK_TOKEN": "secret-token",
            }, clear=False),
            patch.object(bridge, "runtime_info", return_value={
                "runtime_id": "runtime-test",
                "channel_url": "https://runtime-test.example",
                "spi_base_url": "https://runtime-test.example/api/sop",
            }),
            patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            status, result = bridge.hermes_smoke_check("你好 你是谁")

        req = captured["request"]
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(result["target_url"], "https://hermes.example/webhooks/sop-runtime-hermes-smoke")
        self.assertIn("x-hub-signature-256", {key.lower(): value for key, value in req.headers.items()})
        self.assertNotIn("secret-token", json.dumps(result, ensure_ascii=False))
        self.assertIn("$HERMES_WEBHOOK_TOKEN", result["curl"])
        self.assertEqual(captured["timeout"], 60)

    def test_hermes_agent_check_runs_local_cli_with_prompt(self):
        captured = {}

        def fake_run(command, input=None, text=False, capture_output=False, timeout=0, env=None):
            captured["command"] = command
            captured["input"] = input
            captured["text"] = text
            captured["capture_output"] = capture_output
            captured["timeout"] = timeout
            captured["term"] = (env or {}).get("TERM")
            return subprocess.CompletedProcess(command, 0, stdout="我是 Hermes\n", stderr="")

        with (
            patch.object(bridge, "hermes_agent_command", return_value="/usr/local/bin/hermes"),
            patch.dict(os.environ, {"HERMES_AGENT_CHECK_TIMEOUT": "9"}, clear=False),
        ):
            status, result = bridge.hermes_agent_check("你好 你是谁", runner=fake_run)

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "hermes-agent-chat-check")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["response"], "我是 Hermes")
        self.assertEqual(captured["command"], ["/usr/local/bin/hermes", "--oneshot", "你好 你是谁"])
        self.assertIsNone(captured["input"])
        self.assertTrue(captured["text"])
        self.assertTrue(captured["capture_output"])
        self.assertEqual(captured["timeout"], 9)
        self.assertEqual(captured["term"], "xterm-256color")
        self.assertIn("/usr/local/bin/hermes", result["manual_command"])

    def test_hermes_agent_check_reports_missing_cli(self):
        with patch.object(bridge, "hermes_agent_command", return_value=""):
            status, result = bridge.hermes_agent_check("你好")

        self.assertEqual(status, 503)
        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "hermes-agent-chat-check")
        self.assertIn("not installed", result["reason"])

    def test_hermes_agent_check_rejects_cli_error_text_even_with_zero_exit(self):
        def fake_run(command, input=None, text=False, capture_output=False, timeout=0, env=None):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="API call failed after 3 retries: HTTP 530 — Cloudflare Tunnel error",
                stderr="",
            )

        with patch.object(bridge, "hermes_agent_command", return_value="/usr/local/bin/hermes"):
            status, result = bridge.hermes_agent_check("你好", runner=fake_run)

        self.assertEqual(status, 502)
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("error response", result["reason"])

    def test_hermes_smoke_check_retries_transient_502(self):
        calls = []

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.headers = {"content-type": "application/json" if status == 202 else "text/plain"}
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self._body

        def fake_urlopen(request, timeout=0):
            calls.append(request)
            if len(calls) == 1:
                return FakeResponse(502, b"Local service error: fetch failed")
            return FakeResponse(202, b'{"status":"accepted"}')

        payload = {
            "message": "你好 你是谁",
            "runtime_id": "runtime-test",
            "channel_url": "https://runtime-test.example",
            "spi_base_url": "https://runtime-test.example/api/sop",
            "source": "sop-runtime-bridge",
            "mode": "hermes-smoke-check",
        }
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = bridge.hmac.new(b"secret-token", data, bridge.hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": "Mozilla/5.0 SOP-Runtime-Hermes-Smoke/1.0",
            "X-Hub-Signature-256": f"sha256={signature}",
        }

        http_status, content_type, response_body, error, attempts = bridge.hermes_post_with_retry(
            "https://hermes.example/webhooks/sop-runtime-hermes-smoke",
            data,
            headers,
            attempts=3,
            opener=fake_urlopen,
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(http_status, 202)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(response_body, '{"status":"accepted"}')
        self.assertEqual(error, "")
        self.assertEqual(attempts, 2)
        self.assertGreaterEqual(len(calls), 2)

    def test_hermes_smoke_check_retries_tunnel_offline(self):
        calls = []

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.headers = {"content-type": "application/json"}
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self._body

        def fake_urlopen(request, timeout=0):
            calls.append(request)
            if len(calls) == 1:
                return FakeResponse(502, b'{"error":"Tunnel offline"}')
            return FakeResponse(202, b'{"status":"accepted"}')

        http_status, content_type, response_body, error, attempts = bridge.hermes_post_with_retry(
            "https://hermes.example/webhooks/sop-runtime-hermes-smoke",
            b"{}",
            {"Content-Type": "application/json"},
            attempts=3,
            opener=fake_urlopen,
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(http_status, 202)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(response_body, '{"status":"accepted"}')
        self.assertEqual(error, "")
        self.assertEqual(attempts, 2)
        self.assertEqual(len(calls), 2)

    def test_node_registry_and_actions_routes(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/sop/test"
            with urllib.request.urlopen(f"{base}/nodes", timeout=3) as response:
                nodes = json.loads(response.read())
            self.assertEqual(nodes["nodes"][0]["actions"]["retry"]["method"], "POST")
            self.assertIn("--action=inspect", nodes["nodes"][0]["cli"]["inspect"])
            self.assertIn("modules", nodes["nodes"][0])
            self.assertIn("executor", [module["id"] for module in nodes["nodes"][0]["modules"]])
            with urllib.request.urlopen(f"{base}/nodes/wiki-build/actions", timeout=3) as response:
                actions = json.loads(response.read())
            self.assertFalse(actions["actions"]["trigger"]["enabled"])
        server.shutdown()
        server.server_close()

    def test_node_module_routes_expose_static_and_run_scoped_data(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/sop/test"
            with urllib.request.urlopen(f"{base}/nodes/wiki-build/modules", timeout=3) as response:
                modules = json.loads(response.read())
            self.assertEqual(modules["node_id"], "wiki-build")
            self.assertIn("skill", [module["id"] for module in modules["modules"]])
            executor_module = next(module for module in modules["modules"] if module["id"] == "executor")
            self.assertEqual(executor_module["lane"], "execution")
            self.assertIsInstance(executor_module["order"], int)
            self.assertEqual(executor_module["contract_version"], "node-module-contract/v1")
            self.assertIn("executor.type", executor_module["schema"])
            self.assertIn("action_count", executor_module["metrics"])
            with urllib.request.urlopen(f"{base}/nodes/wiki-build/modules/skill", timeout=3) as response:
                skill = json.loads(response.read())
            self.assertEqual(skill["module"]["id"], "skill")
            self.assertEqual(skill["detail"]["skill"]["id"], "sop-wiki-build")
            with urllib.request.urlopen(f"{base}/runs/pipe-1/nodes/wiki-build/modules/inputs", timeout=3) as response:
                inputs = json.loads(response.read())
            self.assertEqual(inputs["pipeline_id"], "pipe-1")
            self.assertEqual(inputs["detail"]["resolved_inputs"]["reports"], ["frozen-report.md"])
            with urllib.request.urlopen(f"{base}/runs/pipe-1/nodes/wiki-build/modules/outputs", timeout=3) as response:
                outputs = json.loads(response.read())
            self.assertEqual(outputs["detail"]["actual_outputs"]["pages"], ["wiki/entities/Agent.md"])
            self.assertEqual(outputs["module"]["lane"], "contract")
            self.assertEqual(outputs["module"]["metrics"]["actual"], 2)
            with urllib.request.urlopen(f"{base}/runs/pipe-1/nodes/wiki-build/modules/capabilities", timeout=3) as response:
                caps = json.loads(response.read())
            self.assertEqual(caps["detail"]["run_capabilities"]["git"]["commit"], "abc123")
        server.shutdown()
        server.server_close()

    def test_run_productization_routes(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/sop/test/runs/pipe-1"
            with urllib.request.urlopen(base, timeout=3) as response:
                run = json.loads(response.read())
            self.assertEqual(run["progress"], 100)
            self.assertIn("node_states", run)
            with urllib.request.urlopen(f"{base}/dag", timeout=3) as response:
                dag = json.loads(response.read())
            self.assertIsInstance(dag["nodes"], list)
            with urllib.request.urlopen(f"{base}/artifacts", timeout=3) as response:
                artifacts = json.loads(response.read())
            self.assertEqual(artifacts[0]["id"], "artifact-1")
            with urllib.request.urlopen(f"{base}/artifact-candidates", timeout=3) as response:
                candidates = json.loads(response.read())
            self.assertEqual(candidates["pipeline_id"], "pipe-1")
        server.shutdown()
        server.server_close()

    def test_instance_execution_semantic_routes(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/sop/instances/test"
            with urllib.request.urlopen(base, timeout=3) as response:
                instance = json.loads(response.read())
            self.assertEqual(instance["instance_id"], "test")
            self.assertIn("workflow_binding", instance)
            with urllib.request.urlopen(f"{base}/workflow", timeout=3) as response:
                workflow = json.loads(response.read())
            self.assertEqual(workflow["workflow_binding"]["workflow_id"], "test")
            self.assertIn("dag", workflow)
            with urllib.request.urlopen(f"{base}/executions", timeout=3) as response:
                executions = json.loads(response.read())
            self.assertEqual(executions["executions"][0]["execution_id"], "pipe-1")
            self.assertEqual(executions["executions"][0]["instance_id"], "test")
            with urllib.request.urlopen(f"{base}/executions/pipe-1", timeout=3) as response:
                execution = json.loads(response.read())
            self.assertEqual(execution["execution_id"], "pipe-1")
            with urllib.request.urlopen(f"{base}/executions/pipe-1/nodes/wiki-build", timeout=3) as response:
                node = json.loads(response.read())
            self.assertEqual(node["execution_id"], "pipe-1")
            self.assertEqual(node["instance_id"], "test")
            with self.assertRaises(urllib.error.HTTPError) as missing_node:
                urllib.request.urlopen(f"{base}/executions/pipe-1/nodes/not-in-run", timeout=3)
            self.assertEqual(missing_node.exception.code, 404)
        server.shutdown()
        server.server_close()

    def test_trigger_persists_explicit_notebooklm_fallback_override(self):
        run_dir = self.wiki / "raw/pipeline-runs/pipe-forced"
        run_dir.mkdir(parents=True)
        (self.wiki / "raw/pipeline-context.json").write_text(
            json.dumps({"pipeline_id": "pipe-forced", "source_url": "https://example.com"}),
            encoding="utf-8",
        )
        (run_dir / "context.json").write_text(
            json.dumps({"pipeline_id": "pipe-forced", "source_url": "https://example.com"}),
            encoding="utf-8",
        )
        with patch.object(bridge.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({"pipeline_id": "pipe-forced"})
            run.return_value.stderr = ""
            status, result = bridge.trigger_sop(self.sop, {
                "repo": "skkeoriw/test",
                "input": {
                    "url": "https://example.com",
                    "force_notebooklm_fallback": True,
                },
            })

        self.assertEqual(status, 200)
        self.assertEqual(result["test_overrides"]["force_notebooklm_fallback"], True)
        root_ctx = json.loads((self.wiki / "raw/pipeline-context.json").read_text(encoding="utf-8"))
        run_ctx = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        self.assertTrue(root_ctx["test_overrides"]["force_notebooklm_fallback"])
        self.assertTrue(run_ctx["test_overrides"]["force_notebooklm_fallback"])

    def test_runtime_management_trigger_writes_secret_request_and_run_workspace(self):
        plugin_root = self.wiki / "plugin-root"
        runner = plugin_root / "youtube-wiki/infrastructure/provision_runtime.py"
        runner.parent.mkdir(parents=True)
        runner.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        sop = {
            "id": "runtime-management",
            "sop_type": "runtime-management",
            "repo": "skkeoriw/runtime-management",
            "wiki_local_path": str(self.wiki),
        }

        with patch.dict(os.environ, {"AGENT_BRAIN_PLUGINS_PATH": str(plugin_root)}):
            with patch.object(bridge.subprocess, "Popen") as popen:
                status, result = bridge.trigger_sop(sop, {
                    "action": "create-runtime",
                    "ssh_command": "ssh -i ~/.ssh/id_ed25519 user@34.29.222.183",
                    "private_key": "secret-test-key",
                    "github_token": "secret-token",
                    "dry_run": True,
                    "pipeline_id": "create-runtime-test",
                })

        self.assertEqual(status, 202)
        self.assertEqual(result["pipeline_id"], "create-runtime-test")
        popen.assert_called_once()
        self.assertIn("--dry-run", popen.call_args.args[0])
        request_file = self.wiki / ".sop/secrets/create-runtime-test/request.json"
        self.assertTrue(request_file.exists())
        self.assertEqual(oct(request_file.stat().st_mode & 0o777), "0o600")
        request_data = json.loads(request_file.read_text())
        self.assertEqual(request_data["private_key"], "secret-test-key")
        context_text = (self.wiki / "raw/pipeline-runs/create-runtime-test/context.json").read_text()
        self.assertNotIn("secret-test-key", context_text)
        run = json.loads((self.wiki / "raw/pipeline-runs/create-runtime-test/run.json").read_text())
        self.assertEqual(run["sop_id"], "runtime-management")
        self.assertEqual(run["workflow_id"], "runtime-management")
        self.assertEqual(run["nodes"]["parse-create-runtime-request"], "waiting")
        self.assertEqual(run["nodes"]["parse-delete-runtime-request"], "skipped")
        dag = json.loads((self.wiki / "raw/pipeline-runs/create-runtime-test/dag.json").read_text())
        node_ids = [node["id"] for node in dag["nodes"]]
        self.assertIn("action-router", node_ids)
        self.assertIn("parse-create-runtime-request", node_ids)
        self.assertIn("parse-delete-runtime-request", node_ids)
        self.assertIn("management-summary", node_ids)
        skipped_state = json.loads((self.wiki / "raw/pipeline-runs/create-runtime-test/nodes/parse-delete-runtime-request.json").read_text())
        self.assertEqual(skipped_state["status"], "skipped")
        self.assertEqual(skipped_state["progress"], 100)

    def test_runtime_management_create_instance_uses_instance_branch(self):
        plugin_root = self.wiki / "plugin-root"
        runner = plugin_root / "youtube-wiki/infrastructure/provision_runtime.py"
        runner.parent.mkdir(parents=True)
        runner.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        sop = {
            "id": "runtime-management",
            "sop_type": "runtime-management",
            "repo": "skkeoriw/runtime-management",
            "wiki_local_path": str(self.wiki),
        }

        with patch.dict(os.environ, {"AGENT_BRAIN_PLUGINS_PATH": str(plugin_root)}):
            with patch.object(bridge.subprocess, "Popen") as popen:
                status, result = bridge.trigger_sop(sop, {
                    "action": "create-instance",
                    "runtime_id": "runtime-34-29-222-183",
                    "channel_url": "https://runtime-34-29-222-183.chxyka.ccwu.cc",
                    "ssh_command": "ssh -i ~/.ssh/id_ed25519 user@34.29.222.183",
                    "private_key": "secret-test-key",
                    "github_token": "secret-token",
                    "instance_id": "wiki-sop-new-instance",
                    "repo": "skkeoriw/wiki-sop-new-instance",
                    "dry_run": True,
                    "pipeline_id": "create-instance-test",
                })

        self.assertEqual(status, 202)
        self.assertEqual(result["pipeline_id"], "create-instance-test")
        popen.assert_called_once()
        request_data = json.loads((self.wiki / ".sop/secrets/create-instance-test/request.json").read_text())
        self.assertEqual(request_data["action"], "create-instance")
        self.assertEqual(request_data["instance_id"], "wiki-sop-new-instance")
        context_text = (self.wiki / "raw/pipeline-runs/create-instance-test/context.json").read_text()
        self.assertNotIn("secret-test-key", context_text)
        run = json.loads((self.wiki / "raw/pipeline-runs/create-instance-test/run.json").read_text())
        self.assertEqual(run["nodes"]["parse-create-instance-request"], "waiting")
        self.assertEqual(run["nodes"]["prepare-instance-workspace"], "waiting")
        self.assertEqual(run["nodes"]["parse-create-runtime-request"], "skipped")
        self.assertEqual(run["nodes"]["parse-delete-instance-request"], "skipped")
        dag = json.loads((self.wiki / "raw/pipeline-runs/create-instance-test/dag.json").read_text())
        summary = next(node for node in dag["nodes"] if node["id"] == "management-summary")
        self.assertIn("verify-runtime-visible", summary["needs"])
        self.assertIn("verify-runtime-removed", summary["needs"])
        self.assertIn("verify-instance-visible", summary["needs"])
        self.assertIn("verify-instance-removed", summary["needs"])

    def test_run_routes_prefer_runtime_index(self):
        run_file = self.wiki / "raw/pipeline-runs/pipe-1/run.json"
        run = json.loads(run_file.read_text(encoding="utf-8"))
        run["status"] = "running"
        run["nodes"]["wiki-build"] = "running"
        run["updated_at"] = "2026-06-05T00:03:00Z"
        run_file.write_text(json.dumps(run), encoding="utf-8")

        cls = bridge.run_index_class()
        self.assertIsNotNone(cls)
        store = cls(self.wiki)
        store.upsert_execution({
            "pipeline_id": "pipe-1",
            "sop_id": "test",
            "status": "running",
            "nodes": {"wiki-build": "running"},
            "updated_at": "2026-06-05T00:03:00Z",
        })
        store.upsert_node_state({
            "pipeline_id": "pipe-1",
            "node_id": "wiki-build",
            "attempt": 2,
            "status": "running",
            "progress": 40,
            "resolved_inputs": {"reports": ["indexed-report.md"]},
            "actual_outputs": {"pages": ["indexed-page.md"]},
        }, {"telegram": {"status": "done", "message_preview": "indexed tg"}})
        store.append_event({"pipeline_id": "pipe-1", "event": "node.progress", "node_id": "wiki-build", "data": {"completed": 2}})

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/sop/test/runs/pipe-1"
            with urllib.request.urlopen(base, timeout=3) as response:
                run = json.loads(response.read())
            self.assertEqual(run["index_resolution"], "indexed")
            self.assertEqual(run["node_states"]["wiki-build"]["attempt"], 2)
            with urllib.request.urlopen(f"{base}/events", timeout=3) as response:
                events = json.loads(response.read())
            self.assertEqual(events["events"][0]["event"], "node.progress")
            with urllib.request.urlopen(f"{base}/artifacts", timeout=3) as response:
                artifacts = json.loads(response.read())
            self.assertEqual(artifacts, [])
            with urllib.request.urlopen(f"{base}/nodes/wiki-build", timeout=3) as response:
                node = json.loads(response.read())
            self.assertEqual(node["index_resolution"], "indexed")
            self.assertEqual(node["resolved_inputs"]["reports"], ["indexed-report.md"])
            self.assertEqual(node["capabilities"]["telegram"]["message_preview"], "indexed tg")
        server.shutdown()
        server.server_close()

    def test_node_draft_route_does_not_change_sop_yaml(self):
        (self.wiki / "sop.yaml").write_text("nodes: {}\n", encoding="utf-8")
        before = (self.wiki / "sop.yaml").read_text(encoding="utf-8")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/node-drafts/schema",
                timeout=3,
            ) as response:
                schema_response = json.loads(response.read())
            schema = schema_response["schema"]
            fields = {field["name"]: field for field in schema["fields"]}
            self.assertEqual(schema["schema_id"], "node-draft-schema/v1")
            self.assertTrue(fields["skill_install_command"]["required"])
            self.assertEqual(fields["output_path"]["type"], "path_pattern")
            self.assertFalse(schema["safety"]["production_dag_changed"])
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/node-drafts",
                method="POST",
                data=json.dumps({
                    "skill_install_command": "bash <(curl -fsSL https://skill.vyibc.com/install-demo.sh)",
                    "skill_id": "demo-skill",
                    "node_id": "youtube-cover-image",
                    "title": "YouTube 封面图生成",
                    "upstream": "youtube-deep-research",
                    "upstream_output": "analysis_file",
                    "input_name": "research_report",
                    "output_name": "cover_image",
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 201)
                draft = json.loads(response.read())
            self.assertEqual(draft["validation"]["schema_id"], "node-draft-schema/v1")
            self.assertFalse(draft["validation"]["production_dag_changed"])
            self.assertTrue((Path(draft["draft_path"]) / "node.yaml").exists())
            invalid_request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/node-drafts",
                method="POST",
                data=json.dumps({"skill_id": "bad id"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(invalid_request, timeout=3)
            self.assertEqual(ctx.exception.code, 422)
            invalid = json.loads(ctx.exception.read())
            self.assertEqual(invalid["validation"]["status"], "failed")
            self.assertEqual(invalid["draft_id"], "")
            self.assertIn("skill_install_command", invalid["validation"]["missing_fields"])
            self.assertIn("node_id", invalid["validation"]["missing_fields"])
            conflict_request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/node-drafts",
                method="POST",
                data=json.dumps({
                    "skill_install_command": "bash <(curl -fsSL https://skill.vyibc.com/install-demo.sh)",
                    "skill_id": "demo-skill",
                    "node_id": "wiki-build",
                    "title": "Wiki Build Override",
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as conflict_ctx:
                urllib.request.urlopen(conflict_request, timeout=3)
            self.assertEqual(conflict_ctx.exception.code, 422)
            conflict = json.loads(conflict_ctx.exception.read())
            self.assertEqual(conflict["validation"]["status"], "failed")
            self.assertIn("node_exists", [error["code"] for error in conflict["validation"]["errors"]])
        self.assertEqual((self.wiki / "sop.yaml").read_text(encoding="utf-8"), before)
        server.shutdown()
        server.server_close()

    def test_trigger_node_test_for_node_without_engine_contract_returns_404(self):
        # Single-node test is only supported for nodes the provisioning engine
        # classifies (runtime-management nodes). A youtube-research node like
        # wiki-build has no engine contract, so trigger returns 404 (not 409).
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/nodes/wiki-build/actions/trigger",
                method="POST",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(ctx.exception.code, 404)
        server.shutdown()
        server.server_close()

    def test_business_node_test_plan_resolves_generated_fixture(self):
        sop = dict(self.sop)
        sop["nodes"] = dict(self.sop["nodes"])
        sop["nodes"]["youtube-deep-research"] = {
            "title": "YouTube Deep Research",
            "skill": "sop-youtube-deep-research",
            "webhook_route": "sop-youtube-deep-research",
            "needs": ["youtube-fetch"],
            "inputs": {"source_url": "youtube-fetch.outputs.source_url"},
            "outputs": {"analysis_file": "raw/youtube-deep-research/{pipeline_id}/analysis.md"},
        }
        plan = bridge.build_node_test_plan(sop, "youtube-deep-research", {"input_source": "generated-fixture"})
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["resolved_inputs"][0]["name"], "source_url")
        self.assertEqual(plan["resolved_inputs"][0]["value"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(plan["upstream_nodes"][0]["node_id"], "youtube-fetch")
        self.assertFalse(plan["actions"]["real_execution"]["enabled"])

    def test_business_node_preflight_is_recorded_outside_pipeline_runs(self):
        code, result = bridge.create_node_preflight_test(
            self.sop,
            "wiki-build",
            {"input_source": "existing-run", "pipeline_id": "pipe-1"},
        )
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["test_id"].startswith("node-test-wiki-build-"))
        self.assertTrue((self.wiki / "raw" / "node-tests" / result["test_id"] / "result.json").exists())
        self.assertFalse((self.wiki / "raw" / "pipeline-runs" / result["test_id"]).exists())
        read_back = bridge.read_node_test_result(self.sop, "wiki-build", result["test_id"])
        self.assertEqual(read_back["status"], "done")
        self.assertEqual(read_back["detail"]["input_source"], "existing-run")
        self.assertEqual([step["id"] for step in read_back["steps"]], [
            "load-definition",
            "resolve-instance",
            "resolve-inputs",
            "check-upstream",
            "check-side-effects",
            "build-execution-plan",
        ])
        self.assertTrue(read_back["events"])
        self.assertEqual(read_back["artifacts"][0]["path"], f"raw/node-tests/{result['test_id']}/result.json")

        history = bridge.list_generic_node_tests(self.sop, "wiki-build")
        self.assertEqual(history[0]["test_id"], result["test_id"])

    def test_node_run_records_diagnostic_flow_without_pipeline_run(self):
        sop = dict(self.sop)
        sop["nodes"] = dict(self.sop["nodes"])
        sop["nodes"]["youtube-deep-research"] = {
            "title": "YouTube Deep Research",
            "skill": "sop-youtube-deep-research",
            "webhook_route": "sop-youtube-deep-research",
            "needs": ["youtube-fetch"],
            "inputs": {"source_url": "youtube-fetch.outputs.source_url"},
            "outputs": {"analysis_file": "raw/youtube-deep-research/{pipeline_id}/analysis.md"},
            "infra": {"tg_notify": True, "log_record": True},
        }
        with patch.dict(os.environ, {
            "YOUTUBE_RESEARCH_WORKFLOW_URL": "https://worker.example",
            "YOUTUBE_RESEARCH_WORKFLOW_TOKEN": "worker-token-secret",
            "YOUTUBE_WIKI_TG_TOKEN": "telegram-token-secret",
            "YOUTUBE_WIKI_TG_CHAT_ID": "7796171193",
            "GITHUB_TOKEN": "",
            "GH_TOKEN": "",
            "YOUTUBE_WIKI_ENV_FILE": str(self.wiki / "missing.env"),
        }, clear=False):
            code, result = bridge.create_node_run(
                sop,
                "test",
                "youtube-deep-research",
                {"mode": "probe", "input_source": "generated-fixture", "retry_of": "node-run-old"},
            )

        self.assertEqual(code, 200)
        self.assertTrue(result["node_run_id"].startswith("node-run-youtube-deep-research-"))
        self.assertEqual(result["status"], "done")
        self.assertGreater(result["elapsed_ms"], 0)
        self.assertEqual(result["retry_of"], "node-run-old")
        self.assertEqual(result["created_from"], "generated-fixture")
        self.assertTrue((self.wiki / "raw" / "node-runs" / result["node_run_id"] / "result.json").exists())
        self.assertFalse((self.wiki / "raw" / "pipeline-runs" / result["node_run_id"]).exists())
        self.assertEqual([step["id"] for step in result["steps"]], [
            "create-run",
            "load-definition",
            "resolve-context",
            "resolve-inputs",
            "resolve-config",
            "probe-capabilities",
            "build-execution-plan",
            "execute-or-dry-run",
            "validate-outputs",
            "persist-to-github",
            "send-telegram-notification",
            "persist-artifacts",
        ])
        detail = result["detail"]
        self.assertEqual(detail["resolved_inputs"][0]["value"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(detail["resolved_config"]["youtube_research_worker"]["timeout"]["value"], 1200)
        self.assertEqual(detail["resolved_config"]["telegram"]["status"], "ready")
        self.assertEqual(detail["resolved_config"]["telegram"]["token"]["masked_value"], "tel***ret")
        self.assertIn(detail["resolved_config"]["youtube_research_worker"]["base_url"]["source"].split(":", 1)[0], {"bridge-env", "runtime-env-file", "node-run-overrides"})
        env_keys = {item["key"] for item in result["environment_snapshot"]}
        self.assertIn("YOUTUBE_RESEARCH_WORKFLOW_URL", env_keys)
        self.assertIn("YOUTUBE_WIKI_TG_TOKEN", env_keys)
        tg_capability = next(item for item in result["capability_results"] if item["key"] == "telegram")
        self.assertEqual(tg_capability["status"], "ready")
        git_capability = next(item for item in result["capability_results"] if item["key"] == "git")
        self.assertEqual(git_capability["status"], "warning")
        self.assertEqual(len(result["inner_steps"]), 8)
        self.assertEqual(result["inner_steps"][0]["id"], "prepare-request")

        read_back = bridge.read_node_run_result(sop, "youtube-deep-research", result["node_run_id"])
        self.assertEqual(read_back["node_run_id"], result["node_run_id"])
        listed = bridge.list_node_runs(sop, "youtube-deep-research")[0]
        self.assertEqual(listed["node_run_id"], result["node_run_id"])
        self.assertEqual(listed["retry_of"], "node-run-old")

    def test_node_run_preflight_loads_runtime_env_file_like_stage_wrapper(self):
        sop = dict(self.sop)
        sop["nodes"] = dict(self.sop["nodes"])
        sop["nodes"]["youtube-deep-research"] = {
            "title": "YouTube Deep Research",
            "skill": "sop-youtube-deep-research",
            "webhook_route": "sop-youtube-deep-research",
            "needs": ["youtube-fetch"],
            "inputs": {"source_url": "youtube-fetch.outputs.source_url"},
            "outputs": {"analysis_file": "raw/youtube-deep-research/{pipeline_id}/analysis.md"},
            "infra": {"tg_notify": True, "log_record": True},
        }
        env_file = self.wiki / ".agent-brain-plugins.env"
        env_file.write_text(
            "\n".join([
                "YOUTUBE_RESEARCH_WORKFLOW_URL=https://worker-from-file.example",
                "YOUTUBE_RESEARCH_WORKFLOW_TOKEN=worker-file-token-secret",
                "YOUTUBE_WIKI_TG_TOKEN=telegram-file-token-secret",
                "YOUTUBE_WIKI_TG_CHAT_ID=7796171193",
                "GITHUB_TOKEN=github-file-token-secret",
            ]) + "\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {
            "YOUTUBE_WIKI_ENV_FILE": str(env_file),
            "YOUTUBE_RESEARCH_WORKFLOW_URL": "",
            "YOUTUBE_RESEARCH_WORKFLOW_TOKEN": "",
            "YOUTUBE_CONTENT_API_TOKEN": "",
            "YOUTUBE_WIKI_TG_TOKEN": "",
            "YOUTUBE_WIKI_TG_CHAT_ID": "",
            "GITHUB_TOKEN": "",
            "GH_TOKEN": "",
        }, clear=False):
            code, result = bridge.create_node_run(
                sop,
                "test",
                "youtube-deep-research",
                {"mode": "probe", "input_source": "generated-fixture"},
            )

        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "done")
        detail = result["detail"]
        worker = detail["resolved_config"]["youtube_research_worker"]
        self.assertEqual(worker["status"], "ready")
        self.assertEqual(worker["base_url"]["source"], "runtime-env-file:YOUTUBE_RESEARCH_WORKFLOW_URL")
        self.assertEqual(worker["token"]["source"], "runtime-env-file:YOUTUBE_RESEARCH_WORKFLOW_TOKEN")
        self.assertEqual(worker["base_url"]["value"], "https://worker-from-file.example")
        self.assertIsNone(worker["token"]["value"])
        self.assertEqual(detail["resolved_config"]["telegram"]["token"]["source"], "runtime-env-file:YOUTUBE_WIKI_TG_TOKEN")
        self.assertEqual(detail["resolved_config"]["github"]["status"], "ready")
        self.assertEqual(detail["resolved_config"]["github"]["token"]["source"], "runtime-env-file:GITHUB_TOKEN")
        self.assertEqual(detail["config_sources"]["runtime_env_file"], str(env_file))
        self.assertIn("YOUTUBE_RESEARCH_WORKFLOW_URL", detail["config_sources"]["runtime_env_file_keys"])
        env_by_key = {item["key"]: item for item in result["environment_snapshot"]}
        self.assertEqual(env_by_key["YOUTUBE_RESEARCH_WORKFLOW_URL"]["source"], "runtime-env-file:YOUTUBE_RESEARCH_WORKFLOW_URL")
        self.assertEqual(env_by_key["GITHUB_TOKEN"]["capability"], "git")
        self.assertEqual(next(item for item in result["capability_results"] if item["key"] == "git")["status"], "ready")

    def test_node_run_prefers_instance_capability_settings_over_env_file(self):
        sop = dict(self.sop)
        sop["runtime_id"] = "runtime-test"
        sop["instance_id"] = "test-instance"
        sop["nodes"] = dict(self.sop["nodes"])
        sop["nodes"]["youtube-deep-research"] = {
            "title": "YouTube Deep Research",
            "skill": "sop-youtube-deep-research",
            "webhook_route": "sop-youtube-deep-research",
            "needs": ["youtube-fetch"],
            "inputs": {"source_url": "youtube-fetch.outputs.source_url"},
            "outputs": {"analysis_file": "raw/youtube-deep-research/{pipeline_id}/analysis.md"},
            "infra": {"tg_notify": True, "log_record": True},
        }
        env_file = self.wiki / ".agent-brain-plugins.env"
        env_file.write_text(
            "YOUTUBE_WIKI_TG_TOKEN=telegram-file-token-secret\n"
            "YOUTUBE_WIKI_TG_CHAT_ID=7796171193\n",
            encoding="utf-8",
        )
        saved = bridge.save_capability_config(sop, {
            "YOUTUBE_WIKI_TG_TOKEN": "telegram-instance-token-secret",
            "YOUTUBE_WIKI_TG_CHAT_ID": "1234567890",
        }, scope="instance", node_id="youtube-deep-research")
        self.assertEqual(saved["status"], "saved")
        with patch.dict(os.environ, {
            "YOUTUBE_WIKI_ENV_FILE": str(env_file),
            "YOUTUBE_WIKI_TG_TOKEN": "",
            "YOUTUBE_WIKI_TG_CHAT_ID": "",
        }, clear=False):
            code, result = bridge.create_node_run(
                sop,
                "test",
                "youtube-deep-research",
                {"mode": "probe", "input_source": "generated-fixture"},
            )
        self.assertEqual(code, 200)
        telegram = result["detail"]["resolved_config"]["telegram"]
        self.assertEqual(telegram["token"]["source"], "instance-settings:YOUTUBE_WIKI_TG_TOKEN")
        self.assertEqual(telegram["chat_id"]["source"], "instance-settings:YOUTUBE_WIKI_TG_CHAT_ID")
        preview = bridge.capability_config_resolution(sop, "youtube-deep-research")
        token_item = next(item for item in preview["items"] if item["key"] == "YOUTUBE_WIKI_TG_TOKEN")
        self.assertTrue(token_item["values_by_scope"]["instance"]["present"])

    def test_real_node_run_executes_youtube_deep_research_wrapper_and_records_outputs(self):
        sop = dict(self.sop)
        sop["nodes"] = dict(self.sop["nodes"])
        sop["nodes"]["youtube-deep-research"] = {
            "title": "YouTube Deep Research",
            "skill": "sop-youtube-deep-research",
            "webhook_route": "sop-youtube-deep-research",
            "needs": ["youtube-fetch"],
            "inputs": {"source_url": "youtube-fetch.outputs.source_url"},
            "outputs": {
                "analysis_file": "raw/youtube-deep-research/{pipeline_id}/analysis.md",
                "transcript_file": "raw/youtube-deep-research/{pipeline_id}/transcript.txt",
            },
            "infra": {"tg_notify": True, "log_record": True},
        }

        plugin_root = self.wiki / "_agent-brain-plugins"
        script_dir = plugin_root / "youtube-wiki" / "skills" / "sop-youtube-deep-research" / "scripts"
        script_dir.mkdir(parents=True)
        script = script_dir / "run_youtube_deep_research.sh"
        script.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
WIKI="$1"
RUN_ID="$2"
PIPELINE_ID="${3:-$2}"
OUT="$WIKI/raw/youtube-deep-research/$PIPELINE_ID"
RUN_DIR="$WIKI/raw/pipeline-runs/$PIPELINE_ID"
mkdir -p "$OUT" "$RUN_DIR/nodes/youtube-deep-research"
printf '# Analysis\\nreal node output\\n' > "$OUT/analysis.md"
printf 'transcript output\\n' > "$OUT/transcript.txt"
python3 - "$WIKI" "$PIPELINE_ID" "$RUN_ID" <<'PY'
import json
import sys
from pathlib import Path
wiki = Path(sys.argv[1])
pipeline_id = sys.argv[2]
run_id = sys.argv[3]
ctx_file = wiki / "raw" / "pipeline-context.json"
ctx = json.loads(ctx_file.read_text(encoding="utf-8"))
ctx.setdefault("stage_b2", {})["analysis_file"] = f"raw/youtube-deep-research/{pipeline_id}/analysis.md"
run_dir = wiki / "raw" / "pipeline-runs" / pipeline_id
(run_dir / "context.json").write_text(json.dumps(ctx), encoding="utf-8")
(run_dir / "run.json").write_text(json.dumps({
    "pipeline_id": pipeline_id,
    "status": "done",
    "nodes": {"youtube-deep-research": "done"},
    "source_url": ctx.get("source_url", ""),
}), encoding="utf-8")
(run_dir / "nodes" / "youtube-deep-research.json").write_text(json.dumps({
    "pipeline_id": pipeline_id,
    "node_id": "youtube-deep-research",
    "run_id": run_id,
    "status": "done",
    "actual_outputs": {
        "analysis_file": [f"raw/youtube-deep-research/{pipeline_id}/analysis.md"],
        "transcript_file": [f"raw/youtube-deep-research/{pipeline_id}/transcript.txt"],
    },
    "validation": {"status": "passed", "missing_outputs": [], "unexpected_outputs": []},
}), encoding="utf-8")
(run_dir / "nodes" / "youtube-deep-research" / "capabilities.json").write_text(json.dumps({
    "git": {
        "enabled": True,
        "required": False,
        "status": "done",
        "managed_by": "runtime-harness",
        "changed_files": [f"raw/youtube-deep-research/{pipeline_id}/analysis.md"],
    },
    "telegram": {
        "enabled": True,
        "required": False,
        "status": "failed",
        "error": "Forbidden: bot can't initiate conversation with a user",
    },
}), encoding="utf-8")
PY
""",
            encoding="utf-8",
        )
        script.chmod(0o755)

        with patch.object(bridge, "plugin_root", return_value=plugin_root), patch.dict(os.environ, {
            "YOUTUBE_RESEARCH_WORKFLOW_URL": "https://worker.example",
            "YOUTUBE_RESEARCH_WORKFLOW_TOKEN": "worker-token-secret",
            "YOUTUBE_WIKI_TG_TOKEN": "telegram-token-secret",
            "YOUTUBE_WIKI_TG_CHAT_ID": "7796171193",
        }, clear=False):
            code, result = bridge.create_node_run(
                sop,
                "test",
                "youtube-deep-research",
                {"mode": "real-node", "input_source": "generated-fixture", "sync": True},
            )

        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["validation"]["status"], "passed")
        self.assertEqual(result["actual_outputs"]["analysis_file"], [
            f"raw/youtube-deep-research/{result['node_run_id']}/analysis.md",
        ])
        self.assertEqual(result["actual_outputs"]["transcript_file"], [
            f"raw/youtube-deep-research/{result['node_run_id']}/transcript.txt",
        ])
        self.assertEqual(
            next(step for step in result["steps"] if step["id"] == "execute-or-dry-run")["status"],
            "done",
        )
        self.assertEqual(
            next(step for step in result["steps"] if step["id"] == "validate-outputs")["status"],
            "done",
        )
        self.assertTrue((self.wiki / "raw" / "node-runs" / result["node_run_id"] / "executor.log").exists())
        self.assertTrue(any(item["type"] == "research.analysis" for item in result["business_artifacts"]))
        capability_by_key = {item["key"]: item for item in result["capability_results"]}
        self.assertEqual(capability_by_key["git"]["status"], "done")
        self.assertEqual(capability_by_key["git"]["managed_by"], "runtime-harness")
        self.assertEqual(capability_by_key["telegram"]["status"], "failed")
        self.assertIn("bot can't initiate conversation", capability_by_key["telegram"]["reason"])
        self.assertTrue(any("bot can't initiate conversation" in issue["message"] for issue in result["issues"]))

    def test_node_run_routes_create_and_read_shareable_id(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/workflows/test/nodes/wiki-build/runs",
                method="POST",
                data=json.dumps({"mode": "dry-run", "input_source": "existing-run", "pipeline_id": "pipe-1"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                created = json.loads(response.read())
            self.assertIn("node_run_id", created)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/workflows/test/nodes/wiki-build/runs/{created['node_run_id']}",
                timeout=3,
            ) as response:
                detail = json.loads(response.read())
            self.assertEqual(detail["node_run_id"], created["node_run_id"])
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/workflows/test/nodes/wiki-build/runs/{created['node_run_id']}/events",
                timeout=3,
            ) as response:
                events = json.loads(response.read())
            self.assertTrue(events["events"])
        server.shutdown()
        server.server_close()

    @unittest.skipUnless(bridge.provision_module() is not None, "engine module not importable")
    def test_node_contract_endpoint_returns_engine_contract(self):
        # P3: run-less catalog contract endpoint serves the engine classification.
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/nodes/ssh-preflight/contract",
                timeout=3,
            ) as resp:
                payload = json.loads(resp.read().decode())
        server.shutdown()
        server.server_close()
        self.assertEqual(payload["node_id"], "ssh-preflight")
        self.assertEqual(payload["contract"]["dep_class"], "independent")
        self.assertTrue(payload["contract"]["testable_standalone"])

    @unittest.skipUnless(bridge.provision_module() is not None, "engine module not importable")
    def test_trigger_mutating_node_requires_confirm(self):
        # P3 guard: a mutating node refuses to test without confirm_mutating.
        code, result = bridge.trigger_node_test(
            {"id": "runtime-management", "wiki_local_path": str(self.wiki)},
            "clone-runtime-repos", {})
        self.assertEqual(code, 409)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["side_effect"], "mutating")

    @unittest.skipUnless(bridge.provision_module() is not None, "engine module not importable")
    def test_trigger_artifact_node_requires_seed(self):
        # P3 guard: an artifact_dependent node refuses to test without a seed run.
        code, result = bridge.trigger_node_test(
            {"id": "runtime-management", "wiki_local_path": str(self.wiki)},
            "verify-runtime-removed", {})
        self.assertEqual(code, 409)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["artifact_deps"])

    @unittest.skipUnless(bridge.provision_module() is not None, "engine module not importable")
    def test_trigger_independent_node_spawns_isolated_test(self):
        # P3 happy path: an independent read-only node triggers a nodetest run.
        with patch.object(bridge, "subprocess") as sp, \
                patch.object(bridge, "inject_runtime_management_config", side_effect=lambda b: b):
            code, result = bridge.trigger_node_test(
                {"id": "runtime-management", "wiki_local_path": str(self.wiki)},
                "ssh-preflight",
                {"request_overrides": {"action": "create-runtime", "target_host": "203.0.113.9"}})
        self.assertEqual(code, 202)
        self.assertEqual(result["mode"], "node-test")
        self.assertTrue(result["pipeline_id"].startswith("nodetest-ssh-preflight-"))
        self.assertTrue(sp.Popen.called)
        # the engine was invoked with --test and the single node
        argv = sp.Popen.call_args[0][0]
        self.assertIn("--test", argv)
        self.assertIn("ssh-preflight", argv)

    def test_sync_runtime_management_definition_refreshes_stale_snapshot(self):
        # Anti-historical-drift: a stale deployed sop.yaml must be re-synced from
        # the authoritative template, so no frozen old workflow version lingers.
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "plugins"
            tpl_dir = plugin / "youtube-wiki" / "templates" / "runtime-management-sop"
            tpl_dir.mkdir(parents=True)
            (tpl_dir / "sop.yaml").write_text("name: runtime-management\nversion: '0.2'\nnodes:\n  a: {}\n  b: {}\n", encoding="utf-8")
            workspace = Path(tmp) / "wiki" / "runtime-management"
            workspace.mkdir(parents=True)
            # deployed = stale single-node snapshot
            (workspace / "sop.yaml").write_text("name: runtime-management\nversion: '0.1'\nnodes:\n  a: {}\n", encoding="utf-8")

            with patch.object(bridge, "plugin_root", return_value=plugin):
                bridge.sync_runtime_management_definition(str(workspace))

            synced = (workspace / "sop.yaml").read_text(encoding="utf-8")
            self.assertIn("b: {}", synced)         # now matches template (2 nodes)
            self.assertIn("version: '0.2'", synced)

    def test_read_node_test_result_pending_then_terminal_and_guards_namespace(self):
        sop = {"id": "runtime-management", "wiki_local_path": str(self.wiki)}
        pid = "nodetest-ssh-preflight-20260614T000000"
        # before the report exists -> pending
        pending = bridge.read_node_test_result(sop, "ssh-preflight", pid)
        self.assertEqual(pending["status"], "running")
        self.assertTrue(pending["pending"])
        # write a terminal report into the nodetest namespace
        rpt = self.wiki / "raw" / "provision" / "nodetest" / pid / "ssh-preflight.json"
        rpt.parent.mkdir(parents=True, exist_ok=True)
        rpt.write_text(json.dumps({"node_id": "ssh-preflight", "status": "done",
                                   "detail": {"ssh_ok": True, "disk_ok": True, "stdout": "host\nuser"}}), encoding="utf-8")
        done = bridge.read_node_test_result(sop, "ssh-preflight", pid)
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["detail"]["ssh_ok"])
        # non-nodetest pipeline ids are rejected (no reading real runs / traversal)
        self.assertIsNone(bridge.read_node_test_result(sop, "ssh-preflight", "create-runtime-20260612T222204"))
        self.assertIsNone(bridge.read_node_test_result(sop, "ssh-preflight", "../../etc"))

    @unittest.skipUnless(bridge.provision_module() is not None, "engine module not importable")
    def test_dry_run_mutating_bypasses_confirm_and_from_run_id_merges_base(self):
        run_id = "create-runtime-FROMRUN"
        reqf = self.wiki / ".sop" / "secrets" / run_id / "request.json"
        reqf.parent.mkdir(parents=True, exist_ok=True)
        reqf.write_text(json.dumps({
            "action": "create-runtime", "ssh_command": "ssh runtime@203.0.113.9",
            "private_key_b64": "BASE64KEY", "runtime_id": "r1",
        }), encoding="utf-8")
        sop = {"id": "runtime-management", "wiki_local_path": str(self.wiki)}
        with patch.object(bridge, "subprocess") as sp, \
                patch.object(bridge, "inject_runtime_management_config", side_effect=lambda b: b):
            # mutating node + dry_run + NO confirm -> allowed (dry-run exempt from confirm)
            code, result = bridge.trigger_node_test(sop, "configure-hermes-model", {
                "dry_run": True, "from_run_id": run_id,
                "request_overrides": {"hermes_openai_api_key": "newkey"},
            })
        self.assertEqual(code, 202)
        argv = sp.Popen.call_args[0][0]
        self.assertIn("--dry-run", argv)
        self.assertIn("configure-hermes-model", argv)
        written = json.loads(next((self.wiki / ".sop" / "secrets").glob(
            "nodetest-configure-hermes-model-*/request.json")).read_text())
        self.assertEqual(written["ssh_command"], "ssh runtime@203.0.113.9")   # from base run
        self.assertEqual(written["private_key_b64"], "BASE64KEY")             # from base run
        self.assertEqual(written["hermes_openai_api_key"], "newkey")          # override wins

    def test_mask_value_handles_list_under_secret_named_key(self):
        # A secret-named field (contains "key") can hold a list, e.g. updated_keys.
        # mask_value must recurse, not crash on the unhashable membership test.
        self.assertEqual(bridge.mask_value(["HERMES_MODEL", "HERMES_OPENAI_API_KEY"]),
                         ["HERMES_MODEL", "HERMES_OPENAI_API_KEY"])
        masked = bridge.mask_data({"updated_keys": ["HERMES_MODEL"], "api_key": "supersecretvalue"})
        self.assertEqual(masked["updated_keys"], ["HERMES_MODEL"])
        self.assertNotEqual(masked["api_key"], "supersecretvalue")

    def test_sop_node_cli_is_http_client_and_requires_confirm_for_destructive_actions(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "sop-node.sh"
        dry_run = subprocess.run(
            [
                "bash", str(script),
                "--endpoint=https://runtime.example",
                "--instance=test",
                "--node=wiki-build",
                "--pipeline-id=pipe-1",
                "--action=retry",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(dry_run.stdout)
        self.assertEqual(payload["method"], "POST")
        self.assertIn("/actions/retry", payload["url"])
        blocked = subprocess.run(
            [
                "bash", str(script),
                "--endpoint=https://runtime.example",
                "--instance=test",
                "--node=wiki-build",
                "--pipeline-id=pipe-1",
                "--action=retry",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(blocked.returncode, 3)
        self.assertIn("without --confirm", blocked.stderr)

    def test_sse_http_response_closes_and_honors_last_event_id(self):
        events_file = self.wiki / "raw/pipeline-runs/pipe-1/events.jsonl"
        events_file.write_text(
            '{"sequence":1,"event":"node.started"}\n{"sequence":2,"event":"node.completed"}\n',
            encoding="utf-8",
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "find_sop", return_value=self.sop):
            thread.start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/runs/pipe-1/events/stream",
                headers={"Last-Event-ID": "1"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read().decode()
                self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
                self.assertIn("retry: 1000", body)
                self.assertNotIn("id: 1\n", body)
                self.assertIn("id: 2\n", body)
        server.shutdown()
        server.server_close()

    def test_running_sse_heartbeats_and_does_not_block_spi(self):
        run_file = self.wiki / "raw/pipeline-runs/pipe-1/run.json"
        run_file.write_text(json.dumps({"pipeline_id": "pipe-1", "status": "running"}), encoding="utf-8")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        url = f"http://127.0.0.1:{server.server_port}"
        with (
            patch.object(bridge, "find_sop", return_value=self.sop),
            patch.object(bridge, "sop_manifest", return_value={"runtime": "test"}),
            patch.object(bridge, "SSE_STREAM_WINDOW_SECONDS", 0.2),
            patch.object(bridge, "SSE_HEARTBEAT_SECONDS", 0.05),
        ):
            thread.start()
            stream_result = {}

            def read_stream():
                with urllib.request.urlopen(
                    f"{url}/api/sop/test/runs/pipe-1/events/stream", timeout=3
                ) as response:
                    stream_result["body"] = response.read().decode()

            stream_thread = threading.Thread(target=read_stream)
            stream_thread.start()
            time.sleep(0.05)
            started = time.monotonic()
            with urllib.request.urlopen(f"{url}/api/sop", timeout=1) as response:
                self.assertEqual(json.loads(response.read()), {"runtime": "test"})
            self.assertLess(time.monotonic() - started, 0.2)
            stream_thread.join(timeout=2)
            self.assertFalse(stream_thread.is_alive())
            self.assertIn(": heartbeat", stream_result["body"])
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    unittest.main()
