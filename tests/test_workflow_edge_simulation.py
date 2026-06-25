#!/usr/bin/env python3

import importlib.util
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "bridge", Path(__file__).resolve().parents[1] / "scripts" / "bridge.py"
)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class WorkflowEdgeSimulationTest(unittest.TestCase):
    def youtube_fetch_node(self):
        return {
            "node_id": "youtube-fetch",
            "outputs": {
                "source_url": {
                    "kind": "scalar",
                    "type": "string",
                    "value_type": "url",
                    "path": "raw/node-runs/{pipeline_id}/outputs/files/source-url.txt",
                },
                "metadata_file": {
                    "kind": "file",
                    "type": "file",
                    "value_type": "json",
                    "path": "raw/node-runs/{pipeline_id}/outputs/files/metadata.json",
                },
            },
        }

    def tg_notify_node(self):
        return {
            "node_id": "tg-notify",
            "inputs": {
                "message": {
                    "required": True,
                    "kind": "object",
                    "type": "object",
                    "value_type": "text",
                    "resolvers": [
                        {"id": "metadata-title", "kind": "json_path", "path": "$.title"},
                    ],
                },
            },
        }

    def test_instruction_maps_metadata_title_to_tg_message(self):
        upstream = self.youtube_fetch_node()
        downstream = self.tg_notify_node()
        data = {"edge_handoff_instruction": "从 metadata_file 读取 title 作为 Telegram message。"}

        mappings = bridge.workflow_edge_simulation_mappings({}, data, upstream, downstream)
        fixture = bridge.workflow_edge_generated_fixture(upstream)
        rows, missing, _fallback_failures, _target_resolutions = bridge.workflow_edge_resolve_simulation_inputs(
            fixture,
            downstream,
            mappings,
        )

        self.assertEqual(mappings, [{"source_output": "metadata_file", "target_input": "message", "resolver": "metadata-title"}])
        self.assertEqual(missing, [])
        self.assertEqual(rows[0]["value"], "Generated fixture video title")

    def test_metadata_title_mapping_blocks_when_title_is_missing(self):
        upstream = self.youtube_fetch_node()
        downstream = self.tg_notify_node()
        data = {"edge_handoff_instruction": "从 metadata_file 读取 title 作为 Telegram message。"}

        mappings = bridge.workflow_edge_simulation_mappings({}, data, upstream, downstream)
        fixture = bridge.workflow_edge_generated_fixture(upstream)
        for item in fixture["items"]:
            if item["name"] == "metadata_file":
                item["value"] = {"source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        _rows, missing, _fallback_failures, target_resolutions = bridge.workflow_edge_resolve_simulation_inputs(
            fixture,
            downstream,
            mappings,
        )

        self.assertEqual(missing[0]["input"], "message")
        self.assertFalse(target_resolutions[0]["resolved"])
        self.assertIn("did not match", target_resolutions[0]["reason"])


if __name__ == "__main__":
    unittest.main()
