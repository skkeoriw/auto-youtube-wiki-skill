#!/usr/bin/env python3

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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

    def test_evaluation_mapping_maps_metadata_title_to_tg_message(self):
        upstream = self.youtube_fetch_node()
        downstream = self.tg_notify_node()
        data = {
            "edge_handoff_instruction": "从 metadata_file 读取 title 作为 Telegram message。",
            "evaluation": {
                "resolved_handoff": {
                    "relay_mappings": [
                        {"source_output": "metadata_file", "target_input": "message", "resolver": "metadata-title"},
                    ],
                },
            },
        }

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

    def test_instruction_without_mapping_does_not_guess_generic_message_input(self):
        upstream = self.youtube_fetch_node()
        downstream = self.tg_notify_node()
        data = {"edge_handoff_instruction": "从 metadata_file 读取 title 作为 Telegram message。"}

        mappings = bridge.workflow_edge_simulation_mappings({}, data, upstream, downstream)
        fixture = bridge.workflow_edge_generated_fixture(upstream)
        _rows, missing, _fallback_failures, target_resolutions = bridge.workflow_edge_resolve_simulation_inputs(
            fixture,
            downstream,
            mappings,
        )

        self.assertEqual(mappings, [])
        self.assertEqual(missing[0]["input"], "message")
        self.assertFalse(target_resolutions[0]["resolved"])

    def test_metadata_title_mapping_blocks_when_title_is_missing(self):
        upstream = self.youtube_fetch_node()
        downstream = self.tg_notify_node()
        data = {
            "evaluation": {
                "resolved_handoff": {
                    "relay_mappings": [
                        {"source_output": "metadata_file", "target_input": "message", "resolver": "metadata-title"},
                    ],
                },
            },
        }

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

    def test_registry_item_prefers_skill_contract_over_legacy_instance_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp)
            skill = plugin / "skills" / "sop-tg-notify"
            skill.mkdir(parents=True)
            (skill / "node.yaml").write_text(
                "\n".join([
                    "id: tg-notify",
                    "title: Telegram 通知",
                    "executor:",
                    "  type: agent-skill",
                    "  skill: sop-tg-notify",
                    "inputs:",
                    "  message:",
                    "    required: true",
                    "    kind: object",
                    "    type: object",
                    "    value_type: text",
                    "optional_inputs:",
                    "  index:",
                    "    required: false",
                    "    kind: file",
                    "    type: file",
                    "    value_type: markdown",
                    "outputs:",
                    "  telegram_message:",
                    "    kind: file",
                    "    type: file",
                    "    value_type: json",
                ]),
                encoding="utf-8",
            )
            sop = {
                "id": "test-instance",
                "nodes": {
                    "tg-notify": {
                        "title": "Telegram 通知",
                        "skill": "sop-tg-notify",
                        "inputs": {
                            "index": {
                                "required": True,
                                "kind": "file",
                                "type": "file",
                                "value_type": "markdown",
                                "from": "wiki-build.outputs.index",
                            },
                        },
                        "optional_inputs": {
                            "message": {
                                "required": False,
                                "kind": "scalar",
                                "type": "string",
                                "value_type": "text",
                            },
                        },
                        "outputs": {},
                    },
                },
            }

            with patch.dict(os.environ, {"YOUTUBE_WIKI_PLUGIN_DIR": str(plugin)}, clear=False):
                item = bridge.node_registry_item(sop, "tg-notify")

        self.assertIn("message", item["inputs"])
        self.assertNotIn("index", item["inputs"])
        self.assertIn("index", item["optional_inputs"])
        self.assertFalse(item["optional_inputs"]["index"]["required"])

    def test_edge_draft_persists_agent_relay_mappings(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp)
            sop = {
                "id": "test-instance",
                "wiki_local_path": str(wiki),
                "nodes": {
                    "youtube-fetch": {"title": "YouTube Fetch", "outputs": {"metadata_file": "raw/metadata.json"}},
                    "tg-notify": {"title": "TG Notify", "inputs": {"message": {"from": "edge.message"}}},
                },
            }
            result = bridge.create_workflow_edge_draft(sop, "youtube-research-wiki", {
                "edge_handoff_instruction": "Use the evaluated relay mapping.",
                "edge": {
                    "id": "youtube-fetch-to-tg-notify",
                    "from": "youtube-fetch",
                    "to": "tg-notify",
                },
                "evaluation": {
                    "status": "trial_ready",
                    "agent": {"used_ai": True},
                    "node_execution_guide": {"format": "markdown", "prompt": "Use skill sop-tg-notify."},
                    "resolved_handoff": {
                        "relay_mappings": [
                            {"from": "youtube-fetch.outputs.metadata_file", "to": "tg-notify.inputs.message", "resolver": "metadata-title"},
                        ],
                    },
                },
            })

        self.assertEqual(result["validation"]["status"], "passed")
        self.assertEqual(
            result["edge"]["relay"]["mappings"],
            [{"source_output": "metadata_file", "target_input": "message", "resolver": "metadata-title"}],
        )


if __name__ == "__main__":
    unittest.main()
