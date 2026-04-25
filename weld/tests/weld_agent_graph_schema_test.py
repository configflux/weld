"""Contract tests for the Agent Graph schema vocabulary."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import (  # noqa: E402
    SCHEMA_VERSION,
    VALID_EDGE_TYPES,
    VALID_NODE_TYPES,
    validate_edge,
    validate_fragment,
    validate_meta,
    validate_node,
)

_TS = "2026-04-24T00:00:00+00:00"

AGENT_GRAPH_NODE_TYPES = (
    "subagent",
    "skill",
    "instruction",
    "prompt",
    "hook",
    "mcp-server",
    "permission",
    "platform",
    "scope",
)

AGENT_GRAPH_EDGE_TYPES = (
    "uses_skill",
    "uses_command",
    "invokes_agent",
    "handoff_to",
    "references_file",
    "applies_to_path",
    "provides_tool",
    "restricts_tool",
    "triggers_on_event",
    "overrides",
    "duplicates",
    "conflicts_with",
    "implements_workflow",
    "part_of_platform",
    "generated_from",
)


class AgentGraphSchemaVersionTest(unittest.TestCase):
    def test_schema_version_is_five(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 5)
        self.assertEqual(
            validate_meta({"version": SCHEMA_VERSION, "updated_at": _TS}),
            [],
        )

    def test_prior_schema_version_is_rejected(self) -> None:
        errors = validate_meta({"version": 4, "updated_at": _TS})
        self.assertTrue(any("version" in error.field for error in errors))


class AgentGraphNodeVocabularyTest(unittest.TestCase):
    def test_agent_graph_node_types_are_registered(self) -> None:
        missing = [
            node_type
            for node_type in AGENT_GRAPH_NODE_TYPES
            if node_type not in VALID_NODE_TYPES
        ]
        self.assertEqual(missing, [])

    def test_agent_graph_node_types_validate(self) -> None:
        for node_type in AGENT_GRAPH_NODE_TYPES:
            node = {"type": node_type, "label": node_type, "props": {}}
            errors = validate_node(f"{node_type}:demo", node)
            self.assertEqual(errors, [], f"{node_type}: {errors}")

    def test_unknown_agent_graph_node_type_still_fails(self) -> None:
        node = {"type": "mcp_server", "label": "bad", "props": {}}
        errors = validate_node("mcp_server:bad", node)
        self.assertTrue(any("type" in error.field for error in errors))


class AgentGraphEdgeVocabularyTest(unittest.TestCase):
    def test_agent_graph_edge_types_are_registered(self) -> None:
        missing = [
            edge_type
            for edge_type in AGENT_GRAPH_EDGE_TYPES
            if edge_type not in VALID_EDGE_TYPES
        ]
        self.assertEqual(missing, [])

    def test_agent_graph_edge_types_validate(self) -> None:
        node_ids = {"agent:planner", "skill:planning"}
        for edge_type in AGENT_GRAPH_EDGE_TYPES:
            edge = {
                "from": "agent:planner",
                "to": "skill:planning",
                "type": edge_type,
                "props": {},
            }
            errors = validate_edge(edge, node_ids)
            self.assertEqual(errors, [], f"{edge_type}: {errors}")

    def test_unknown_agent_graph_edge_type_still_fails(self) -> None:
        edge = {
            "from": "agent:planner",
            "to": "skill:planning",
            "type": "uses-skills",
            "props": {},
        }
        errors = validate_edge(edge, {"agent:planner", "skill:planning"})
        self.assertTrue(any("type" in error.field for error in errors))

    def test_representative_agent_graph_fragment_validates(self) -> None:
        fragment = {
            "nodes": {
                "agent:planner": {"type": "agent", "label": "planner", "props": {}},
                "skill:planning": {
                    "type": "skill",
                    "label": "planning",
                    "props": {},
                },
                "platform:claude": {
                    "type": "platform",
                    "label": "Claude",
                    "props": {},
                },
                "scope:src": {"type": "scope", "label": "src/**", "props": {}},
                "tool:file-write": {
                    "type": "tool",
                    "label": "file-write",
                    "props": {},
                },
                "permission:readonly": {
                    "type": "permission",
                    "label": "read-only",
                    "props": {},
                },
            },
            "edges": [
                {
                    "from": "agent:planner",
                    "to": "skill:planning",
                    "type": "uses_skill",
                    "props": {},
                },
                {
                    "from": "agent:planner",
                    "to": "platform:claude",
                    "type": "part_of_platform",
                    "props": {},
                },
                {
                    "from": "agent:planner",
                    "to": "scope:src",
                    "type": "applies_to_path",
                    "props": {},
                },
                {
                    "from": "permission:readonly",
                    "to": "tool:file-write",
                    "type": "restricts_tool",
                    "props": {},
                },
            ],
        }
        self.assertEqual(validate_fragment(fragment), [])


if __name__ == "__main__":
    unittest.main()
