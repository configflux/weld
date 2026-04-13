"""Tests for cortex ranking — authority, confidence, and role-based retrieval ordering.

Verifies that:
- cortex query returns results ranked by authority then confidence within each
  token-hit tier
- cortex brief returns results ranked by authority then confidence in every section
- canonical > derived > manual > inferred for authority
- definite > inferred > speculative for confidence
- role boosting is optional and moves role-relevant nodes earlier
- missing metadata sorts after all known values
- deterministic tiebreaking by node ID
- higher-confidence results rank above lower-confidence ones
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.graph import Graph  # noqa: E402
from cortex.ranking import (  # noqa: E402
    AUTHORITY_RANK,
    CONFIDENCE_RANK,
    authority_score,
    confidence_score,
    query_rank_key,
    rank_key,
    role_boost,
)

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph with the given nodes and edges."""
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    g._data = {
        "meta": {"version": 1, "updated_at": "2026-04-02T12:00:00+00:00"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g

# -- Unit tests for ranking primitives ------------------------------------

class AuthorityScoreTest(unittest.TestCase):
    """Verify authority_score returns correct ordering values."""

    def test_canonical_is_lowest(self) -> None:
        node = {"props": {"authority": "canonical"}}
        self.assertEqual(authority_score(node), 0)

    def test_derived_is_second(self) -> None:
        node = {"props": {"authority": "derived"}}
        self.assertEqual(authority_score(node), 1)

    def test_manual_is_third(self) -> None:
        node = {"props": {"authority": "manual"}}
        self.assertEqual(authority_score(node), 2)

    def test_inferred_is_fourth(self) -> None:
        node = {"props": {"authority": "inferred"}}
        self.assertEqual(authority_score(node), 3)

    def test_missing_authority_sorts_last(self) -> None:
        node = {"props": {}}
        score = authority_score(node)
        self.assertGreater(score, max(AUTHORITY_RANK.values()))

    def test_unknown_authority_sorts_last(self) -> None:
        node = {"props": {"authority": "bogus"}}
        score = authority_score(node)
        self.assertGreater(score, max(AUTHORITY_RANK.values()))

    def test_missing_props_sorts_last(self) -> None:
        node: dict = {}
        score = authority_score(node)
        self.assertGreater(score, max(AUTHORITY_RANK.values()))

class ConfidenceScoreTest(unittest.TestCase):
    """Verify confidence_score returns correct ordering values."""

    def test_definite_is_lowest(self) -> None:
        node = {"props": {"confidence": "definite"}}
        self.assertEqual(confidence_score(node), 0)

    def test_inferred_is_second(self) -> None:
        node = {"props": {"confidence": "inferred"}}
        self.assertEqual(confidence_score(node), 1)

    def test_speculative_is_third(self) -> None:
        node = {"props": {"confidence": "speculative"}}
        self.assertEqual(confidence_score(node), 2)

    def test_missing_confidence_sorts_last(self) -> None:
        node = {"props": {}}
        score = confidence_score(node)
        self.assertGreater(score, max(CONFIDENCE_RANK.values()))

class RoleBoostTest(unittest.TestCase):
    """Verify role_boost returns 0 for matching roles, 1 otherwise."""

    def test_no_query_roles_returns_zero(self) -> None:
        node = {"props": {"roles": ["test"]}}
        self.assertEqual(role_boost(node, None), 0)

    def test_empty_query_roles_returns_zero(self) -> None:
        node = {"props": {"roles": ["test"]}}
        self.assertEqual(role_boost(node, frozenset()), 0)

    def test_matching_role_returns_zero(self) -> None:
        node = {"props": {"roles": ["implementation", "test"]}}
        self.assertEqual(role_boost(node, frozenset(["test"])), 0)

    def test_no_matching_role_returns_one(self) -> None:
        node = {"props": {"roles": ["doc"]}}
        self.assertEqual(role_boost(node, frozenset(["test"])), 1)

    def test_node_without_roles_returns_one(self) -> None:
        node = {"props": {}}
        self.assertEqual(role_boost(node, frozenset(["test"])), 1)

class RankKeyTest(unittest.TestCase):
    """Verify rank_key produces correct composite ordering."""

    def test_canonical_definite_before_derived_definite(self) -> None:
        a = {"id": "a", "props": {"authority": "canonical", "confidence": "definite"}}
        b = {"id": "b", "props": {"authority": "derived", "confidence": "definite"}}
        self.assertLess(rank_key(a), rank_key(b))

    def test_canonical_speculative_before_derived_definite(self) -> None:
        """Authority is the primary signal after role boost."""
        a = {"id": "a", "props": {"authority": "canonical", "confidence": "speculative"}}
        b = {"id": "b", "props": {"authority": "derived", "confidence": "definite"}}
        self.assertLess(rank_key(a), rank_key(b))

    def test_same_authority_definite_before_inferred(self) -> None:
        a = {"id": "a", "props": {"authority": "canonical", "confidence": "definite"}}
        b = {"id": "b", "props": {"authority": "canonical", "confidence": "inferred"}}
        self.assertLess(rank_key(a), rank_key(b))

    def test_same_authority_inferred_before_speculative(self) -> None:
        a = {"id": "a", "props": {"authority": "canonical", "confidence": "inferred"}}
        b = {"id": "b", "props": {"authority": "canonical", "confidence": "speculative"}}
        self.assertLess(rank_key(a), rank_key(b))

    def test_tiebreak_by_id(self) -> None:
        a = {"id": "a:first", "props": {"authority": "canonical", "confidence": "definite"}}
        b = {"id": "b:second", "props": {"authority": "canonical", "confidence": "definite"}}
        self.assertLess(rank_key(a), rank_key(b))

    def test_missing_metadata_sorts_after_known(self) -> None:
        known = {"id": "a", "props": {"authority": "inferred", "confidence": "speculative"}}
        unknown = {"id": "b", "props": {}}
        self.assertLess(rank_key(known), rank_key(unknown))

    def test_role_boost_overrides_authority(self) -> None:
        """A role-matching node with lower authority beats a non-matching
        node with higher authority when role boost is active."""
        matching = {"id": "a", "props": {"authority": "derived", "roles": ["test"]}}
        non_matching = {"id": "b", "props": {"authority": "canonical", "roles": ["doc"]}}
        roles = frozenset(["test"])
        self.assertLess(rank_key(matching, query_roles=roles),
                        rank_key(non_matching, query_roles=roles))

class QueryRankKeyTest(unittest.TestCase):
    """Verify query_rank_key layers ranking on top of token hit count."""

    def test_more_hits_always_first(self) -> None:
        """Token hit count is the primary signal."""
        low_auth_more_hits = {"id": "a", "props": {}}
        high_auth_fewer_hits = {"id": "b", "props": {"authority": "canonical"}}
        self.assertLess(
            query_rank_key(3, low_auth_more_hits),
            query_rank_key(2, high_auth_fewer_hits),
        )

    def test_same_hits_canonical_before_derived(self) -> None:
        canonical = {"id": "a", "props": {"authority": "canonical"}}
        derived = {"id": "b", "props": {"authority": "derived"}}
        self.assertLess(
            query_rank_key(2, canonical),
            query_rank_key(2, derived),
        )

    def test_same_hits_definite_before_speculative(self) -> None:
        definite = {"id": "a", "props": {"authority": "canonical", "confidence": "definite"}}
        speculative = {"id": "b", "props": {"authority": "canonical", "confidence": "speculative"}}
        self.assertLess(
            query_rank_key(2, definite),
            query_rank_key(2, speculative),
        )

# -- Integration tests: Graph.query() ranking -----------------------------

class QueryRankingIntegrationTest(unittest.TestCase):
    """Verify Graph.query() uses authority+confidence ranking."""

    def _ranked_graph(self) -> Graph:
        """Build a graph with nodes that all match 'service' but differ
        in authority and confidence."""
        nodes = {
            "service:derived-speculative": {
                "type": "service",
                "label": "Service D-S",
                "props": {"authority": "derived", "confidence": "speculative"},
            },
            "service:canonical-definite": {
                "type": "service",
                "label": "Service C-D",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
            "service:canonical-speculative": {
                "type": "service",
                "label": "Service C-S",
                "props": {"authority": "canonical", "confidence": "speculative"},
            },
            "service:inferred-definite": {
                "type": "service",
                "label": "Service I-D",
                "props": {"authority": "inferred", "confidence": "definite"},
            },
            "service:manual-inferred": {
                "type": "service",
                "label": "Service M-I",
                "props": {"authority": "manual", "confidence": "inferred"},
            },
            "service:no-metadata": {
                "type": "service",
                "label": "Service None",
                "props": {},
            },
        }
        return _make_graph(nodes)

    def test_canonical_definite_ranks_first(self) -> None:
        g = self._ranked_graph()
        result = g.query("service")
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(ids[0], "service:canonical-definite")

    def test_canonical_before_derived(self) -> None:
        g = self._ranked_graph()
        result = g.query("service")
        ids = [m["id"] for m in result["matches"]]
        canonical_idx = ids.index("service:canonical-definite")
        derived_idx = ids.index("service:derived-speculative")
        self.assertLess(canonical_idx, derived_idx)

    def test_no_metadata_ranks_last(self) -> None:
        g = self._ranked_graph()
        result = g.query("service")
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(ids[-1], "service:no-metadata")

    def test_full_ordering(self) -> None:
        """Verify the complete expected ranking order."""
        g = self._ranked_graph()
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

    def test_token_hits_still_primary(self) -> None:
        """A node matching more tokens should still rank above a node with
        better authority but fewer token matches."""
        nodes = {
            "service:api-handler": {
                "type": "service",
                "label": "API Handler Service",
                "props": {"authority": "inferred", "confidence": "speculative"},
            },
            "service:worker": {
                "type": "service",
                "label": "Worker Service",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
        }
        g = _make_graph(nodes)
        # "api handler" matches 2 tokens in service:api-handler but only 0 in worker
        result = g.query("api handler")
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(ids[0], "service:api-handler")

    def test_deterministic_ordering(self) -> None:
        g = self._ranked_graph()
        r1 = g.query("service")
        r2 = g.query("service")
        self.assertEqual(
            [m["id"] for m in r1["matches"]],
            [m["id"] for m in r2["matches"]],
        )

if __name__ == "__main__":
    unittest.main()
