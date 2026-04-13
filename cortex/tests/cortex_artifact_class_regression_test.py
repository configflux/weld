"""Regression tests for whole-codebase artifact class discoverability.

Ensures that ``cortex discover`` running against the repo's own
``.cortex/discover.yaml`` produces nodes for every configured artifact
class (node type).

Expectations are derived from discover.yaml so the tests are portable
across any repo that uses cortex discovery.  If a strategy is removed, a
discover.yaml entry is deleted, or a glob pattern stops matching, these
tests will fail — protecting the whole-codebase value proposition.
"""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex._yaml import parse_yaml  # noqa: E402
from cortex.discover import discover  # noqa: E402

_HAS_DISCOVER_YAML = (Path(_repo_root) / ".cortex" / "discover.yaml").exists()
_STANDALONE_SKIP = "No .cortex/discover.yaml — standalone repo has no infrastructure to discover"

_GRAPH: dict | None = None

def _graph() -> dict:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = discover(Path(_repo_root))
    return _GRAPH

def _configured_node_types() -> set[str]:
    """Return the set of node types configured in discover.yaml."""
    config_path = Path(_repo_root) / ".cortex" / "discover.yaml"
    if not config_path.exists():
        return set()
    config = parse_yaml(config_path.read_text(encoding="utf-8"))
    return {
        src["type"]
        for src in config.get("sources", [])
        if src.get("type")
    }

def _configured_strategies() -> set[str]:
    """Return the set of strategy names configured in discover.yaml."""
    config_path = Path(_repo_root) / ".cortex" / "discover.yaml"
    if not config_path.exists():
        return set()
    config = parse_yaml(config_path.read_text(encoding="utf-8"))
    return {
        src["strategy"]
        for src in config.get("sources", [])
        if src.get("strategy")
    }

def _nodes_by_type(ntype: str) -> dict[str, dict]:
    g = _graph()
    return {nid: n for nid, n in g["nodes"].items() if n["type"] == ntype}

def _nodes_by_strategy(strategy: str) -> dict[str, dict]:
    g = _graph()
    return {
        nid: n
        for nid, n in g["nodes"].items()
        if n.get("props", {}).get("source_strategy") == strategy
    }

def _edges_by_type(etype: str) -> list[dict]:
    return [e for e in _graph()["edges"] if e["type"] == etype]

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_DISCOVER_YAML, _STANDALONE_SKIP)
class ArtifactClassPresenceTest(unittest.TestCase):
    """Every configured node type must be present in the discovered graph."""

    def test_each_configured_type_has_nodes(self) -> None:
        """Verify each configured node type produced at least one node."""
        g = _graph()
        type_counts = Counter(n["type"] for n in g["nodes"].values())
        for ntype in sorted(_configured_node_types()):
            with self.subTest(node_type=ntype):
                actual = type_counts.get(ntype, 0)
                self.assertGreater(
                    actual,
                    0,
                    f"Node type '{ntype}' is configured in discover.yaml "
                    f"but has 0 nodes. A strategy or glob may be broken.",
                )

    def test_total_node_count_healthy(self) -> None:
        """The graph should have a non-trivial number of nodes."""
        g = _graph()
        self.assertGreaterEqual(
            len(g["nodes"]), 50,
            "Expected >= 50 total nodes; discovery may be broken.",
        )

@unittest.skipUnless(_HAS_DISCOVER_YAML, _STANDALONE_SKIP)
class StrategyNodeTypeConsistencyTest(unittest.TestCase):
    """Each configured strategy should produce the node type declared for it."""

    def test_strategy_produces_declared_type(self) -> None:
        config_path = Path(_repo_root) / ".cortex" / "discover.yaml"
        if not config_path.exists():
            self.skipTest("No discover.yaml")
        config = parse_yaml(config_path.read_text(encoding="utf-8"))
        g = _graph()

        # Build mapping: strategy -> set of types it actually produced
        actual_types: dict[str, set[str]] = {}
        for n in g["nodes"].values():
            strat = n.get("props", {}).get("source_strategy", "")
            if strat:
                actual_types.setdefault(strat, set()).add(n["type"])

        # For each configured source, check the declared type is produced
        for src in config.get("sources", []):
            strat = src.get("strategy", "")
            declared_type = src.get("type", "")
            if not strat or not declared_type:
                continue
            if strat not in actual_types:
                continue  # Strategy produced nothing — caught by integrity test
            with self.subTest(strategy=strat, declared_type=declared_type):
                self.assertIn(
                    declared_type,
                    actual_types[strat],
                    f"Strategy '{strat}' is configured to produce "
                    f"'{declared_type}' but only produced: "
                    f"{sorted(actual_types[strat])}",
                )

@unittest.skipUnless(_HAS_DISCOVER_YAML, _STANDALONE_SKIP)
class NodePropsQualityTest(unittest.TestCase):
    """Discovered nodes should have required properties."""

    def test_all_nodes_have_source_strategy(self) -> None:
        """Every node should declare its source_strategy."""
        g = _graph()
        missing = [
            nid for nid, n in g["nodes"].items()
            if not n.get("props", {}).get("source_strategy")
        ]
        self.assertEqual(
            missing, [],
            f"{len(missing)} nodes lack source_strategy prop: "
            f"{missing[:5]}{'...' if len(missing) > 5 else ''}",
        )

    def test_file_bearing_nodes_have_file_prop(self) -> None:
        """Node types that represent files should have a 'file' prop."""
        file_types = {"doc", "config", "tool", "workflow", "dockerfile",
                      "runbook", "build-target", "test-target"}
        g = _graph()
        # Only check types that actually exist in the graph
        present_types = file_types & set(
            n["type"] for n in g["nodes"].values()
        )
        for ntype in sorted(present_types):
            nodes = _nodes_by_type(ntype)
            for nid, n in nodes.items():
                with self.subTest(node_id=nid, node_type=ntype):
                    self.assertIn(
                        "file", n.get("props", {}),
                        f"Node {nid} (type={ntype}) missing 'file' prop",
                    )

@unittest.skipUnless(_HAS_DISCOVER_YAML, _STANDALONE_SKIP)
class EdgePresenceTest(unittest.TestCase):
    """The graph should have a meaningful edge structure."""

    def test_calls_edges_if_callgraph_configured(self) -> None:
        """If python_callgraph is configured, calls edges should exist."""
        if "python_callgraph" not in _configured_strategies():
            self.skipTest("python_callgraph not configured")
        calls = _edges_by_type("calls")
        self.assertGreater(
            len(calls), 0,
            "python_callgraph is configured but no 'calls' edges found.",
        )

    def test_edge_types_nonempty(self) -> None:
        """The graph should have at least one edge type."""
        g = _graph()
        edge_types = {e["type"] for e in g["edges"]}
        self.assertGreater(
            len(edge_types), 0,
            "Expected at least one edge type in the graph.",
        )

if __name__ == "__main__":
    unittest.main()
