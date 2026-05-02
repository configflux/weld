"""Acceptance tests for ``Graph.query()`` OR-fallback behavior.

Bug repro from `weld dogfood gap: wd query returns empty for multi-word
phrases`. Before this fix, ``wd query "discovery strategy"`` returned an
empty envelope because the strict-AND path required every token group to
intersect a single node. After the fix, ``Graph.query()`` mirrors the
``brief()`` behavior: when strict-AND yields nothing on a multi-token
query, retry via :func:`weld.graph_query.query_or_fallback` (per-group
union ranked by group-hit count then BM25) and tag the envelope with
``degraded_match: 'or_fallback'`` so consumers know the result was
relaxed.

Single-token queries skip the fallback (OR == AND for one group).
Strict-AND wins are not relaxed — when AND succeeds the fallback is not
invoked and ``degraded_match`` is absent.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402

_TS = "2026-05-02T12:00:00+00:00"


def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph instance pre-loaded with given data."""
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    g._data = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "or_q1"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g


def _two_disjoint_nodes_graph() -> Graph:
    """Graph where 'discovery' and 'strategy' each hit a different node."""
    nodes = {
        # Has 'discovery' but not 'strategy'.
        "module:discovery": {
            "type": "module", "label": "discovery",
            "props": {"file": "weld/discovery.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        # Has 'strategy' but not 'discovery'.
        "module:strategy": {
            "type": "module", "label": "strategy",
            "props": {"file": "weld/strategy.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        # Distractor: matches neither.
        "module:unrelated": {
            "type": "module", "label": "unrelated",
            "props": {"file": "weld/unrelated.py"},
        },
    }
    return _make_graph(nodes)


def _and_succeeds_graph() -> Graph:
    """Graph where strict-AND succeeds for 'api service'."""
    nodes = {
        "service:api": {
            "type": "service", "label": "api service",
            "props": {"file": "services/api/main.py"},
        },
        "service:other": {
            "type": "service", "label": "other",
            "props": {"file": "services/other.py"},
        },
    }
    return _make_graph(nodes)


def _co_occurrence_graph() -> Graph:
    """Graph where one node has both tokens and others have only one."""
    nodes = {
        # Both 'discovery' and 'strategy' co-occur.
        "module:discovery-strategy": {
            "type": "module", "label": "discovery strategy",
            "props": {"file": "weld/strategies/discovery.py"},
        },
        # Only 'discovery'.
        "module:discovery-only": {
            "type": "module", "label": "discovery",
            "props": {"file": "weld/discovery_helper.py"},
        },
        # Only 'strategy'.
        "module:strategy-only": {
            "type": "module", "label": "strategy",
            "props": {"file": "weld/strategy_helper.py"},
        },
    }
    return _make_graph(nodes)


class GraphQueryOrFallbackTest(unittest.TestCase):
    """Issue acceptance criteria: graph.query() must use OR fallback."""

    def test_multi_word_disjoint_tokens_returns_union(self) -> None:
        """Both tokens individually match different nodes -> non-empty union.

        This is the headline issue repro: ``wd query 'discovery strategy'``
        with a graph where no single node has both tokens but each token has
        matches on its own. Strict-AND was returning empty; OR fallback now
        surfaces both nodes and tags the envelope with ``degraded_match``.
        """
        g = _two_disjoint_nodes_graph()
        result = g.query("discovery strategy", limit=5)
        ids = {m["id"] for m in result["matches"]}
        self.assertIn("module:discovery", ids)
        self.assertIn("module:strategy", ids)
        # Distractor must NOT appear.
        self.assertNotIn("module:unrelated", ids)
        self.assertEqual(result.get("degraded_match"), "or_fallback")

    def test_multi_word_one_token_zero_matches_returns_other(self) -> None:
        """One token has zero matches, other has matches -> non-empty result.

        When ``zzznonexistent`` has no candidates but ``discovery`` does, the
        OR fallback returns the discovery match instead of the empty envelope
        the strict-AND path would have produced.
        """
        g = _two_disjoint_nodes_graph()
        result = g.query("discovery zzznonexistent", limit=5)
        ids = {m["id"] for m in result["matches"]}
        self.assertIn("module:discovery", ids)
        self.assertEqual(result.get("degraded_match"), "or_fallback")

    def test_single_token_query_unchanged(self) -> None:
        """Single-token queries skip the fallback and behave as before."""
        g = _two_disjoint_nodes_graph()
        # Match present.
        result = g.query("discovery", limit=5)
        ids = {m["id"] for m in result["matches"]}
        self.assertIn("module:discovery", ids)
        # Single-token wins are strict-AND wins; no degraded flag.
        self.assertNotIn("degraded_match", result)
        # No match: still empty, no fallback (would be identical anyway).
        empty = g.query("zzznonexistent", limit=5)
        self.assertEqual(empty["matches"], [])
        self.assertNotIn("degraded_match", empty)

    def test_co_occurrence_ranks_above_single_token_only(self) -> None:
        """A node hitting both tokens ranks above single-token-only matches.

        Strict-AND succeeds on the co-occurrence node, so the fallback is not
        triggered and ``degraded_match`` is absent. The single-token-only
        nodes should NOT appear (strict-AND filters them out).
        """
        g = _co_occurrence_graph()
        result = g.query("discovery strategy", limit=5)
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("module:discovery-strategy", ids)
        # Strict-AND won, so no degraded flag.
        self.assertNotIn("degraded_match", result)
        # Co-occurrence node must be first.
        self.assertEqual(ids[0], "module:discovery-strategy")
        # Single-token-only nodes are filtered out by strict-AND.
        self.assertNotIn("module:discovery-only", ids)
        self.assertNotIn("module:strategy-only", ids)

    def test_and_succeeds_no_fallback_flag(self) -> None:
        """When strict-AND finds matches, the fallback is not used."""
        g = _and_succeeds_graph()
        result = g.query("api service", limit=5)
        self.assertGreater(len(result["matches"]), 0)
        self.assertNotIn("degraded_match", result)

    def test_both_zero_returns_empty_envelope(self) -> None:
        """When AND and OR both find nothing, envelope is empty (no flag)."""
        g = _two_disjoint_nodes_graph()
        result = g.query("zzznonexistent xyznonexistent", limit=5)
        self.assertEqual(result["matches"], [])
        # No degraded flag because the OR path also failed.
        self.assertNotIn("degraded_match", result)

    def test_envelope_shape_preserved(self) -> None:
        """Fallback path returns the same envelope keys as the AND path."""
        g = _two_disjoint_nodes_graph()
        result = g.query("discovery strategy", limit=5)
        for key in ("query", "matches", "neighbors", "edges"):
            self.assertIn(key, result)
        self.assertEqual(result["query"], "discovery strategy")

    def test_limit_respected_on_fallback(self) -> None:
        """``limit`` is honored when the fallback is used."""
        g = _two_disjoint_nodes_graph()
        result = g.query("discovery strategy", limit=1)
        self.assertLessEqual(len(result["matches"]), 1)

    def test_three_token_mixed_one_match_two_zero(self) -> None:
        """3-token query with only one token matching -> returns that token's nodes.

        Regression guard for the dogfood-gap report: a multi-token query
        where most tokens have zero inverted-index hits must still surface
        the matches for whichever token(s) DO hit. The OR-fallback predicate
        must be 'fall back when strict-AND is empty AND at least one token
        has matches', not 'fall back only when every token has matches'.
        """
        g = _two_disjoint_nodes_graph()
        result = g.query(
            "discovery zzznonexistent xyznonexistent", limit=5
        )
        ids = {m["id"] for m in result["matches"]}
        self.assertIn("module:discovery", ids)
        self.assertNotIn("module:strategy", ids)
        self.assertNotIn("module:unrelated", ids)
        self.assertEqual(result.get("degraded_match"), "or_fallback")

    def test_three_token_all_zero_returns_empty(self) -> None:
        """3-token query with no matching tokens -> empty envelope, no flag."""
        g = _two_disjoint_nodes_graph()
        result = g.query(
            "zzznonexistent xyznonexistent qqqnonexistent", limit=5
        )
        self.assertEqual(result["matches"], [])
        self.assertNotIn("degraded_match", result)

    def test_repro_one_token_zero_other_nonzero(self) -> None:
        """Direct repro of the reported gap pattern.

        Mirrors the user-visible scenario: a multi-word query where one
        token has plenty of single-token matches and another token has
        zero. Pre-fix this returned an empty envelope. Post-fix it
        returns the matching token's nodes with ``degraded_match`` set.
        """
        nodes = {
            "file:graph_communities_render": {
                "type": "file", "label": "graph_communities_render",
                "props": {"file": "weld/graph_communities_render.py"},
            },
            "file:graph_communities_cli": {
                "type": "file", "label": "graph_communities_cli",
                "props": {"file": "weld/graph_communities_cli.py"},
            },
            "file:graph": {
                "type": "file", "label": "graph",
                "props": {"file": "weld/graph.py"},
            },
        }
        g = _make_graph(nodes)
        # 'communities' hits the two graph_communities_* files via substring;
        # 'zzznonexistenttoken' hits nothing.
        result = g.query("communities zzznonexistenttoken", limit=10)
        ids = {m["id"] for m in result["matches"]}
        self.assertIn("file:graph_communities_render", ids)
        self.assertIn("file:graph_communities_cli", ids)
        self.assertNotIn("file:graph", ids)
        self.assertEqual(result.get("degraded_match"), "or_fallback")


if __name__ == "__main__":
    unittest.main()
