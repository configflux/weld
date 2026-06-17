"""Regression guard for the ``typescript discovery strategy`` dogfood gap (jucl).

bd jucl reported that
``wd query "typescript discovery strategy"`` surfaced ``weld/discovery_state``
and its symbols ABOVE the actual TypeScript strategy modules
(``weld/strategies/typescript_exports`` / ``_typescript_tree_sitter``), even
though those ``discovery_state`` nodes contain no ``typescript`` token at all.

Root cause (verified against the live graph at fix time): the colliding nodes
tie on covered-group count -- ``typescript_exports`` covers
``{typescript, strategy}`` and ``discovery_state`` covers
``{discovery, strategy}``, both 2 of the 3 query groups. The pre-existing
tie-break was pure BM25 ``-score`` and ``discovery`` happens to be a *rarer*
token in this corpus than ``typescript`` (higher IDF), so the node carrying the
rarer non-subject token at high term-frequency won -- even though it misses the
query's subject entirely. BM25 IDF rarity is not query-subject relevance for an
entity-shaped navigation query.

The fix adds a *subject* tie-break (ADR 0075 sibling): among nodes tied on
coverage / group-hit count, one that does NOT carry the query's **leading**
token-group ("typescript" here) in an identity field (id / label / file /
qualname / description) sorts last. It is applied in BOTH retrieval tiers that
can produce this ordering:

* the bounded-coverage admission tier (strict-AND + admission), via
  :func:`weld._coverage_admission.partial_coverage_subject_miss` wired into
  :func:`weld.ranking.rank_query_matches` and its sqlite / federation peers;
* the OR-fallback tier (the *durable* clean-graph case: with no node covering
  all three groups the query relaxes to OR), via
  :func:`weld._coverage_admission.subject_identity_miss` wired into
  :func:`weld.graph_query.query_or_fallback`.

The integration graphs here are hermetic, in-memory synthetic reproductions
(same pattern as ``weld_multi_token_query_test`` / ``weld_query_or_fallback_test``):
they deliberately do NOT discover the live repo, so the guard is deterministic
and immune to graph drift -- and, crucially, immune to the transient
``concept:<this-issue>`` node the bd issue itself injects (which would otherwise
activate the admission tier instead of OR-fallback and mask the durable defect).
Filler ``typescript``-bearing nodes are added so ``typescript`` is a *commoner*
token than ``discovery`` (lower IDF), reproducing the exact BM25 inversion the
live corpus exhibits; the per-group coverage split is asserted in-test so the
fixture cannot silently stop exercising the bug.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._coverage_admission import (
    covered_group_count,
    partial_coverage_subject_miss,
    subject_identity_miss,
)
from weld.contract import SCHEMA_VERSION
from weld.graph import Graph
from weld.synonyms import expand_token_groups

_TS = "2026-06-17T12:00:00+00:00"

# The exact failing query from the issue (N=3, entity-shaped; subject first).
_QUERY = "typescript discovery strategy"

# The TypeScript strategy modules the issue says must surface.
_TS_EXPORTS = "file:weld/strategies/typescript_exports"
_TS_TREE_SITTER = "file:weld/strategies/_typescript_tree_sitter"
# The unrelated discovery_state hits the issue says must NOT outrank them. The
# symbol covers {discovery, strategy} via label ("...strategy...") + file path
# ("discovery_state.py"); the file covers {discovery} only.
_DISCOVERY_FILE = "file:weld/discovery_state"
_DISCOVERY_SYM = "symbol:py:weld.discovery_state:files_missing_strategy_outputs"


def _make_graph(nodes: dict) -> Graph:
    """Build an in-memory Graph pre-loaded with *nodes* (no live discovery)."""
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    g._data = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "jucl"},
        "nodes": nodes,
        "edges": [],
    }
    g._build_inverted_index()
    return g


def _collision_nodes() -> dict:
    """Reproduce the {typescript,strategy} vs {discovery,strategy} 2/3 collision.

    No node covers all three query groups, so ``Graph.query`` relaxes to
    OR-fallback -- the durable clean-graph path. The filler ``typescript``
    nodes drive ``typescript``'s document frequency up so its IDF falls *below*
    ``discovery``'s, reproducing the BM25 inversion that made the
    subject-missing ``discovery_state`` node win before the fix.
    """
    nodes: dict = {
        # Covers {typescript, strategy} via path; NO 'discovery'.
        _TS_EXPORTS: {
            "type": "file", "label": "typescript_exports",
            "props": {"file": "weld/strategies/typescript_exports.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        _TS_TREE_SITTER: {
            "type": "file", "label": "_typescript_tree_sitter",
            "props": {"file": "weld/strategies/_typescript_tree_sitter.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        # Covers {discovery} via path; NO 'typescript', NO 'strategy'.
        _DISCOVERY_FILE: {
            "type": "file", "label": "discovery_state",
            "props": {"file": "weld/discovery_state.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        # Covers {discovery, strategy} via path + label; NO 'typescript'.
        _DISCOVERY_SYM: {
            "type": "symbol", "label": "files_missing_strategy_outputs",
            "props": {"file": "weld/discovery_state.py",
                      "qualname": "files_missing_strategy_outputs",
                      "authority": "canonical", "confidence": "definite"},
        },
    }
    # Filler typescript-bearing nodes: push typescript IDF below discovery IDF.
    for i in range(8):
        nodes[f"file:weld/x/typescript_filler{i}"] = {
            "type": "file", "label": f"typescript_filler{i}",
            "props": {"file": f"weld/x/typescript_filler{i}.ts"},
        }
    return nodes


def _ranked_ids(graph: Graph, query: str, limit: int = 20) -> list[str]:
    return [m["id"] for m in graph.query(query, limit=limit)["matches"]]


class TypescriptStrategyQueryRegressionTest(unittest.TestCase):
    """Issue jucl: TS strategy modules must outrank unrelated discovery_state."""

    def setUp(self) -> None:
        self.graph = _make_graph(_collision_nodes())
        self.groups = expand_token_groups(_QUERY.split())

    def test_fixture_reproduces_the_coverage_collision(self) -> None:
        """Guard the fixture itself still exercises the bug.

        If discovery drift ever made these nodes cover a different group set,
        the ordering assertions below would pass vacuously. Pin the exact 2/3
        split and the BM25 inversion (discovery rarer than typescript).
        """
        nodes = self.graph._data["nodes"]
        self.assertEqual(
            covered_group_count(self.groups, _TS_EXPORTS, nodes[_TS_EXPORTS]), 2)
        self.assertEqual(
            covered_group_count(self.groups, _DISCOVERY_SYM, nodes[_DISCOVERY_SYM]),
            2,
            "the discovery_state symbol must tie the TS module on coverage for "
            "the collision to be real",
        )
        self.graph._ensure_query_state()
        bm25 = self.graph._bm25
        self.assertGreater(
            bm25._idf("discovery"), bm25._idf("typescript"),
            "fixture must reproduce the corpus inversion (discovery rarer than "
            "typescript) that makes BM25 alone pick the wrong node",
        )

    def test_relaxes_to_or_fallback(self) -> None:
        """No node covers all three groups -> the query is the OR-fallback case.

        This pins that the guard exercises the *durable* clean-graph path (the
        one that reproduces without the transient concept:<issue> node), not
        the admission path.
        """
        result = self.graph.query(_QUERY, limit=20)
        self.assertEqual(result.get("degraded_match"), "or_fallback")

    def test_ts_modules_outrank_discovery_state(self) -> None:
        """Headline golden: both TS strategy modules rank above both
        discovery_state hits for the entity-shaped query."""
        ids = _ranked_ids(self.graph, _QUERY)
        for ts_node in (_TS_EXPORTS, _TS_TREE_SITTER):
            self.assertIn(ts_node, ids, f"{ts_node} must surface for {_QUERY!r}")
        for noise in (_DISCOVERY_FILE, _DISCOVERY_SYM):
            if noise in ids:
                best_ts = min(ids.index(_TS_EXPORTS), ids.index(_TS_TREE_SITTER))
                self.assertLess(
                    best_ts, ids.index(noise),
                    f"a TypeScript strategy module must rank ABOVE {noise} for "
                    f"{_QUERY!r}; ranked ids {ids}",
                )

    def test_subject_tie_break_only_demotes_within_a_group_hit_tier(self) -> None:
        """The subject tie-break re-ranks *within* a group-hit tier, not across.

        A node hitting strictly more query groups must still win on
        ``-group_hits`` regardless of the subject signal: the discovery_state
        symbol (group_hits=2, subject absent) legitimately outranks the
        single-group filler nodes (group_hits=1). The subject tie-break only
        decides the order *among* the group_hits==2 nodes, where it pushes the
        subject-missing symbol below both 2-hit TS modules.
        """
        ids = _ranked_ids(self.graph, _QUERY)
        # Within the 2-hit tier the symbol sits below both TS modules ...
        self.assertLess(ids.index(_TS_EXPORTS), ids.index(_DISCOVERY_SYM))
        self.assertLess(ids.index(_TS_TREE_SITTER), ids.index(_DISCOVERY_SYM))
        # ... but still ahead of the 1-hit filler nodes (group_hits dominates).
        self.assertLess(
            ids.index(_DISCOVERY_SYM), ids.index("file:weld/x/typescript_filler0"))


class SubjectIdentityMissPredicateTest(unittest.TestCase):
    """Unit coverage for the OR-fallback subject tie-break predicate."""

    def setUp(self) -> None:
        self.groups = expand_token_groups(_QUERY.split())

    def test_subject_in_path_is_not_penalised(self) -> None:
        node = {"type": "file", "label": "typescript_exports",
                "props": {"file": "weld/strategies/typescript_exports.py"}}
        self.assertEqual(subject_identity_miss(_TS_EXPORTS, node, self.groups), 0)

    def test_subject_absent_is_penalised(self) -> None:
        node = {"type": "symbol", "label": "files_missing_strategy_outputs",
                "props": {"file": "weld/discovery_state.py",
                          "qualname": "files_missing_strategy_outputs"}}
        self.assertEqual(subject_identity_miss(_DISCOVERY_SYM, node, self.groups), 1)

    def test_subject_in_description_is_not_penalised(self) -> None:
        """A description mention of the subject still counts as identity."""
        node = {"type": "file", "label": "helper",
                "props": {"file": "weld/helper.py",
                          "description": "a typescript discovery helper"}}
        self.assertEqual(subject_identity_miss("file:weld/helper", node, self.groups), 0)

    def test_single_token_query_is_inert(self) -> None:
        """Single-token queries never reach OR-fallback -> always 0."""
        groups = expand_token_groups(["discovery"])
        node = {"type": "file", "label": "x", "props": {"file": "weld/x.py"}}
        self.assertEqual(subject_identity_miss("file:weld/x", node, groups), 0)

    def test_two_token_query_is_active(self) -> None:
        """The OR-fallback tier fires for N>=2, so the predicate is active there."""
        groups = expand_token_groups(["typescript", "strategy"])
        miss = {"type": "file", "label": "discovery_state",
                "props": {"file": "weld/discovery_state.py"}}
        hit = {"type": "file", "label": "typescript_exports",
               "props": {"file": "weld/strategies/typescript_exports.py"}}
        self.assertEqual(subject_identity_miss(_DISCOVERY_FILE, miss, groups), 1)
        self.assertEqual(subject_identity_miss(_TS_EXPORTS, hit, groups), 0)


class PartialCoverageSubjectMissPredicateTest(unittest.TestCase):
    """Unit coverage for the admission-tier subject tie-break predicate."""

    def setUp(self) -> None:
        self.groups = expand_token_groups(_QUERY.split())

    def test_non_admitted_node_is_never_penalised(self) -> None:
        """Without the partial_coverage tag the dimension is inert.

        Protects strict-AND full-coverage matches and diffuse docs (which carry
        no partial_coverage tag) from being reordered by this dimension.
        """
        node = {"id": _DISCOVERY_SYM, "type": "symbol",
                "label": "files_missing_strategy_outputs",
                "props": {"file": "weld/discovery_state.py"}}
        self.assertEqual(partial_coverage_subject_miss(node, self.groups), 0)

    def test_admitted_subject_missing_node_is_penalised(self) -> None:
        node = {"id": _DISCOVERY_SYM, "type": "symbol",
                "label": "files_missing_strategy_outputs",
                "partial_coverage": True,
                "props": {"file": "weld/discovery_state.py"}}
        self.assertEqual(partial_coverage_subject_miss(node, self.groups), 1)

    def test_admitted_subject_bearing_node_is_not_penalised(self) -> None:
        node = {"id": _TS_EXPORTS, "type": "file", "label": "typescript_exports",
                "partial_coverage": True,
                "props": {"file": "weld/strategies/typescript_exports.py"}}
        self.assertEqual(partial_coverage_subject_miss(node, self.groups), 0)

    def test_admission_dimension_inert_below_three_groups(self) -> None:
        """The admission tier is empty for N<3, so this dimension is inert there.

        (The OR-fallback predicate, by contrast, is active for N>=2 -- the two
        tiers fire on different token-count regimes.)
        """
        groups = expand_token_groups(["typescript", "strategy"])
        node = {"id": _DISCOVERY_FILE, "type": "file", "label": "discovery_state",
                "partial_coverage": True,
                "props": {"file": "weld/discovery_state.py"}}
        self.assertEqual(partial_coverage_subject_miss(node, groups), 0)


class ImplParitySubjectTieBreakTest(unittest.TestCase):
    """The subject tie-break is wired identically across the three query impls.

    8rm0.4 requires impl #1 (JSON ranking), impl #2 (sqlite) and impl #3
    (federation eager) to agree. The admission-tier predicate must be imported
    and called in all three sort keys.
    """

    def test_predicate_present_in_all_three_sort_keys(self) -> None:
        import inspect

        from weld import _federation_eager_index, _sqlite_query, ranking

        for module in (ranking, _sqlite_query, _federation_eager_index):
            src = inspect.getsource(module)
            self.assertIn(
                "partial_coverage_subject_miss", src,
                f"{module.__name__} must wire the admission-tier subject "
                "tie-break for impl parity (8rm0.4)",
            )


if __name__ == "__main__":
    unittest.main()
