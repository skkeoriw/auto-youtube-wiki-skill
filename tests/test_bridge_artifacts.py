#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


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
        self.sop = {
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
                    "inputs": {"reports": "notebooklm-research.outputs.reports"},
                    "outputs": {"index": "index.md", "pages": "wiki/**"},
                },
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_resolves_context_and_upstream_artifacts(self):
        detail = bridge.node_runtime_detail(self.sop, "pipe-1", "wiki-build")
        self.assertEqual(detail["resolved_inputs"]["reports"], ["raw/notebooklm-analysis/report.md"])
        self.assertEqual(detail["actual_outputs"]["pages"], ["wiki/entities/Agent.md"])
        self.assertEqual(detail["validation"]["status"], "passed")
        self.assertEqual(detail["artifacts"][0]["producer"], "wiki-build")
        self.assertIn("preview", detail["artifacts"][0])

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


if __name__ == "__main__":
    unittest.main()
