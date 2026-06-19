#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "bridge", Path(__file__).resolve().parents[1] / "scripts" / "bridge.py"
)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class FakeRunIndexStore:
    def __init__(self, indexed_before, indexed_after=None):
        self.indexed_before = indexed_before
        self.indexed_after = indexed_after or indexed_before
        self.rebuild_called = False

    def get_run(self, _pipeline_id):
        return self.indexed_after if self.rebuild_called else self.indexed_before

    def rebuild_from_workspace(self, _pipeline_id, _sop):
        self.rebuild_called = True
        return True


class FakeSummaryStore:
    def count_runs(self, status="", q="", action="", source_type="", failed_node="", date_from="", date_to=""):
        return 2

    def list_run_summaries(
        self,
        limit=80,
        offset=0,
        status="",
        q="",
        action="",
        source_type="",
        failed_node="",
        date_from="",
        date_to="",
        sort="updated_at",
        order="desc",
    ):
        self.args = {
            "limit": limit,
            "offset": offset,
            "status": status,
            "q": q,
            "sort": sort,
            "order": order,
        }
        return [{
            "pipeline_id": "pipe-2",
            "execution_id": "pipe-2",
            "status": "done",
            "nodes": {"build": "done"},
            "updated_at": "2026-06-15T00:00:00Z",
        }]


class BridgeRunIndexTest(unittest.TestCase):
    def write_workspace_run(self, wiki, run):
        run_dir = wiki / "raw/pipeline-runs/pipe-1"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")

    def test_indexed_run_rebuilds_when_workspace_is_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp)
            sop = {"wiki_local_path": str(wiki)}
            self.write_workspace_run(wiki, {
                "pipeline_id": "pipe-1",
                "status": "done",
                "updated_at": "2026-06-07T00:06:08Z",
                "nodes": {"tg-notify": "done"},
            })
            store = FakeRunIndexStore(
                {
                    "pipeline_id": "pipe-1",
                    "status": "running",
                    "updated_at": "2026-06-07T00:06:05Z",
                    "nodes": {"tg-notify": "running"},
                },
                {
                    "pipeline_id": "pipe-1",
                    "status": "done",
                    "updated_at": "2026-06-07T00:06:08Z",
                    "nodes": {"tg-notify": "done"},
                },
            )

            with patch.object(bridge, "run_index_store", return_value=store):
                run = bridge.indexed_run(sop, "pipe-1", rebuild=True)

            self.assertTrue(store.rebuild_called)
            self.assertEqual(run["status"], "done")
            self.assertEqual(run["nodes"]["tg-notify"], "done")

    def test_indexed_run_keeps_fresh_running_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp)
            sop = {"wiki_local_path": str(wiki)}
            self.write_workspace_run(wiki, {
                "pipeline_id": "pipe-1",
                "status": "running",
                "updated_at": "2026-06-07T00:06:05Z",
                "nodes": {"tg-notify": "running"},
            })
            store = FakeRunIndexStore({
                "pipeline_id": "pipe-1",
                "status": "running",
                "updated_at": "2026-06-07T00:06:05Z",
                "nodes": {"tg-notify": "running"},
            })

            with patch.object(bridge, "run_index_store", return_value=store):
                run = bridge.indexed_run(sop, "pipe-1", rebuild=True)

            self.assertFalse(store.rebuild_called)
            self.assertEqual(run["status"], "running")

    def test_stale_detection_handles_terminal_node_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp)
            sop = {"wiki_local_path": str(wiki)}
            self.write_workspace_run(wiki, {
                "pipeline_id": "pipe-1",
                "status": "running",
                "updated_at": "2026-06-07T00:06:08Z",
                "nodes": {"tg-notify": "done"},
            })

            self.assertTrue(bridge.indexed_run_is_stale(sop, "pipe-1", {
                "pipeline_id": "pipe-1",
                "status": "running",
                "updated_at": "2026-06-07T00:06:05Z",
                "nodes": {"tg-notify": "running"},
            }))

    def test_sop_runs_uses_summary_store_with_pagination(self):
        sop = {
            "id": "runtime-management",
            "instance_id": "runtime-management",
            "wiki_local_path": "/tmp/does-not-need-files",
            "nodes": {"build": {"mode": "blocking"}},
        }
        store = FakeSummaryStore()

        with patch.object(bridge, "run_index_store", return_value=store):
            payload = bridge.sop_runs(sop, {
                "page": ["2"],
                "page_size": ["1"],
                "status": ["done"],
                "q": ["pipe"],
            })

        self.assertEqual(store.args["limit"], 1)
        self.assertEqual(store.args["offset"], 1)
        self.assertEqual(store.args["status"], "done")
        self.assertEqual(payload["page"]["total"], 2)
        self.assertEqual(payload["executions"][0]["pipeline_id"], "pipe-2")
        self.assertNotIn("node_states", payload["executions"][0])

    def test_execution_summary_derives_failed_from_blocking_node(self):
        sop = {
            "id": "test",
            "wiki_local_path": "/tmp/does-not-need-files",
            "nodes": {
                "fetch": {"mode": "blocking"},
                "sidecar": {"mode": "sidecar"},
                "build": {"mode": "blocking"},
            },
        }
        summary = bridge.execution_summary(sop, {
            "pipeline_id": "pipe-1",
            "status": "running",
            "nodes": {"fetch": "done", "sidecar": "failed", "build": "failed"},
        })

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failed_node"], "build")
        self.assertEqual(summary["status_evidence"]["blocking_failed_nodes"], ["build"])

    def test_execution_summary_allows_sidecar_failed_when_blocking_done(self):
        sop = {
            "id": "test",
            "wiki_local_path": "/tmp/does-not-need-files",
            "nodes": {
                "fetch": {"mode": "blocking"},
                "sidecar": {"mode": "sidecar"},
                "build": {"mode": "blocking"},
            },
        }
        summary = bridge.execution_summary(sop, {
            "pipeline_id": "pipe-1",
            "status": "running",
            "nodes": {"fetch": "done", "sidecar": "failed", "build": "done"},
        })

        self.assertEqual(summary["status"], "done")
        self.assertEqual(summary["sidecar_failed_nodes"], ["sidecar"])

    def test_execution_summary_ignores_manual_node_status(self):
        sop = {
            "id": "test",
            "wiki_local_path": "/tmp/does-not-need-files",
            "nodes": {
                "fetch": {"mode": "blocking"},
                "retry": {"mode": "manual"},
                "manual-fix": {"mode": "manual"},
            },
        }
        summary = bridge.execution_summary(sop, {
            "pipeline_id": "pipe-1",
            "status": "running",
            "nodes": {"fetch": "done", "retry": "failed", "manual-fix": "running"},
        })

        self.assertEqual(summary["status"], "done")
        self.assertEqual(summary["status_evidence"]["blocking_failed_nodes"], [])
        self.assertEqual(summary["status_evidence"]["running_nodes"], [])


if __name__ == "__main__":
    unittest.main()
