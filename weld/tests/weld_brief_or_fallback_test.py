"""Acceptance tests for wd brief OR-fallback behavior (Bug-3).

When a strict-AND multi-token query yields no matches, brief() retries via
``query_or_fallback`` (per-group union ranked by group-hit count and BM25)
and tags the result with ``degraded_match: 'or_fallback'`` so consumers see
they did not receive strict-AND results.

These tests live in their own module so the original
``weld_brief_acceptance_test`` stays under the line-count cap.

"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.brief import brief  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.graph_query import query_or_fallback  # noqa: E402

_TS = "2026-04-02T12:00:00+00:00"


def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph instance pre-loaded with given data."""
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    g._data = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "or123"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g


def _make_or_fallback_graph() -> Graph:
    """Build a graph where 'discover' and 'federation' each hit different nodes
    so strict-AND yields nothing but a per-group union finds both nodes."""
    nodes = {
        # Has 'discover' but not 'federation'.
        "module:discover": {
            "type": "module", "label": "discover",
            "props": {"file": "weld/discover.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        # Has 'federation' but not 'discover'.
        "module:federation": {
            "type": "module", "label": "federation",
            "props": {"file": "weld/federation.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        # Distractor: matches neither.
        "module:unrelated": {
            "type": "module", "label": "unrelated",
            "props": {"file": "weld/unrelated.py",
                      "authority": "canonical", "confidence": "definite"},
        },
    }
    return _make_graph(nodes)


def _make_realistic_graph_for_and() -> Graph:
    """Minimal graph where 'api service' AND-succeeds (no fallback expected)."""
    nodes = {
        "service:api": {
            "type": "service", "label": "api service",
            "props": {"file": "services/api/main.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        "service:other": {
            "type": "service", "label": "other",
            "props": {"file": "services/other.py"},
        },
    }
    return _make_graph(nodes)


class BriefOrFallbackAcceptanceTest(unittest.TestCase):
    """Bug-3: brief retries with OR semantics when strict-AND yields nothing
    on a multi-token query, and tags the result with degraded_match.
    """

    def test_and_succeeds_no_fallback_flag(self) -> None:
        """When strict-AND returns matches, no degraded_match flag is set."""
        g = _make_realistic_graph_for_and()
        result = brief(g, "api service")
        self.assertGreater(len(result["primary"]), 0)
        self.assertNotIn("degraded_match", result)

    def test_and_zero_or_finds_matches_sets_flag(self) -> None:
        """When AND is empty but >1 token, OR fallback fires and flag is set."""
        g = _make_or_fallback_graph()
        # Strict-AND would zero: no node has BOTH 'discover' and 'federation'.
        result = brief(g, "discover federation")
        # OR fallback should find both.
        primary_ids = {n["id"] for n in result["primary"]}
        self.assertIn("module:discover", primary_ids)
        self.assertIn("module:federation", primary_ids)
        # Flag is set so callers know they didn't get strict AND.
        self.assertEqual(result.get("degraded_match"), "or_fallback")

    def test_or_fallback_warning_is_clear(self) -> None:
        """OR fallback emits a warning explaining the relaxation."""
        g = _make_or_fallback_graph()
        result = brief(g, "discover federation")
        joined = " ".join(result["warnings"]).lower()
        self.assertTrue(
            "or" in joined or "fallback" in joined or "relaxed" in joined,
            f"Expected OR-fallback warning, got: {result['warnings']}",
        )

    def test_both_zero_empty_with_warning(self) -> None:
        """When AND and OR both find nothing, result is empty with clear warning."""
        g = _make_or_fallback_graph()
        result = brief(g, "zzznonexistent xyznonexistent")
        self.assertEqual(len(result["primary"]), 0)
        self.assertEqual(len(result["interfaces"]), 0)
        self.assertEqual(len(result["docs"]), 0)
        self.assertEqual(len(result["build"]), 0)
        self.assertEqual(len(result["boundaries"]), 0)
        # Warning surfaces.
        self.assertGreater(len(result["warnings"]), 0)
        joined = " ".join(result["warnings"]).lower()
        self.assertIn("no matches", joined)
        # No degraded flag because OR also failed -- the result is honestly empty.
        self.assertNotIn("degraded_match", result)

    def test_single_token_no_fallback_attempted(self) -> None:
        """Single-token query that AND-zeroes: no fallback (would be identical)."""
        g = _make_or_fallback_graph()
        result = brief(g, "zzznonexistent")
        # No matches, no degraded flag (single-token AND == single-token OR).
        self.assertEqual(len(result["primary"]), 0)
        self.assertNotIn("degraded_match", result)

    def test_or_fallback_ranks_more_groups_first(self) -> None:
        """A node hitting more token groups ranks before a node hitting fewer.

        Tests query_or_fallback directly so we can exercise the multi-group
        ranking path even on graphs where AND would also succeed.
        """
        nodes = {
            "module:alpha-beta": {
                "type": "module", "label": "alpha-beta",
                "props": {"file": "weld/alpha_beta.py",
                          "authority": "canonical", "confidence": "definite"},
            },
            "module:alpha-only": {
                "type": "module", "label": "alpha-only",
                "props": {"file": "weld/alpha_only.py",
                          "authority": "canonical", "confidence": "definite"},
            },
            "module:beta-only": {
                "type": "module", "label": "beta-only",
                "props": {"file": "weld/beta_only.py",
                          "authority": "canonical", "confidence": "definite"},
            },
        }
        g = _make_graph(nodes)
        result = query_or_fallback(g, "alpha beta", limit=10)
        ids = [m["id"] for m in result["matches"]]
        # alpha-beta hits 2 groups, alpha-only and beta-only hit 1 each.
        self.assertEqual(ids[0], "module:alpha-beta",
                         f"alpha-beta should rank first, got {ids}")
        self.assertEqual(set(ids[1:]),
                         {"module:alpha-only", "module:beta-only"})

    def test_or_fallback_helper_returns_query_envelope(self) -> None:
        """query_or_fallback returns the same envelope shape as query_graph."""
        g = _make_or_fallback_graph()
        result = query_or_fallback(g, "discover federation", limit=10)
        self.assertIn("query", result)
        self.assertIn("matches", result)
        self.assertIn("neighbors", result)
        self.assertIn("edges", result)
        self.assertEqual(result["query"], "discover federation")

    def test_or_fallback_empty_term(self) -> None:
        """Empty / whitespace term returns the empty envelope without crashing."""
        g = _make_or_fallback_graph()
        for term in ("", "   "):
            result = query_or_fallback(g, term, limit=10)
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["neighbors"], [])
            self.assertEqual(result["edges"], [])


if __name__ == "__main__":
    unittest.main()
