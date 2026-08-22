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

import unittest
from collections import Counter
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)

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
        # incremental=False forces a full, independent re-walk of the live
        # repo's current source on every run, regardless of .weld/graph.json's
        # freshness or vouch status (weld/_discover_inputs.py's plan_delta
        # short-circuits to a full pass whenever incremental is explicitly
        # False) -- this suite exercises real strategies against real
        # conditions, not a cached graph, so it must never trust stale state.
        # with_sqlite=False: this test only ever reads the returned dict, so
        # the .weld/graph.db sidecar discover() would otherwise write
        # unconditionally (tens of MB on this repo) is pure waste here, and
        # dropping it shrinks this call's footprint on the shared, live
        # .weld/ directory a concurrently-running sibling test also touches
        # (bd 70he: weld_artifact_class_regression_test does the same full
        # discover() against this same root; the actual concurrency bug that
        # could ERROR this suite was a fixed-name temp file race in
        # weld.discovery_state.save_state, fixed at the source).
        _GRAPH = discover(Path(_repo_root), incremental=False, with_sqlite=False)
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

class _DiscoverYamlGated(unittest.TestCase):
    """Base class for host-repo suites that require ``.weld/discover.yaml``.

    The class-level ``_HAS_DISCOVER_YAML`` flag is evaluated at module
    import time. That captures the standalone-vs-configured distinction
    most of the time, but other tests in the same ``bazel test //...``
    invocation -- notably ``weld_discover_test.sh`` -- transiently
    create and delete the workspace's ``.weld/discover.yaml`` to
    bootstrap their own discover() runs. If this module is imported
    during that brief window, the import-time flag is True but the
    file is gone (or freshly truncated) by the time the test methods
    execute, producing a spurious ``configured strategies == 0``
    failure rather than a clean skip. Re-check at setUp time so the
    runtime state -- not the import-time snapshot -- is what gates
    the suite.
    """

    def setUp(self) -> None:
        super().setUp()
        config = Path(_repo_root) / ".weld" / "discover.yaml"
        if not config.is_file():
            self.skipTest(_STANDALONE_SKIP)
        if not _configured_strategies():
            self.skipTest(_STANDALONE_SKIP)


@unittest.skipUnless(_HAS_DISCOVER_YAML, _STANDALONE_SKIP)
class StrategyPresenceTest(_DiscoverYamlGated):
    """Every configured strategy should produce at least one node."""

    def test_each_configured_strategy_produces_nodes(self) -> None:
        g = _graph()
        strategy_counts = Counter(
            n.get("props", {}).get("source_strategy", "unknown")
            for n in g["nodes"].values()
        )
        configured = _configured_strategies()
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
class EdgeIntegrityTest(_DiscoverYamlGated):
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
class GraphMetaTest(_DiscoverYamlGated):
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
