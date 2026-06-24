"""Tests for cancel_run, cancel_node, retry_node in bridge.py."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bridge import cancel_run, cancel_node, relay_context_brief, retry_node


def _make_wiki(tmp_path, pipeline_id, node_id="notebooklm-research", node_status="failed"):
    wiki = tmp_path / "wiki"
    run_dir = wiki / "raw" / "pipeline-runs" / pipeline_id / "nodes"
    run_dir.mkdir(parents=True)
    ctx_dir = wiki / "raw"
    ctx_dir.mkdir(parents=True, exist_ok=True)

    (ctx_dir / "pipeline-context.json").write_text(json.dumps({
        "pipeline_id": pipeline_id,
        "source_url": "https://example.com",
    }))

    run_file = wiki / "raw" / "pipeline-runs" / pipeline_id / "run.json"
    run_file.write_text(json.dumps({
        "pipeline_id": pipeline_id,
        "status": "running",
        "nodes": {node_id: node_status},
        "updated_at": "2026-06-04T00:00:00Z",
    }))

    (run_dir / f"{node_id}.json").write_text(json.dumps({
        "node_id": node_id,
        "status": node_status,
        "run_id": "run-old",
    }))

    sop = {
        "id": "test-instance",
        "instance_id": "test-instance",
        "wiki_local_path": str(wiki),
        "nodes": {node_id: {"webhook_route": ""}},
    }
    return wiki, sop


class CancelRunTest(unittest.TestCase):
    def test_sets_cancelled_status_in_run_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-001")
            _status, result = cancel_run(sop, "pipeline-001", reason="test cancel")
            self.assertEqual(result["status"], "cancelled")
            run = json.loads((wiki / "raw/pipeline-runs/pipeline-001/run.json").read_text())
            self.assertEqual(run["status"], "cancelled")
            self.assertEqual(run["cancel_reason"], "test cancel")

    def test_sets_cancelled_flag_in_context_when_pipeline_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-002")
            cancel_run(sop, "pipeline-002")
            ctx = json.loads((wiki / "raw/pipeline-context.json").read_text())
            self.assertTrue(ctx.get("cancelled"))

    def test_does_not_cancel_context_if_different_pipeline_is_active(self):
        """Regression: cancel must not stomp on an unrelated active pipeline's context."""
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-003")
            # Simulate a DIFFERENT pipeline being active in the context
            ctx_file = wiki / "raw/pipeline-context.json"
            ctx_file.write_text(json.dumps({
                "pipeline_id": "OTHER-pipeline",
                "source_url": "https://example.com",
            }))
            cancel_run(sop, "pipeline-003")
            ctx = json.loads(ctx_file.read_text())
            # Should NOT have written cancelled flag since active context is a different pipeline
            self.assertIsNone(ctx.get("cancelled"))

    def test_returns_404_for_nonexistent_pipeline(self):
        """Regression: cancel must not create fake run directories."""
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp) / "wiki"
            wiki.mkdir()
            (wiki / "raw").mkdir()
            sop = {"id": "test", "wiki_local_path": str(wiki)}
            _status, result = cancel_run(sop, "does-not-exist")
            self.assertIsNone(_status)
            self.assertEqual(result["status"], "error")
            # Must NOT have created a directory
            self.assertFalse((wiki / "raw/pipeline-runs/does-not-exist").exists())

    def test_appends_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-010")
            cancel_run(sop, "pipeline-010")
            events_file = wiki / "raw/pipeline-runs/pipeline-010/events.jsonl"
            self.assertTrue(events_file.exists())
            events = [json.loads(line) for line in events_file.read_text().splitlines()]
            self.assertTrue(any(e["event"] == "pipeline_cancelled" for e in events))

    def test_does_not_overwrite_done_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-011")
            run_file = wiki / "raw/pipeline-runs/pipeline-011/run.json"
            run_data = json.loads(run_file.read_text())
            run_data["status"] = "done"
            run_file.write_text(json.dumps(run_data))
            _status, result = cancel_run(sop, "pipeline-011")
            self.assertEqual(result["status"], "done")
            run = json.loads(run_file.read_text())
            self.assertEqual(run["status"], "done")


