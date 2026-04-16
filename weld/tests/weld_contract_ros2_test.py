"""Contract tests for the ROS2 vocabulary added by SCHEMA_VERSION 3.

the rationale, the seven new ``ros_*`` node types, and the decision to
reuse existing edge semantics rather than introduce new edge types.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import (  # noqa: E402
    SCHEMA_VERSION,
    VALID_EDGE_TYPES,
    VALID_NODE_TYPES,
    validate_graph,
    validate_meta,
    validate_node,
)

_TS = "2026-04-07T12:00:00+00:00"

_ROS2_NODE_TYPES = [
    "ros_package", "ros_interface", "ros_node",
    "ros_topic", "ros_service", "ros_action", "ros_parameter",
]

class Ros2SchemaTest(unittest.TestCase):
    """project-f7y.3: ROS2 vocabulary and SCHEMA_VERSION bump (ADR 0016)."""

    def test_schema_version_at_least_three(self) -> None:
        # ROS2 vocabulary landed in v3; later bumps (e.g. v4 for the
        # interaction-surface vocabulary under ADR 0018) must preserve it.
        self.assertGreaterEqual(SCHEMA_VERSION, 3)

    def test_ros2_node_types_in_vocabulary(self) -> None:
        for t in _ROS2_NODE_TYPES:
            self.assertIn(t, VALID_NODE_TYPES, f"{t!r} missing")

    def test_ros2_node_types_pass_validation(self) -> None:
        for t in _ROS2_NODE_TYPES:
            errs = validate_node(f"{t}:demo", {"type": t, "label": t, "props": {}})
            self.assertEqual(errs, [], f"{t!r}: {errs}")

    def test_ros2_node_types_accept_optional_metadata(self) -> None:
        # Statically-extracted ROS2 names will travel with confidence=inferred;
        # the new node types must still accept the standard optional vocabulary.
        props = {
            "source_strategy": "ros2_treesitter",
            "authority": "derived",
            "confidence": "inferred",
            "file": "src/demo/demo.cpp",
        }
        for t in _ROS2_NODE_TYPES:
            errs = validate_node(
                f"{t}:demo", {"type": t, "label": t, "props": dict(props)}
            )
            self.assertEqual(errs, [], f"{t!r}: {errs}")

    def test_unknown_ros_like_type_still_rejected(self) -> None:
        # Only the seven documented types are allowed; other ros_* strings reject.
        errs = validate_node(
            "ros_universe:x",
            {"type": "ros_universe", "label": "X", "props": {}},
        )
        self.assertTrue(any("type" in e.field for e in errs))

    def test_no_new_edge_types_introduced(self) -> None:
        # ADR 0016 reuses the existing edge vocabulary; no ros_* edges allowed.
        for t in VALID_EDGE_TYPES:
            self.assertFalse(t.startswith("ros_"), f"unexpected ros_* edge: {t!r}")

    def test_validate_meta_accepts_current_schema(self) -> None:
        self.assertEqual(
            validate_meta({"version": SCHEMA_VERSION, "updated_at": _TS}), []
        )

    def test_validate_meta_rejects_old_schema_two(self) -> None:
        errs = validate_meta({"version": 2, "updated_at": _TS})
        self.assertTrue(any("version" in e.field for e in errs))

    def test_ros2_nodes_in_full_graph_validate(self) -> None:
        # Smoke: a tiny graph using the ROS2 vocabulary + existing edges validates.
        graph = {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
            "nodes": {
                "ros_package:demo": {
                    "type": "ros_package", "label": "demo", "props": {},
                },
                "ros_node:Talker": {
                    "type": "ros_node", "label": "Talker", "props": {},
                },
                "ros_topic:/chatter": {
                    "type": "ros_topic", "label": "/chatter", "props": {},
                },
                "ros_interface:std_msgs/String": {
                    "type": "ros_interface", "label": "String", "props": {},
                },
                "ros_parameter:rate": {
                    "type": "ros_parameter", "label": "rate", "props": {},
                },
            },
            "edges": [
                {"from": "ros_package:demo", "to": "ros_node:Talker",
                 "type": "contains", "props": {}},
                {"from": "ros_node:Talker", "to": "ros_topic:/chatter",
                 "type": "produces", "props": {}},
                {"from": "ros_node:Talker", "to": "ros_interface:std_msgs/String",
                 "type": "implements", "props": {}},
                {"from": "ros_node:Talker", "to": "ros_parameter:rate",
                 "type": "exposes", "props": {}},
            ],
        }
        self.assertEqual(validate_graph(graph), [])

if __name__ == "__main__":
    unittest.main()
