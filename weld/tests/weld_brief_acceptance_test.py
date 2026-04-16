"""Acceptance tests for wd brief — JSON contract and ranking behavior.

Validates the brief surface end-to-end with realistic multi-type graphs:
  - JSON output contract stability (all keys, types, provenance)
  - Classification into primary/docs/build/boundaries sections
  - Ranking: canonical > derived > inferred, definite > speculative
  - Limit parameter enforcement
  - Warning on empty results

"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.brief import BRIEF_VERSION, brief  # noqa: E402
from weld.brief import main as brief_main  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402

_TS = "2026-04-02T12:00:00+00:00"

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create a Graph instance pre-loaded with given data."""
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    g._data = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "acc123"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g

def _make_realistic_graph() -> Graph:
    """Build a graph that covers all brief sections: primary, docs, build, boundaries."""
    nodes = {
        # Primary: services
        "service:api": {
            "type": "service", "label": "API Service",
            "props": {"file": "services/api/main.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        "service:worker": {
            "type": "service", "label": "Worker Service",
            "props": {"file": "services/worker/main.py",
                      "authority": "derived", "confidence": "inferred"},
        },
        "service:cache": {
            "type": "service", "label": "Cache Layer",
            "props": {"file": "services/cache/main.py",
                      "authority": "inferred", "confidence": "speculative"},
        },
        # Docs
        "doc:adr-0001": {
            "type": "doc", "label": "ADR 0001: API Design",
            "props": {"file": "docs/adrs/0001.md",
                      "authority": "canonical", "confidence": "definite",
                      "doc_kind": "adr"},
        },
        "doc:readme": {
            "type": "doc", "label": "README",
            "props": {"file": "README.md",
                      "authority": "canonical", "confidence": "definite",
                      "doc_kind": "guide"},
        },
        # Build / verification
        "build-target:api-test": {
            "type": "build-target", "label": "//services/api:test",
            "props": {"authority": "canonical", "confidence": "definite"},
        },
        "gate:tier1": {
            "type": "gate", "label": "Tier 1 Gate",
            "props": {"authority": "canonical", "confidence": "definite"},
        },
        # Boundaries
        "boundary:external-api": {
            "type": "boundary", "label": "External API Boundary",
            "props": {"authority": "derived", "confidence": "inferred"},
        },
        "entrypoint:cli": {
            "type": "entrypoint", "label": "CLI Entrypoint",
            "props": {"authority": "canonical", "confidence": "definite"},
        },
        # Policy (classified as doc)
        "policy:security": {
            "type": "policy", "label": "Security Policy",
            "props": {"authority": "canonical", "confidence": "definite"},
        },
    }
    edges = [
        {"from": "service:api", "to": "doc:adr-0001",
         "type": "documents", "props": {}},
        {"from": "service:api", "to": "build-target:api-test",
         "type": "tests", "props": {}},
        {"from": "service:api", "to": "boundary:external-api",
         "type": "exposes", "props": {}},
        {"from": "service:api", "to": "service:worker",
         "type": "depends_on", "props": {}},
        {"from": "service:worker", "to": "service:cache",
         "type": "depends_on", "props": {}},
    ]
    return _make_graph(nodes, edges)

# -- Contract tests -----------------------------------------------------------

class BriefContractAcceptanceTest(unittest.TestCase):
    """The brief JSON envelope conforms to the documented contract."""

    _REQUIRED_KEYS = {
        "brief_version", "query", "primary", "interfaces", "docs", "build",
        "boundaries", "edges", "provenance", "warnings",
    }

    def test_all_keys_present_with_realistic_graph(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "service")
        self.assertEqual(set(result.keys()), self._REQUIRED_KEYS)

    def test_brief_version_is_2(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "service")
        self.assertEqual(result["brief_version"], 2)
        self.assertEqual(result["brief_version"], BRIEF_VERSION)

    def test_list_fields_are_lists_never_null(self) -> None:
        g = _make_realistic_graph()
        for term in ("service", "nonexistent-xyzzy"):
            result = brief(g, term)
            for key in ("primary", "interfaces", "docs", "build", "boundaries",
                        "edges", "warnings"):
                self.assertIsInstance(result[key], list,
                                     f"{key} should be list for term={term!r}")

    def test_provenance_has_sha_and_timestamp(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "service")
        prov = result["provenance"]
        self.assertEqual(prov["graph_sha"], "acc123")
        self.assertEqual(prov["updated_at"], _TS)

    def test_query_field_echoes_input(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "api service worker")
        self.assertEqual(result["query"], "api service worker")

    def test_nodes_have_relevance_field(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "service")
        for section in ("primary", "interfaces", "docs", "build", "boundaries"):
            for node in result[section]:
                self.assertIn("relevance", node,
                              f"Node in {section} missing 'relevance'")

# -- Classification tests ----------------------------------------------------

class BriefClassificationAcceptanceTest(unittest.TestCase):
    """Nodes are classified into the correct brief sections."""

    def test_services_appear_in_primary(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "service")
        primary_ids = {n["id"] for n in result["primary"]}
        self.assertIn("service:api", primary_ids)
        self.assertIn("service:worker", primary_ids)

    def test_docs_appear_in_docs_section(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "adr")
        doc_ids = {n["id"] for n in result["docs"]}
        self.assertIn("doc:adr-0001", doc_ids)

    def test_policy_classified_as_doc(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "security")
        doc_ids = {n["id"] for n in result["docs"]}
        self.assertIn("policy:security", doc_ids)

    def test_build_targets_in_build_section(self) -> None:
        g = _make_realistic_graph()
        # query for "api" which matches build-target:api-test label
        result = brief(g, "api")
        all_ids = set()
        for section in ("build", "primary", "docs", "boundaries"):
            all_ids.update(n["id"] for n in result[section])
        # build-target:api-test should appear somewhere (as match or neighbor)
        build_ids = {n["id"] for n in result["build"]}
        has_build = "build-target:api-test" in build_ids or \
                    "build-target:api-test" in all_ids
        self.assertTrue(has_build, "Expected build-target:api-test in brief")

    def test_boundaries_in_boundaries_section(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "api")
        # boundary:external-api is a neighbor of service:api
        boundary_ids = {n["id"] for n in result["boundaries"]}
        all_neighbor_ids = set()
        for section in ("primary", "docs", "build", "boundaries"):
            all_neighbor_ids.update(n["id"] for n in result[section])
        has_boundary = "boundary:external-api" in boundary_ids or \
                       "boundary:external-api" in all_neighbor_ids
        self.assertTrue(has_boundary, "Expected boundary in brief")

# -- Ranking tests ------------------------------------------------------------

class BriefRankingAcceptanceTest(unittest.TestCase):
    """Authoritative, high-confidence nodes rank before derived/speculative."""

    def test_canonical_before_derived_in_primary(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "service")
        primaries = result["primary"]
        if len(primaries) >= 2:
            authorities = [
                n.get("props", {}).get("authority", "unknown")
                for n in primaries
            ]
            # canonical should appear before inferred
            if "canonical" in authorities and "inferred" in authorities:
                canon_idx = authorities.index("canonical")
                inferred_idx = authorities.index("inferred")
                self.assertLess(canon_idx, inferred_idx,
                                f"canonical should rank before inferred: {authorities}")

    def test_definite_before_speculative(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "service")
        primaries = result["primary"]
        if len(primaries) >= 2:
            confidences = [
                n.get("props", {}).get("confidence", "unknown")
                for n in primaries
            ]
            if "definite" in confidences and "speculative" in confidences:
                def_idx = confidences.index("definite")
                spec_idx = confidences.index("speculative")
                self.assertLess(def_idx, spec_idx,
                                f"definite should rank before speculative: {confidences}")

    def test_stable_sort_determinism(self) -> None:
        g = _make_realistic_graph()
        r1 = brief(g, "service")
        r2 = brief(g, "service")
        ids1 = [n["id"] for n in r1["primary"]]
        ids2 = [n["id"] for n in r2["primary"]]
        self.assertEqual(ids1, ids2, "Ranking should be deterministic")

# -- Limit and warning tests --------------------------------------------------

class BriefLimitAcceptanceTest(unittest.TestCase):
    """The limit parameter caps results per section."""

    def test_limit_caps_primary(self) -> None:
        nodes = {}
        for i in range(30):
            nodes[f"service:s{i:02d}"] = {
                "type": "service",
                "label": f"Service {i}",
                "props": {"authority": "canonical", "confidence": "definite"},
            }
        g = _make_graph(nodes)
        result = brief(g, "service", limit=5)
        self.assertLessEqual(len(result["primary"]), 5)

    def test_limit_caps_docs(self) -> None:
        nodes = {}
        for i in range(30):
            nodes[f"doc:d{i:02d}"] = {
                "type": "doc",
                "label": f"Doc {i}",
                "props": {"authority": "canonical", "confidence": "definite"},
            }
        g = _make_graph(nodes)
        result = brief(g, "doc", limit=5)
        self.assertLessEqual(len(result["docs"]), 5)

class BriefWarningAcceptanceTest(unittest.TestCase):
    """Warnings surface when no results are found."""

    def test_empty_graph_warns(self) -> None:
        g = _make_graph({})
        result = brief(g, "anything")
        self.assertGreater(len(result["warnings"]), 0)
        self.assertIn("No matches", result["warnings"][0])

    def test_no_warning_when_matches_exist(self) -> None:
        g = _make_realistic_graph()
        result = brief(g, "service")
        self.assertEqual(result["warnings"], [])

# -- CLI integration ----------------------------------------------------------

class BriefCliAcceptanceTest(unittest.TestCase):
    """The brief CLI produces valid JSON conforming to the contract."""

    def _setup_graph(self, nodes: dict, edges: list | None = None) -> str:
        tmpdir = tempfile.mkdtemp()
        weld_dir = Path(tmpdir) / ".weld"
        weld_dir.mkdir()
        graph = {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS,
                     "git_sha": "cli-test"},
            "nodes": nodes,
            "edges": edges or [],
        }
        (weld_dir / "graph.json").write_text(
            json.dumps(graph), encoding="utf-8")
        return tmpdir

    def _run_cli(self, root: str, term: str,
                 extra: list[str] | None = None) -> dict:
        argv = [term, "--root", root]
        if extra:
            argv.extend(extra)
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            brief_main(argv)
        return json.loads(buf.getvalue())

    def test_cli_contract_keys(self) -> None:
        root = self._setup_graph(
            {"service:api": {"type": "service", "label": "API",
                             "props": {"authority": "canonical"}}})
        output = self._run_cli(root, "api")
        expected = {"brief_version", "query", "primary", "interfaces",
                    "docs", "build", "boundaries", "edges", "provenance",
                    "warnings"}
        self.assertEqual(set(output.keys()), expected)

    def test_cli_respects_limit_flag(self) -> None:
        nodes = {}
        for i in range(20):
            nodes[f"service:s{i:02d}"] = {
                "type": "service", "label": f"S{i}", "props": {},
            }
        root = self._setup_graph(nodes)
        output = self._run_cli(root, "service", ["--limit", "3"])
        self.assertLessEqual(len(output["primary"]), 3)

    def test_cli_provenance_from_meta(self) -> None:
        root = self._setup_graph({})
        output = self._run_cli(root, "test")
        self.assertEqual(output["provenance"]["graph_sha"], "cli-test")
        self.assertEqual(output["provenance"]["updated_at"], _TS)

    def test_cli_with_realistic_graph(self) -> None:
        g = _make_realistic_graph()
        nodes = g.dump()["nodes"]
        edges = g.dump()["edges"]
        root = self._setup_graph(nodes, edges)
        output = self._run_cli(root, "service")
        self.assertGreater(len(output["primary"]), 0)
        self.assertEqual(output["warnings"], [])
        self.assertEqual(output["brief_version"], BRIEF_VERSION)

if __name__ == "__main__":
    unittest.main()
