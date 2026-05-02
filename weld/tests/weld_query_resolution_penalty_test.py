"""Acceptance tests for the wd query resolution-penalty refinement.

Bug repro: ``wd query 'enrichment'`` ranked
``symbol:unresolved:_has_enrichment`` (confidence: speculative,
unresolved sentinel) above
``symbol:py:weld.embeddings:enrichment_description`` (confidence:
definite). The fix penalizes nodes that are speculative and/or unresolved
sentinels in the ``rank_query_matches`` sort key so definite resolved
peers surface first while preserving the existing OR-fallback contract
(commit fd256b1).

Tests live in a dedicated module to keep ``weld_ranking_test.py`` under
the 400-line cap.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph import Graph  # noqa: E402
from weld.ranking import resolution_penalty  # noqa: E402


def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph with the given nodes and edges."""
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    g._data = {
        "meta": {"version": 1, "updated_at": "2026-05-02T12:00:00+00:00"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g


class ResolutionPenaltyUnitTest(unittest.TestCase):
    """Verify ``resolution_penalty`` flags speculative/unresolved nodes."""

    def test_definite_resolved_node_has_zero_penalty(self) -> None:
        node = {
            "id": "symbol:py:weld.embeddings:enrichment_description",
            "props": {"confidence": "definite"},
        }
        self.assertEqual(resolution_penalty(node), 0)

    def test_unresolved_sentinel_id_is_penalized(self) -> None:
        """The ``symbol:unresolved:`` ID prefix marks an unresolved sentinel."""
        node = {
            "id": "symbol:unresolved:_has_enrichment",
            "props": {"confidence": "speculative"},
        }
        self.assertGreater(resolution_penalty(node), 0)

    def test_explicit_unresolved_resolution_prop_is_penalized(self) -> None:
        node = {
            "id": "symbol:foo:bar",
            "props": {"resolution": "unresolved", "confidence": "definite"},
        }
        self.assertGreater(resolution_penalty(node), 0)

    def test_speculative_resolved_node_is_not_penalized(self) -> None:
        """Speculative confidence alone does NOT trigger the penalty.

        The penalty targets unresolved-sentinel noise, not the broader
        speculative-confidence class. Speculative resolved peers still
        rank by the existing authority > confidence > id tiebreakers.
        """
        node = {
            "id": "symbol:py:weld.foo:bar",
            "props": {"confidence": "speculative"},
        }
        self.assertEqual(resolution_penalty(node), 0)

    def test_inferred_confidence_is_not_penalized(self) -> None:
        """Inferred is between definite and speculative; do not demote it."""
        node = {
            "id": "symbol:py:weld.foo:bar",
            "props": {"confidence": "inferred"},
        }
        self.assertEqual(resolution_penalty(node), 0)

    def test_node_without_props_is_not_penalized(self) -> None:
        """A node missing confidence/resolution metadata is treated as neutral."""
        node = {"id": "symbol:py:weld.foo:bar", "props": {}}
        self.assertEqual(resolution_penalty(node), 0)


class QueryResolutionPenaltyIntegrationTest(unittest.TestCase):
    """End-to-end through ``Graph.query()``: definite-resolved beats sentinel."""

    def test_definite_resolved_outranks_unresolved_sentinel(self) -> None:
        """Bug repro for ``wd query 'enrichment'``.

        Both nodes share the ``enrichment`` token. Before the fix, the
        sentinel ranked first because its label is short and BM25 saw it
        as a denser term match. After the fix, the resolution penalty
        pushes the sentinel below the definite resolved symbol regardless
        of BM25 weight.
        """
        nodes = {
            "symbol:unresolved:_has_enrichment": {
                "type": "symbol",
                "label": "_has_enrichment",
                "props": {
                    "authority": "derived",
                    "confidence": "speculative",
                },
            },
            "symbol:py:weld.embeddings:enrichment_description": {
                "type": "symbol",
                "label": "enrichment_description",
                "props": {
                    "authority": "derived",
                    "confidence": "definite",
                    "file": "weld/embeddings.py",
                },
            },
        }
        g = _make_graph(nodes)
        result = g.query("enrichment")
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(
            ids[0],
            "symbol:py:weld.embeddings:enrichment_description",
            "definite resolved symbol must rank above unresolved sentinel",
        )
        # Penalty demotes; it does not filter -- the sentinel still appears.
        self.assertIn("symbol:unresolved:_has_enrichment", ids)

    def test_definite_outranks_speculative_with_same_authority(self) -> None:
        """Same token overlap, same authority -> definite wins."""
        nodes = {
            "symbol:py:foo:speculative_match": {
                "type": "symbol",
                "label": "speculative_enrichment",
                "props": {
                    "authority": "derived",
                    "confidence": "speculative",
                },
            },
            "symbol:py:foo:definite_match": {
                "type": "symbol",
                "label": "definite_enrichment",
                "props": {
                    "authority": "derived",
                    "confidence": "definite",
                },
            },
        }
        g = _make_graph(nodes)
        result = g.query("enrichment")
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(ids[0], "symbol:py:foo:definite_match")

    def test_single_token_existing_full_ordering_unchanged(self) -> None:
        """Regression guard: existing single-token rank order still holds.

        This mirrors ``QueryRankingIntegrationTest.test_full_ordering`` so a
        future change to the ranker that breaks it would fail here too.
        """
        nodes = {
            "service:derived-speculative": {
                "type": "service", "label": "Service D-S",
                "props": {"authority": "derived", "confidence": "speculative"},
            },
            "service:canonical-definite": {
                "type": "service", "label": "Service C-D",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
            "service:canonical-speculative": {
                "type": "service", "label": "Service C-S",
                "props": {"authority": "canonical", "confidence": "speculative"},
            },
            "service:inferred-definite": {
                "type": "service", "label": "Service I-D",
                "props": {"authority": "inferred", "confidence": "definite"},
            },
            "service:manual-inferred": {
                "type": "service", "label": "Service M-I",
                "props": {"authority": "manual", "confidence": "inferred"},
            },
            "service:no-metadata": {
                "type": "service", "label": "Service None", "props": {},
            },
        }
        g = _make_graph(nodes)
        result = g.query("service")
        ids = [m["id"] for m in result["matches"]]
        expected = [
            "service:canonical-definite",
            "service:canonical-speculative",
            "service:derived-speculative",
            "service:manual-inferred",
            "service:inferred-definite",
            "service:no-metadata",
        ]
        self.assertEqual(ids, expected)


class MultiWordOrFallbackRegressionTest(unittest.TestCase):
    """Regression guard for multi-word OR-fallback behavior (commit fd256b1).

    The resolution-penalty refinement must not break the OR fallback:
    when no node has all tokens but each token matches some node, the
    union still surfaces with ``degraded_match: 'or_fallback'``.
    """

    def test_or_fallback_still_triggers_for_multi_word(self) -> None:
        nodes = {
            "module:discovery": {
                "type": "module", "label": "discovery",
                "props": {"file": "weld/discovery.py",
                          "authority": "canonical", "confidence": "definite"},
            },
            "module:strategy": {
                "type": "module", "label": "strategy",
                "props": {"file": "weld/strategy.py",
                          "authority": "canonical", "confidence": "definite"},
            },
        }
        g = _make_graph(nodes)
        result = g.query("discovery strategy", limit=5)
        ids = {m["id"] for m in result["matches"]}
        self.assertIn("module:discovery", ids)
        self.assertIn("module:strategy", ids)
        self.assertEqual(result.get("degraded_match"), "or_fallback")


if __name__ == "__main__":
    unittest.main()
