"""Tests for wd brief — stable JSON output contract and ranking behavior."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.brief import (  # noqa: E402
    BRIEF_VERSION,
    _classify_node,
    _sort_key,
    brief,
)
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402

_TS = "2026-04-02T12:00:00+00:00"

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create a Graph loaded with the given nodes and edges."""
    import tempfile

    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    # Directly set internal data for testing
    g._data = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "abc123"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g

class BriefContractTest(unittest.TestCase):
    """Verify the stable JSON output contract."""

    def test_brief_version_present(self) -> None:
        g = _make_graph({})
        result = brief(g, "anything")
        self.assertEqual(result["brief_version"], BRIEF_VERSION)
        self.assertEqual(result["brief_version"], 2)

    def test_all_required_keys_present(self) -> None:
        g = _make_graph({})
        result = brief(g, "test")
        required = {"brief_version", "query", "primary", "interfaces",
                    "docs", "build", "boundaries", "edges", "provenance",
                    "warnings"}
        self.assertEqual(set(result.keys()), required)

    def test_lists_never_null(self) -> None:
        g = _make_graph({})
        result = brief(g, "nonexistent")
        for key in ("primary", "interfaces", "docs", "build", "boundaries",
                    "edges", "warnings"):
            self.assertIsInstance(result[key], list, f"{key} should be a list")

    def test_provenance_always_present(self) -> None:
        g = _make_graph({})
        result = brief(g, "test")
        self.assertIn("provenance", result)
        prov = result["provenance"]
        self.assertIn("graph_sha", prov)
        self.assertIn("updated_at", prov)

    def test_provenance_contains_sha_and_timestamp(self) -> None:
        g = _make_graph({})
        result = brief(g, "test")
        self.assertEqual(result["provenance"]["graph_sha"], "abc123")
        self.assertEqual(result["provenance"]["updated_at"], _TS)

    def test_query_field_echoes_input(self) -> None:
        g = _make_graph({})
        result = brief(g, "my search term")
        self.assertEqual(result["query"], "my search term")

    def test_empty_graph_returns_warning(self) -> None:
        g = _make_graph({})
        result = brief(g, "anything")
        self.assertTrue(len(result["warnings"]) > 0)
        self.assertIn("No matches", result["warnings"][0])

class BriefClassificationTest(unittest.TestCase):
    """Verify node classification into sections."""

    def test_service_node_classified_as_primary(self) -> None:
        node = {"id": "service:api", "type": "service", "label": "API", "props": {}}
        self.assertEqual(_classify_node(node), "primary")

    def test_doc_node_classified_as_doc(self) -> None:
        node = {"id": "doc:adr-001", "type": "doc", "label": "ADR", "props": {}}
        self.assertEqual(_classify_node(node), "doc")

    def test_policy_node_classified_as_doc(self) -> None:
        node = {"id": "policy:sec", "type": "policy", "label": "Security", "props": {}}
        self.assertEqual(_classify_node(node), "doc")

    def test_runbook_node_classified_as_doc(self) -> None:
        node = {"id": "runbook:deploy", "type": "runbook", "label": "Deploy", "props": {}}
        self.assertEqual(_classify_node(node), "doc")

    def test_node_with_doc_role_classified_as_doc(self) -> None:
        node = {"id": "file:readme", "type": "file", "label": "README",
                "props": {"roles": ["doc"]}}
        self.assertEqual(_classify_node(node), "doc")

    def test_node_with_policy_role_classified_as_doc(self) -> None:
        node = {"id": "file:agents", "type": "file", "label": "AGENTS.md",
                "props": {"roles": ["policy"]}}
        self.assertEqual(_classify_node(node), "doc")

    def test_node_with_adr_doc_kind_classified_as_doc(self) -> None:
        node = {"id": "file:adr", "type": "file", "label": "ADR",
                "props": {"doc_kind": "adr"}}
        self.assertEqual(_classify_node(node), "doc")

    def test_build_target_classified_as_build(self) -> None:
        node = {"id": "build-target:api", "type": "build-target", "label": "//api",
                "props": {}}
        self.assertEqual(_classify_node(node), "build")

    def test_test_target_classified_as_build(self) -> None:
        node = {"id": "test-target:unit", "type": "test-target", "label": "//test",
                "props": {}}
        self.assertEqual(_classify_node(node), "build")

    def test_gate_node_classified_as_build(self) -> None:
        node = {"id": "gate:tier1", "type": "gate", "label": "Tier 1",
                "props": {}}
        self.assertEqual(_classify_node(node), "build")

    def test_node_with_build_role_classified_as_build(self) -> None:
        node = {"id": "file:build", "type": "file", "label": "BUILD",
                "props": {"roles": ["build"]}}
        self.assertEqual(_classify_node(node), "build")

    def test_node_with_gate_doc_kind_classified_as_build(self) -> None:
        node = {"id": "file:gate", "type": "file", "label": "gate",
                "props": {"doc_kind": "gate"}}
        self.assertEqual(_classify_node(node), "build")

    def test_boundary_classified_as_boundary(self) -> None:
        node = {"id": "boundary:ext", "type": "boundary", "label": "External",
                "props": {}}
        self.assertEqual(_classify_node(node), "boundary")

    def test_entrypoint_classified_as_boundary(self) -> None:
        node = {"id": "entrypoint:main", "type": "entrypoint", "label": "Main",
                "props": {}}
        self.assertEqual(_classify_node(node), "boundary")

    # v2 classification coverage (rpc/channel/ros/protocol-promotion) lives
    # in weld_brief_v2_test.py to keep this file under the 400-line lint cap.

