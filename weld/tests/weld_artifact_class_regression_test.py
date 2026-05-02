"""Regression tests for whole-codebase artifact class discoverability.

Two complementary suites:

1. Host-repo suites (``ArtifactClassPresenceTest`` etc.) run against
   the repository's own ``.weld/discover.yaml`` when present. They give
   the strongest signal because they exercise every configured strategy
   and glob in real conditions, but they are gated on the host config
   being present.
2. Synthetic suites (``SyntheticArtifactClass*Test``) always run. They
   build a minimal fixture (``.weld/discover.yaml`` plus a tiny Python
   module and a tiny markdown file) inside a temp directory and exercise
   the same assertions against that graph. This ensures default
   ``bazel test //weld/tests/...`` runs never silently pass by skipping
   in a standalone environment that lacks the dev YAML tooling.

If a strategy is removed, a discover.yaml entry is deleted, or a glob
pattern stops matching, the host suites will fail; if discovery itself
breaks (no nodes, dangling edges, missing props) the synthetic suites
will fail in any environment.
"""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._yaml import parse_yaml  # noqa: E402
from weld.discover import discover  # noqa: E402
from weld.tests.regression_fixture_helpers import (  # noqa: E402
    SYNTH_NODE_TYPES,
    SyntheticGraphMixin,
)

_HAS_DISCOVER_YAML = (Path(_repo_root) / ".weld" / "discover.yaml").exists()
_STANDALONE_SKIP = "No .weld/discover.yaml — standalone repo has no infrastructure to discover"

_GRAPH: dict | None = None


def _graph() -> dict:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = discover(Path(_repo_root), incremental=False)
    return _GRAPH

def _configured_node_types() -> set[str]:
    """Return the set of node types configured in discover.yaml."""
    config_path = Path(_repo_root) / ".weld" / "discover.yaml"
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
    config_path = Path(_repo_root) / ".weld" / "discover.yaml"
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
        config_path = Path(_repo_root) / ".weld" / "discover.yaml"
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

# ---------------------------------------------------------------------------
# Synthetic-fixture suites — always run, regardless of host environment
# ---------------------------------------------------------------------------


class SyntheticArtifactClassPresenceTest(
    SyntheticGraphMixin, unittest.TestCase
):
    """Synthetic counterpart of ``ArtifactClassPresenceTest`` (always runs)."""

    SYNTH_PREFIX = "weld-artifact-presence-"

    def test_each_configured_type_has_nodes(self) -> None:
        type_counts = Counter(
            n["type"] for n in self.graph["nodes"].values()
        )
        for ntype in sorted(SYNTH_NODE_TYPES):
            with self.subTest(node_type=ntype):
                self.assertGreater(
                    type_counts.get(ntype, 0), 0,
                    f"Synthetic fixture configures node type '{ntype}' "
                    f"but discovery produced 0 such nodes.",
                )

    def test_total_node_count_matches_fixture(self) -> None:
        # Fixture has 2 .py files + 1 .md file = at least 3 file-bearing
        # nodes; python_callgraph is not configured so symbol nodes do
        # not appear. We assert >= 3 to keep the lower bound robust to
        # additional bookkeeping nodes the discovery pipeline may emit.
        self.assertGreaterEqual(
            len(self.graph["nodes"]), 3,
            "Synthetic fixture should produce >= 3 nodes "
            "(2 python modules + 1 doc); discovery may be broken.",
        )


class SyntheticStrategyNodeTypeConsistencyTest(
    SyntheticGraphMixin, unittest.TestCase
):
    """Synthetic counterpart of ``StrategyNodeTypeConsistencyTest``."""

    SYNTH_PREFIX = "weld-strategy-consistency-"

    def test_strategy_produces_declared_type(self) -> None:
        actual_types: dict[str, set[str]] = {}
        for n in self.graph["nodes"].values():
            strat = n.get("props", {}).get("source_strategy", "")
            if strat:
                actual_types.setdefault(strat, set()).add(n["type"])

        expected = {"python_module": "file", "markdown": "doc"}
        for strat, declared in expected.items():
            with self.subTest(strategy=strat, declared_type=declared):
                self.assertIn(
                    strat, actual_types,
                    f"Strategy '{strat}' produced no nodes in synthetic "
                    f"fixture; glob may have stopped matching.",
                )
                self.assertIn(
                    declared, actual_types[strat],
                    f"Strategy '{strat}' produced "
                    f"{sorted(actual_types[strat])} but should produce "
                    f"'{declared}'.",
                )


class SyntheticNodePropsQualityTest(
    SyntheticGraphMixin, unittest.TestCase
):
    """Synthetic counterpart of ``NodePropsQualityTest``."""

    SYNTH_PREFIX = "weld-node-props-"

    def test_all_nodes_have_source_strategy(self) -> None:
        missing = [
            nid for nid, n in self.graph["nodes"].items()
            if not n.get("props", {}).get("source_strategy")
        ]
        self.assertEqual(
            missing, [],
            f"{len(missing)} synthetic-fixture nodes lack source_strategy: "
            f"{missing[:5]}",
        )

    def test_file_bearing_nodes_have_file_prop(self) -> None:
        # In the synthetic fixture, every node is file-bearing (python
        # modules + markdown docs). Each must carry a ``file`` prop.
        for nid, n in self.graph["nodes"].items():
            with self.subTest(node_id=nid, node_type=n["type"]):
                self.assertIn(
                    "file", n.get("props", {}),
                    f"Node {nid} (type={n['type']}) missing 'file' prop "
                    f"in synthetic fixture.",
                )


class SyntheticEdgePresenceTest(SyntheticGraphMixin, unittest.TestCase):
    """Synthetic counterpart of ``EdgePresenceTest`` (always runs).

    The synthetic fixture does not configure ``python_callgraph``, so we
    do not assert ``calls`` edges here; instead we cover the meta-graph
    invariants (``edges`` field is a list, no dangling refs).
    """

    SYNTH_PREFIX = "weld-edge-presence-"

    def test_edges_field_is_a_list(self) -> None:
        self.assertIsInstance(
            self.graph["edges"], list,
            "graph['edges'] must always be a list; discovery contract.",
        )

    def test_no_dangling_edge_references_in_synthetic_fixture(self) -> None:
        node_ids = set(self.graph["nodes"].keys())
        dangling: list[str] = []
        for edge in self.graph["edges"]:
            if edge["from"] not in node_ids:
                dangling.append(f"from={edge['from']}")
            if edge["to"] not in node_ids:
                dangling.append(f"to={edge['to']}")
        self.assertEqual(
            dangling, [],
            f"Synthetic graph has {len(dangling)} dangling edge refs.",
        )


if __name__ == "__main__":
    unittest.main()
