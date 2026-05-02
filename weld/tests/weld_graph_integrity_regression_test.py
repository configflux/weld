"""Regression tests for strategy presence, edge integrity, and graph meta.

Two complementary suites:

1. Host-repo suites (``StrategyPresenceTest``, ``EdgeIntegrityTest``,
   ``GraphMetaTest``) run against the repository's own
   ``.weld/discover.yaml`` when present. They cover every configured
   strategy in real conditions but are gated on the host config.
2. Synthetic suites (``SyntheticStrategyPresenceTest`` etc.) always
   run. They build the canonical synthetic fixture (see
   ``regression_fixture_helpers``) and assert the same invariants
   against that graph. This guarantees default
   ``bazel test //weld/tests/...`` runs never silently pass by skipping
   in a standalone environment that lacks the dev YAML tooling.
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
    SYNTH_STRATEGIES,
    SyntheticGraphMixin,
    source_should_require_output,
)

_HAS_DISCOVER_YAML = (Path(_repo_root) / ".weld" / "discover.yaml").exists()
_STANDALONE_SKIP = "No .weld/discover.yaml — standalone repo has no infrastructure to discover"

_GRAPH: dict | None = None

def _graph() -> dict:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = discover(Path(_repo_root), incremental=False)
    return _GRAPH

def _configured_strategies() -> set[str]:
    """Return the set of strategy names configured in discover.yaml."""
    config_path = Path(_repo_root) / ".weld" / "discover.yaml"
    if not config_path.exists():
        return set()
    config = parse_yaml(config_path.read_text(encoding="utf-8"))
    return {
        src["strategy"]
        for src in config.get("sources", [])
        if src.get("strategy") and source_should_require_output(Path(_repo_root), src)
    }

@unittest.skipUnless(_HAS_DISCOVER_YAML, _STANDALONE_SKIP)
class StrategyPresenceTest(unittest.TestCase):
    """Every configured strategy should produce at least one node."""

    def test_each_configured_strategy_produces_nodes(self) -> None:
        g = _graph()
        strategy_counts = Counter(
            n.get("props", {}).get("source_strategy", "unknown")
            for n in g["nodes"].values()
        )
        configured = _configured_strategies()
        self.assertGreater(
            len(configured), 0,
            "discover.yaml has no configured strategies.",
        )
        for strat in sorted(configured):
            with self.subTest(strategy=strat):
                actual = strategy_counts.get(strat, 0)
                self.assertGreater(
                    actual,
                    0,
                    f"Strategy '{strat}' is configured in discover.yaml "
                    f"but produced 0 nodes. Its glob pattern may not match "
                    f"any files, or the strategy may be broken.",
                )

@unittest.skipUnless(_HAS_DISCOVER_YAML, _STANDALONE_SKIP)
class EdgeIntegrityTest(unittest.TestCase):
    """Edges should reference existing nodes (no dangling references)."""

    def test_no_dangling_edge_references(self) -> None:
        g = _graph()
        node_ids = set(g["nodes"].keys())
        dangling = []
        for edge in g["edges"]:
            if edge["from"] not in node_ids:
                dangling.append(f"from={edge['from']}")
            if edge["to"] not in node_ids:
                dangling.append(f"to={edge['to']}")
        self.assertEqual(
            dangling, [],
            f"Found {len(dangling)} dangling edge references: "
            f"{dangling[:5]}{'...' if len(dangling) > 5 else ''}",
        )

    def test_minimum_edge_count(self) -> None:
        """A discovered graph should have a healthy number of edges."""
        g = _graph()
        self.assertGreaterEqual(
            len(g["edges"]), 50,
            "Expected >= 50 edges in the graph; "
            "edge production may be broken.",
        )

@unittest.skipUnless(_HAS_DISCOVER_YAML, _STANDALONE_SKIP)
class GraphMetaTest(unittest.TestCase):
    """Graph meta block should be well-formed."""

    def test_meta_version(self) -> None:
        from weld.contract import SCHEMA_VERSION

        g = _graph()
        self.assertEqual(g["meta"]["version"], SCHEMA_VERSION)

    def test_meta_has_updated_at(self) -> None:
        g = _graph()
        self.assertIn("updated_at", g["meta"])

    def test_discovered_from_is_populated(self) -> None:
        g = _graph()
        discovered_from = g["meta"].get("discovered_from", [])
        configured = _configured_strategies()
        # At least half the configured strategies should report source files
        min_expected = max(1, len(configured) // 2)
        self.assertGreaterEqual(
            len(discovered_from), min_expected,
            f"Expected >= {min_expected} discovered_from entries "
            f"({len(configured)} strategies configured); "
            f"strategies may not be reporting source files.",
        )

# ---------------------------------------------------------------------------
# Synthetic-fixture suites — always run, regardless of host environment
# ---------------------------------------------------------------------------


class SyntheticStrategyPresenceTest(SyntheticGraphMixin, unittest.TestCase):
    """Synthetic counterpart of ``StrategyPresenceTest`` (always runs)."""

    SYNTH_PREFIX = "weld-strategy-presence-"

    def test_each_configured_strategy_produces_nodes(self) -> None:
        strategy_counts = Counter(
            n.get("props", {}).get("source_strategy", "unknown")
            for n in self.graph["nodes"].values()
        )
        for strat in sorted(SYNTH_STRATEGIES):
            with self.subTest(strategy=strat):
                self.assertGreater(
                    strategy_counts.get(strat, 0), 0,
                    f"Synthetic fixture configures strategy '{strat}' "
                    f"but it produced 0 nodes; glob/strategy may be broken.",
                )


class SyntheticEdgeIntegrityTest(SyntheticGraphMixin, unittest.TestCase):
    """Synthetic counterpart of ``EdgeIntegrityTest`` (always runs)."""

    SYNTH_PREFIX = "weld-edge-integrity-"

    def test_no_dangling_edge_references(self) -> None:
        node_ids = set(self.graph["nodes"].keys())
        dangling: list[str] = []
        for edge in self.graph["edges"]:
            if edge["from"] not in node_ids:
                dangling.append(f"from={edge['from']}")
            if edge["to"] not in node_ids:
                dangling.append(f"to={edge['to']}")
        self.assertEqual(
            dangling, [],
            f"Synthetic graph has {len(dangling)} dangling edge refs: "
            f"{dangling[:5]}",
        )

    def test_edges_field_is_a_list(self) -> None:
        # The synthetic fixture is too small to guarantee any specific
        # edge count, but the discovery contract guarantees ``edges``
        # is always a list (even when empty).
        self.assertIsInstance(
            self.graph["edges"], list,
            "graph['edges'] must always be a list (discovery contract).",
        )


class SyntheticGraphMetaTest(SyntheticGraphMixin, unittest.TestCase):
    """Synthetic counterpart of ``GraphMetaTest`` (always runs)."""

    SYNTH_PREFIX = "weld-graph-meta-"

    def test_meta_version(self) -> None:
        from weld.contract import SCHEMA_VERSION
        self.assertEqual(self.graph["meta"]["version"], SCHEMA_VERSION)

    def test_meta_has_updated_at(self) -> None:
        self.assertIn("updated_at", self.graph["meta"])

    def test_discovered_from_is_populated(self) -> None:
        # The synthetic fixture configures two strategies, both with at
        # least one matching file, so ``discovered_from`` must contain
        # at least one entry.
        discovered_from = self.graph["meta"].get("discovered_from", [])
        self.assertGreaterEqual(
            len(discovered_from), 1,
            f"Synthetic fixture should have >= 1 discovered_from entries; "
            f"got {len(discovered_from)}.",
        )


if __name__ == "__main__":
    unittest.main()
