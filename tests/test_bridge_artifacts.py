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

    def test_run_routes_prefer_runtime_index(self):
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
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/sop/test/node-drafts",
                method="POST",
                data=json.dumps({
                    "skill_install_command": "bash <(curl -fsSL https://skill.vyibc.com/install-demo.sh)",
                    "skill_id": "demo-skill",
                    "node_id": "youtube-cover-image",
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
            self.assertFalse(draft["validation"]["production_dag_changed"])
            self.assertTrue((Path(draft["draft_path"]) / "node.yaml").exists())
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
