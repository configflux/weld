"""Tests for the weld normalized metadata contract and graph validation.

ROS2-vocabulary tests for project-f7y.3 live in weld_contract_ros2_test.py.
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
    AUTHORITY_VALUES,
    CONFIDENCE_VALUES,
    EDGE_OPTIONAL_PROPS,
    NODE_OPTIONAL_PROPS,
    ROLE_VALUES,
    SCHEMA_VERSION,
    ValidationError,
    validate_edge,
    validate_graph,
    validate_meta,
    validate_node,
)
from weld.contract import VALID_EDGE_TYPES, VALID_NODE_TYPES  # noqa: E402

def _node(overrides: dict | None = None) -> dict:
    n = {"type": "service", "label": "API", "props": {"file": "services/api/main.py"}}
    if overrides:
        n.update(overrides)
    return n

def _edge(overrides: dict | None = None) -> dict:
    e = {"from": "service:api", "to": "package:domain", "type": "depends_on", "props": {}}
    if overrides:
        e.update(overrides)
    return e

_TS = "2026-04-02T12:00:00+00:00"

def _graph(overrides: dict | None = None) -> dict:
    g = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
        "nodes": {"service:api": {"type": "service", "label": "API", "props": {}},
                  "package:domain": {"type": "package", "label": "Domain", "props": {}}},
        "edges": [{"from": "service:api", "to": "package:domain", "type": "depends_on", "props": {}}],
    }
    if overrides:
        g.update(overrides)
    return g

class ValidateNodeTest(unittest.TestCase):

    def test_minimal_valid_node(self) -> None:
        errors = validate_node("service:api", _node())
        self.assertEqual(errors, [])

    def test_missing_type(self) -> None:
        node = _node()
        del node["type"]
        errors = validate_node("service:api", node)
        self.assertTrue(any("type" in e.field for e in errors))

    def test_missing_label(self) -> None:
        node = _node()
        del node["label"]
        errors = validate_node("service:api", node)
        self.assertTrue(any("label" in e.field for e in errors))

    def test_missing_props(self) -> None:
        node = _node()
        del node["props"]
        errors = validate_node("service:api", node)
        self.assertTrue(any("props" in e.field for e in errors))

    def test_invalid_node_type(self) -> None:
        node = _node({"type": "spaceship"})
        errors = validate_node("x:y", node)
        self.assertTrue(any("type" in e.field for e in errors))

    def test_valid_optional_metadata_source_strategy(self) -> None:
        node = _node()
        node["props"]["source_strategy"] = "sqlalchemy"
        errors = validate_node("entity:Foo", node)
        self.assertEqual(errors, [])

    def test_valid_optional_metadata_authority(self) -> None:
        for value in AUTHORITY_VALUES:
            node = _node()
            node["props"]["authority"] = value
            errors = validate_node("entity:Foo", node)
            self.assertEqual(errors, [], f"authority={value!r} should be valid")

    def test_invalid_authority_value(self) -> None:
        node = _node()
        node["props"]["authority"] = "supreme"
        errors = validate_node("entity:Foo", node)
        self.assertTrue(any("authority" in e.field for e in errors))

    def test_valid_confidence_values(self) -> None:
        for value in CONFIDENCE_VALUES:
            node = _node()
            node["props"]["confidence"] = value
            errors = validate_node("entity:Foo", node)
            self.assertEqual(errors, [], f"confidence={value!r} should be valid")

    def test_invalid_confidence_value(self) -> None:
        node = _node()
        node["props"]["confidence"] = "maybe"
        errors = validate_node("entity:Foo", node)
        self.assertTrue(any("confidence" in e.field for e in errors))

    def test_valid_roles_list(self) -> None:
        node = _node()
        node["props"]["roles"] = ["implementation", "test"]
        errors = validate_node("file:api/main", node)
        self.assertEqual(errors, [])

    def test_invalid_role_value(self) -> None:
        node = _node()
        node["props"]["roles"] = ["implementation", "banana"]
        errors = validate_node("file:api/main", node)
        self.assertTrue(any("roles" in e.field for e in errors))

    def test_roles_must_be_list(self) -> None:
        node = _node()
        node["props"]["roles"] = "implementation"
        errors = validate_node("file:api/main", node)
        self.assertTrue(any("roles" in e.field for e in errors))

    def test_valid_span(self) -> None:
        node = _node()
        node["props"]["span"] = {"start_line": 10, "end_line": 25}
        errors = validate_node("file:api/main", node)
        self.assertEqual(errors, [])

    def test_invalid_span_missing_fields(self) -> None:
        node = _node()
        node["props"]["span"] = {"start_line": 10}
        errors = validate_node("file:api/main", node)
        self.assertTrue(any("span" in e.field for e in errors))

    def test_invalid_span_non_integer(self) -> None:
        node = _node()
        node["props"]["span"] = {"start_line": "ten", "end_line": 25}
        errors = validate_node("file:api/main", node)
        self.assertTrue(any("span" in e.field for e in errors))

    def test_invalid_span_start_after_end(self) -> None:
        node = _node()
        node["props"]["span"] = {"start_line": 30, "end_line": 10}
        errors = validate_node("file:api/main", node)
        self.assertTrue(any("span" in e.field for e in errors))

    def test_omission_preferred_over_guessing(self) -> None:
        # Optional metadata fields may be absent entirely; that is valid.
        node = _node()
        # No source_strategy, authority, confidence, roles, span
        node["props"] = {"file": "foo.py"}
        errors = validate_node("file:foo", node)
        self.assertEqual(errors, [])

    def test_source_strategy_must_be_string(self) -> None:
        node = _node()
        node["props"]["source_strategy"] = 42
        errors = validate_node("entity:Foo", node)
        self.assertTrue(any("source_strategy" in e.field for e in errors))

    def test_file_must_be_string(self) -> None:
        node = _node()
        node["props"]["file"] = 42
        errors = validate_node("entity:Foo", node)
        self.assertTrue(any("file" in e.field for e in errors))

class ValidateEdgeTest(unittest.TestCase):

    def test_minimal_valid_edge(self) -> None:
        node_ids = {"service:api", "package:domain"}
        errors = validate_edge(_edge(), node_ids)
        self.assertEqual(errors, [])

    def test_missing_from(self) -> None:
        edge = _edge()
        del edge["from"]
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertTrue(any("from" in e.field for e in errors))

    def test_missing_to(self) -> None:
        edge = _edge()
        del edge["to"]
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertTrue(any("to" in e.field for e in errors))

    def test_missing_type(self) -> None:
        edge = _edge()
        del edge["type"]
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertTrue(any("type" in e.field for e in errors))

    def test_missing_props(self) -> None:
        edge = _edge()
        del edge["props"]
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertTrue(any("props" in e.field for e in errors))

    def test_invalid_edge_type(self) -> None:
        edge = _edge({"type": "teleports_to"})
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertTrue(any("type" in e.field for e in errors))

    def test_dangling_from_reference(self) -> None:
        edge = _edge({"from": "service:nonexistent"})
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertTrue(any("from" in e.field for e in errors))

    def test_dangling_to_reference(self) -> None:
        edge = _edge({"to": "package:nonexistent"})
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertTrue(any("to" in e.field for e in errors))

    def test_valid_optional_edge_source_strategy(self) -> None:
        edge = _edge()
        edge["props"]["source_strategy"] = "topology"
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertEqual(errors, [])

    def test_valid_optional_edge_confidence(self) -> None:
        edge = _edge()
        edge["props"]["confidence"] = "inferred"
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertEqual(errors, [])

    def test_invalid_edge_confidence(self) -> None:
        edge = _edge()
        edge["props"]["confidence"] = "maybe"
        errors = validate_edge(edge, {"service:api", "package:domain"})
        self.assertTrue(any("confidence" in e.field for e in errors))

class ValidateMetaTest(unittest.TestCase):

    def test_valid_meta(self) -> None:
        self.assertEqual(validate_meta({"version": SCHEMA_VERSION, "updated_at": _TS}), [])

    def test_missing_version(self) -> None:
        self.assertTrue(any("version" in e.field for e in validate_meta({"updated_at": _TS})))

    def test_missing_updated_at(self) -> None:
        self.assertTrue(any("updated_at" in e.field for e in validate_meta({"version": SCHEMA_VERSION})))

    def test_wrong_version_type(self) -> None:
        self.assertTrue(any("version" in e.field for e in validate_meta({"version": "1", "updated_at": _TS})))

    def test_unsupported_version(self) -> None:
        self.assertTrue(any("version" in e.field for e in validate_meta({"version": 999, "updated_at": _TS})))

    def test_optional_git_sha(self) -> None:
        meta = {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "abc123"}
        self.assertEqual(validate_meta(meta), [])

    def test_optional_discovered_from(self) -> None:
        meta = {"version": SCHEMA_VERSION, "updated_at": _TS, "discovered_from": ["libs/domain/"]}
        self.assertEqual(validate_meta(meta), [])

class ValidateGraphTest(unittest.TestCase):

    def test_valid_graph(self) -> None:
        self.assertEqual(validate_graph(_graph()), [])

    def _assert_field(self, graph: dict, field: str) -> None:
        self.assertTrue(any(field in e.field for e in validate_graph(graph)))

    def test_missing_meta(self) -> None:
        g = _graph()
        del g["meta"]
        self._assert_field(g, "meta")

    def test_missing_nodes(self) -> None:
        g = _graph()
        del g["nodes"]
        self._assert_field(g, "nodes")

    def test_missing_edges(self) -> None:
        g = _graph()
        del g["edges"]
        self._assert_field(g, "edges")

    def test_nodes_must_be_dict(self) -> None:
        self._assert_field(_graph({"nodes": []}), "nodes")

    def test_edges_must_be_list(self) -> None:
        self._assert_field(_graph({"edges": {}}), "edges")

    def test_propagates_node_errors(self) -> None:
        g = _graph()
        g["nodes"]["bad:x"] = {"type": "spaceship", "label": "X", "props": {}}
        self._assert_field(g, "type")

    def test_propagates_edge_errors(self) -> None:
        g = _graph()
        g["edges"].append({"from": "service:api", "to": "package:domain",
                           "type": "teleports_to", "props": {}})
        self._assert_field(g, "type")

    def test_detects_dangling_edge(self) -> None:
        g = _graph()
        g["edges"].append({"from": "service:api", "to": "ghost:x",
                           "type": "depends_on", "props": {}})
        self._assert_field(g, "to")

    def test_empty_graph_valid(self) -> None:
        g = {"meta": {"version": SCHEMA_VERSION, "updated_at": _TS}, "nodes": {}, "edges": []}
        self.assertEqual(validate_graph(g), [])

class ValidationErrorTest(unittest.TestCase):

    def test_str_representation(self) -> None:
        s = str(ValidationError("nodes.service:api", "type", "invalid node type: spaceship"))
        self.assertIn("service:api", s)
        self.assertIn("spaceship", s)

    def test_equality(self) -> None:
        self.assertEqual(ValidationError("x", "y", "z"), ValidationError("x", "y", "z"))

class VocabularyConstantsTest(unittest.TestCase):

    def test_authority_values_non_empty(self) -> None:
        self.assertTrue(len(AUTHORITY_VALUES) > 0)

    def test_confidence_values_non_empty(self) -> None:
        self.assertTrue(len(CONFIDENCE_VALUES) > 0)

    def test_role_values_non_empty(self) -> None:
        self.assertTrue(len(ROLE_VALUES) > 0)

    def test_node_optional_props_documented(self) -> None:
        expected = {"source_strategy", "authority", "confidence", "roles", "file", "span"}
        self.assertTrue(expected.issubset(set(NODE_OPTIONAL_PROPS)))

    def test_edge_optional_props_documented(self) -> None:
        expected = {"source_strategy", "confidence"}
        self.assertTrue(expected.issubset(set(EDGE_OPTIONAL_PROPS)))

    # -- project-0hh: agent-relevant vocabulary expansion --

    _AGENT_NODE_TYPES = ["policy", "runbook", "build-target", "test-target", "boundary", "entrypoint"]
    _AGENT_EDGE_TYPES = ["enforces", "verifies", "exposes"]
    _ORIGINAL_NODE_TYPES = [
        "service", "package", "entity", "stage", "concept", "doc", "route",
        "contract", "enum", "file", "dockerfile", "compose", "agent",
        "command", "tool", "workflow", "test-suite", "config",
    ]
    _ORIGINAL_EDGE_TYPES = [
        "contains", "depends_on", "produces", "consumes", "implements",
        "documents", "relates_to", "responds_with", "accepts", "builds",
        "orchestrates", "invokes", "configures", "tests", "represents", "feeds_into",
    ]

    def test_agent_node_types_in_vocabulary(self) -> None:
        for t in self._AGENT_NODE_TYPES:
            self.assertIn(t, VALID_NODE_TYPES, f"{t!r} missing")

    def test_agent_node_types_pass_validation(self) -> None:
        for t in self._AGENT_NODE_TYPES:
            errs = validate_node(f"{t}:x", {"type": t, "label": t, "props": {}})
            self.assertEqual(errs, [], f"{t!r} errors: {errs}")

    def test_original_node_types_preserved(self) -> None:
        for t in self._ORIGINAL_NODE_TYPES:
            self.assertIn(t, VALID_NODE_TYPES, f"original {t!r} missing")

    def test_agent_edge_types_in_vocabulary(self) -> None:
        for t in self._AGENT_EDGE_TYPES:
            self.assertIn(t, VALID_EDGE_TYPES, f"{t!r} missing")

    def test_agent_edge_types_pass_validation(self) -> None:
        ids = {"a:1", "b:2"}
        for t in self._AGENT_EDGE_TYPES:
            errs = validate_edge({"from": "a:1", "to": "b:2", "type": t, "props": {}}, ids)
            self.assertEqual(errs, [], f"{t!r} errors: {errs}")

    def test_original_edge_types_preserved(self) -> None:
        for t in self._ORIGINAL_EDGE_TYPES:
            self.assertIn(t, VALID_EDGE_TYPES, f"original {t!r} missing")

if __name__ == "__main__":
    unittest.main()
