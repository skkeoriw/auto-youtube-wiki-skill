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
        with patch.dict(os.environ, {
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
                "SOP_UI_URL": "https://sop-ui.example",
            })
            preview = bridge.runtime_config_inheritance_preview(sop)
            merged = bridge.inject_runtime_management_config({"action": "create-runtime"})

        by_key = {item["key"]: item for item in preview["items"]}
        self.assertEqual(sorted(changed.keys()), ["CLOUDFLARE_API_KEY", "SOP_UI_URL"])
        self.assertEqual(by_key["CLOUDFLARE_API_KEY"]["source"], "management_config")
        self.assertEqual(by_key["CLOUDFLARE_API_KEY"]["masked_value"], "clo***lue")
        self.assertEqual(by_key["SOP_UI_URL"]["masked_value"], "https://sop-ui.example")
        self.assertEqual(merged["CLOUDFLARE_API_KEY"], "cloudflare-secret-value")
        self.assertEqual(merged["SOP_UI_URL"], "https://sop-ui.example")
        self.assertIn("CLOUDFLARE_API_KEY", merged["_management_config_injected"])

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

    def test_trigger_action_is_disabled(self):
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
            self.assertEqual(ctx.exception.code, 409)
        server.shutdown()
        server.server_close()

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