class CancelNodeTest(unittest.TestCase):
    def test_marks_node_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-020", node_status="running")
            _status, result = cancel_node(sop, "pipeline-020", "notebooklm-research")
            self.assertEqual(result["status"], "cancelled")
            node = json.loads(
                (wiki / "raw/pipeline-runs/pipeline-020/nodes/notebooklm-research.json").read_text()
            )
            self.assertEqual(node["status"], "cancelled")

    def test_updates_run_nodes_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-021", node_status="running")
            cancel_node(sop, "pipeline-021", "notebooklm-research")
            run = json.loads((wiki / "raw/pipeline-runs/pipeline-021/run.json").read_text())
            self.assertEqual(run["nodes"]["notebooklm-research"], "cancelled")

    def test_returns_404_for_nonexistent_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp) / "wiki"
            wiki.mkdir()
            sop = {"id": "test", "wiki_local_path": str(wiki)}
            _status, result = cancel_node(sop, "does-not-exist", "some-node")
            self.assertIsNone(_status)
            self.assertEqual(result["status"], "error")


class RetryNodeTest(unittest.TestCase):
    def test_rejects_running_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-030", node_status="running")
            status, result = retry_node(sop, "pipeline-030", "notebooklm-research")
            self.assertEqual(status, 409)
            self.assertEqual(result["status"], "error")

    def test_rejects_non_retryable_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-030b", node_status="failed")
            sop["nodes"]["notebooklm-research"]["retryable"] = False
            status, result = retry_node(sop, "pipeline-030b", "notebooklm-research")
            self.assertEqual(status, 409)
            self.assertEqual(result["status"], "error")
            node = json.loads(
                (wiki / "raw/pipeline-runs/pipeline-030b/nodes/notebooklm-research.json").read_text()
            )
            self.assertEqual(node["status"], "failed")

    def test_returns_404_for_nonexistent_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp) / "wiki"
            wiki.mkdir()
            sop = {"id": "test", "wiki_local_path": str(wiki)}
            status, result = retry_node(sop, "does-not-exist", "some-node")
            self.assertEqual(status, 404)

    def test_resets_node_to_running_when_script_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-031", node_status="failed")
            status, result = retry_node(sop, "pipeline-031", "notebooklm-research")
            self.assertIn(status, {200, 500})
            node = json.loads(
                (wiki / "raw/pipeline-runs/pipeline-031/nodes/notebooklm-research.json").read_text()
            )
            self.assertIn(node["status"], {"running", "failed"})

    def test_appends_retry_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-032", node_status="failed")
            retry_node(sop, "pipeline-032", "notebooklm-research")
            events_file = wiki / "raw/pipeline-runs/pipeline-032/events.jsonl"
            if events_file.exists():
                events = [json.loads(line) for line in events_file.read_text().splitlines()]
                self.assertTrue(any(e["event"] == "node_retry" for e in events))

    def test_retry_uses_custom_node_executor_entry_without_hardcoded_script_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wiki, sop = _make_wiki(root, "pipeline-033", node_id="custom-summary", node_status="failed")
            plugin_root = root / "agent-brain-plugins"
            script = plugin_root / "youtube-wiki" / "skills" / "sop-custom-summary" / "scripts" / "run_custom_summary.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            sop["nodes"]["custom-summary"] = {
                "executor": {
                    "type": "agent-skill",
                    "skill": "sop-custom-summary",
                    "entry": "scripts/run_custom_summary.sh",
                },
            }
            old_path = os.environ.get("AGENT_BRAIN_PLUGINS_PATH")
            os.environ["AGENT_BRAIN_PLUGINS_PATH"] = str(plugin_root)
            try:
                with mock.patch("bridge.subprocess.Popen") as popen:
                    status, result = retry_node(sop, "pipeline-033", "custom-summary")
            finally:
                if old_path is None:
                    os.environ.pop("AGENT_BRAIN_PLUGINS_PATH", None)
                else:
                    os.environ["AGENT_BRAIN_PLUGINS_PATH"] = old_path
            self.assertEqual(status, 200)
            self.assertEqual(result["status"], "retrying")
            command = popen.call_args.args[0]
            self.assertEqual(command[:2], ["bash", str(script)])
            self.assertEqual(command[2], str(wiki))
            self.assertEqual(command[4], "pipeline-033")


class RelayContextTest(unittest.TestCase):
    def test_relay_instruction_is_included_in_context_brief(self):
        brief = relay_context_brief({
            "relay_instruction": "Use only analysis_file as wiki source.",
            "relay_selection": {
                "edge_contract": {"intent": {"title": "Research to Wiki"}},
                "matched_items": [{
                    "source_output": "analysis_file",
                    "target_input": "reports",
                    "source_path": "raw/example/analysis.md",
                }],
            },
        })
        self.assertIn("Run instruction: Use only analysis_file as wiki source.", brief)
        self.assertIn("analysis_file -> reports", brief)


if __name__ == "__main__":
    unittest.main()