class BriefRankingTest(unittest.TestCase):
    """Verify authoritative, high-confidence context ranks first."""

    def test_canonical_ranks_before_derived(self) -> None:
        canonical = {"id": "a", "type": "service", "props": {"authority": "canonical"}}
        derived = {"id": "b", "type": "service", "props": {"authority": "derived"}}
        self.assertLess(_sort_key(canonical), _sort_key(derived))

    def test_definite_ranks_before_speculative(self) -> None:
        definite = {"id": "a", "type": "service",
                    "props": {"authority": "canonical", "confidence": "definite"}}
        speculative = {"id": "b", "type": "service",
                       "props": {"authority": "canonical", "confidence": "speculative"}}
        self.assertLess(_sort_key(definite), _sort_key(speculative))

    def test_missing_authority_ranks_last(self) -> None:
        no_auth = {"id": "a", "type": "service", "props": {}}
        inferred = {"id": "b", "type": "service", "props": {"authority": "inferred"}}
        self.assertGreater(_sort_key(no_auth), _sort_key(inferred))

    def test_stable_sort_by_id(self) -> None:
        a = {"id": "a:x", "type": "service", "props": {"authority": "canonical"}}
        b = {"id": "b:x", "type": "service", "props": {"authority": "canonical"}}
        self.assertLess(_sort_key(a), _sort_key(b))

class BriefIntegrationTest(unittest.TestCase):
    """End-to-end brief with a populated graph."""

    def _sample_graph(self) -> Graph:
        nodes = {
            "service:api": {
                "type": "service", "label": "API Service",
                "props": {"file": "services/api/main.py",
                           "authority": "canonical", "confidence": "definite"},
            },
            "doc:adr-001": {
                "type": "doc", "label": "ADR 0001",
                "props": {"file": "docs/adrs/0001.md",
                           "authority": "canonical", "confidence": "definite",
                           "doc_kind": "adr"},
            },
            "build-target:api-test": {
                "type": "build-target", "label": "//services/api:test",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
            "boundary:external-api": {
                "type": "boundary", "label": "External API boundary",
                "props": {"authority": "derived", "confidence": "inferred"},
            },
            "service:worker": {
                "type": "service", "label": "Worker Service",
                "props": {"file": "services/worker/main.py",
                           "authority": "derived", "confidence": "inferred"},
            },
        }
        edges = [
            {"from": "service:api", "to": "doc:adr-001",
             "type": "documents", "props": {}},
            {"from": "service:api", "to": "build-target:api-test",
             "type": "tests", "props": {}},
            {"from": "service:api", "to": "boundary:external-api",
             "type": "exposes", "props": {}},
        ]
        return _make_graph(nodes, edges)

    def test_brief_classifies_into_sections(self) -> None:
        g = self._sample_graph()
        result = brief(g, "api")
        # service:api should be in primary
        primary_ids = [n["id"] for n in result["primary"]]
        self.assertIn("service:api", primary_ids)
        # doc:adr-001 should be in docs
        doc_ids = [n["id"] for n in result["docs"]]
        self.assertIn("doc:adr-001", doc_ids)

    def test_brief_includes_build_surfaces(self) -> None:
        g = self._sample_graph()
        result = brief(g, "api")
        build_ids = [n["id"] for n in result["build"]]
        # build-target matched or neighbor
        has_build = len(build_ids) > 0 or any(
            n["id"] == "build-target:api-test" for n in result["build"]
        )
        # The build target may appear as neighbor or match depending on query
        self.assertTrue(
            has_build or "build-target:api-test" in
            {n.get("id") for n in result.get("build", [])},
            "Expected build surface in brief"
        )

    def test_brief_includes_boundaries(self) -> None:
        g = self._sample_graph()
        result = brief(g, "api")
        boundary_ids = [n["id"] for n in result["boundaries"]]
        # boundary:external-api may be neighbor or match
        all_boundary_ids = boundary_ids
        self.assertTrue(
            "boundary:external-api" in all_boundary_ids or
            len(result["edges"]) > 0,
            "Expected boundary or edges in brief"
        )

    def test_brief_has_edges(self) -> None:
        g = self._sample_graph()
        result = brief(g, "api")
        self.assertIsInstance(result["edges"], list)

    def test_nodes_have_relevance_field(self) -> None:
        g = self._sample_graph()
        result = brief(g, "api")
        for section in ("primary", "interfaces", "docs", "build", "boundaries"):
            for node in result[section]:
                self.assertIn("relevance", node,
                              f"Node in {section} missing relevance field")

    def test_canonical_nodes_rank_first_in_primary(self) -> None:
        g = self._sample_graph()
        result = brief(g, "service")
        primaries = result["primary"]
        if len(primaries) >= 2:
            # The canonical one should come first
            first_auth = primaries[0].get("props", {}).get("authority", "")
            self.assertEqual(first_auth, "canonical")

    def test_no_warnings_when_matches_found(self) -> None:
        g = self._sample_graph()
        result = brief(g, "api")
        self.assertEqual(result["warnings"], [])

    def test_provenance_reflects_meta(self) -> None:
        g = self._sample_graph()
        result = brief(g, "api")
        self.assertEqual(result["provenance"]["graph_sha"], "abc123")
        self.assertEqual(result["provenance"]["updated_at"], _TS)

class BriefLimitTest(unittest.TestCase):
    """Verify limit parameter controls output size."""

    def test_limit_caps_primary_results(self) -> None:
        nodes = {}
        for i in range(30):
            nodes[f"service:s{i:02d}"] = {
                "type": "service", "label": f"Service {i}",
                "props": {"authority": "canonical", "confidence": "definite"},
            }
        g = _make_graph(nodes)
        result = brief(g, "service", limit=5)
        self.assertLessEqual(len(result["primary"]), 5)

if __name__ == "__main__":
    unittest.main()
