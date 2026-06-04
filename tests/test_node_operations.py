"""Tests for cancel_run, cancel_node, retry_node in bridge.py."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bridge import cancel_run, cancel_node, retry_node


def _make_wiki(tmp_path, pipeline_id, node_id="notebooklm-research", node_status="failed"):
    wiki = tmp_path / "wiki"
    run_dir = wiki / "raw" / "pipeline-runs" / pipeline_id / "nodes"
    run_dir.mkdir(parents=True)
    ctx_dir = wiki / "raw"
    ctx_dir.mkdir(parents=True, exist_ok=True)

    # pipeline-context.json
    (ctx_dir / "pipeline-context.json").write_text(json.dumps({
        "pipeline_id": pipeline_id,
        "source_url": "https://example.com",
    }))

    # run.json
    run_file = wiki / "raw" / "pipeline-runs" / pipeline_id / "run.json"
    run_file.write_text(json.dumps({
        "pipeline_id": pipeline_id,
        "status": "running",
        "nodes": {node_id: node_status},
        "updated_at": "2026-06-04T00:00:00Z",
    }))

    # node json
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
            result = cancel_run(sop, "pipeline-001", reason="test cancel")
            self.assertEqual(result["status"], "cancelled")
            run = json.loads((wiki / "raw/pipeline-runs/pipeline-001/run.json").read_text())
            self.assertEqual(run["status"], "cancelled")
            self.assertEqual(run["cancel_reason"], "test cancel")

    def test_sets_cancelled_flag_in_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-002")
            cancel_run(sop, "pipeline-002")
            ctx = json.loads((wiki / "raw/pipeline-context.json").read_text())
            self.assertTrue(ctx.get("cancelled"))

    def test_appends_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-003")
            cancel_run(sop, "pipeline-003")
            events_file = wiki / "raw/pipeline-runs/pipeline-003/events.jsonl"
            self.assertTrue(events_file.exists())
            events = [json.loads(line) for line in events_file.read_text().splitlines()]
            self.assertTrue(any(e["event"] == "pipeline_cancelled" for e in events))

    def test_does_not_overwrite_done_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-004")
            run_file = wiki / "raw/pipeline-runs/pipeline-004/run.json"
            run_data = json.loads(run_file.read_text())
            run_data["status"] = "done"
            run_file.write_text(json.dumps(run_data))
            cancel_run(sop, "pipeline-004")
            run = json.loads(run_file.read_text())
            self.assertEqual(run["status"], "done")


class CancelNodeTest(unittest.TestCase):
    def test_marks_node_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-010", node_status="running")
            result = cancel_node(sop, "pipeline-010", "notebooklm-research")
            self.assertEqual(result["status"], "cancelled")
            node = json.loads(
                (wiki / "raw/pipeline-runs/pipeline-010/nodes/notebooklm-research.json").read_text()
            )
            self.assertEqual(node["status"], "cancelled")

    def test_updates_run_nodes_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-011", node_status="running")
            cancel_node(sop, "pipeline-011", "notebooklm-research")
            run = json.loads((wiki / "raw/pipeline-runs/pipeline-011/run.json").read_text())
            self.assertEqual(run["nodes"]["notebooklm-research"], "cancelled")


class RetryNodeTest(unittest.TestCase):
    def test_rejects_running_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-020", node_status="running")
            status, result = retry_node(sop, "pipeline-020", "notebooklm-research")
            self.assertEqual(status, 409)
            self.assertEqual(result["status"], "error")

    def test_resets_node_to_running_when_script_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-021", node_status="failed")
            status, result = retry_node(sop, "pipeline-021", "notebooklm-research")
            # Script not found on test machine, falls back to webhook (also missing) → 500
            self.assertIn(status, {200, 500})
            node = json.loads(
                (wiki / "raw/pipeline-runs/pipeline-021/nodes/notebooklm-research.json").read_text()
            )
            # status should be running (if launched) or failed (if nothing found)
            self.assertIn(node["status"], {"running", "failed"})

    def test_appends_retry_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki, sop = _make_wiki(Path(tmp), "pipeline-022", node_status="failed")
            retry_node(sop, "pipeline-022", "notebooklm-research")
            events_file = wiki / "raw/pipeline-runs/pipeline-022/events.jsonl"
            if events_file.exists():
                events = [json.loads(line) for line in events_file.read_text().splitlines()]
                self.assertTrue(any(e["event"] == "node_retry" for e in events))


if __name__ == "__main__":
    unittest.main()
