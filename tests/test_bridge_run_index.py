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


if __name__ == "__main__":
    unittest.main()
