"""Headline golden for parent bd 8rm0 -- the entity-shaped multi-token query.

Parent 8rm0: for the entity-shaped multi-token query
``boundary entrypoint strategy test`` the strategy module
(``file:weld/strategies/boundary_entrypoint``) and its sibling test target
(``file:weld/tests/weld_boundary_entrypoint_test``) historically never
surfaced, because strict-AND (``weld.graph.Graph._match_token_groups`` via
``weld.synonyms.candidate_nodes_grouped``) required every query token-group to
hit one node and no single code node covers all four literal tokens -- the
strategy path tokenizes ``strategies`` (not ``strategy``) and ``test`` lives
only on the sibling test node. The ``docs/determinism-audit-T1a`` doc won
purely because its headings happen to contain all four tokens, and OR-fallback
could not help (it only fires when strict-AND yields ZERO matches; there it
yielded the doc).

ADR 0075 (landed in 8rm0.3) fixes this in impl #1 (the in-memory ``Graph``
read path) with a dual-gated (N>=3) mechanism:

* **stemming** (8rm0.2) lifts the strategy file 2/4 -> 3/4 group coverage
  (``strategy`` ~= ``strategies``);
* **bounded coverage admission** (8rm0.3) additionally admits non-doc nodes at
  ``>= max(2, N-1)`` coverage, so the 3/4 strategy and 3/4 test nodes are
  admitted alongside the 4/4 doc and tagged ``partial_coverage``;
* **diffuse-doc demotion** (8rm0.3) re-ranks the heading-only doc BELOW the
  admitted code nodes (never excluding it).

This module is the regression guard those subtasks landed against:

* ``test_admission_surfaces_high_coverage_code`` pins the post-8rm0.3 behavior:
  the high-coverage code nodes are now admitted (previously this test pinned
  the *defect* -- code absent, doc alone -- and was amended at 8rm0.3 once
  admission first surfaced them).

* ``test_high_coverage_code_outranks_diffuse_doc`` is the headline golden: a
  high-coverage code/entity node ranks ABOVE the heading-only doc, using top-k
  membership semantics. It was ``@unittest.expectedFailure`` through 8rm0.2 and
  flips to a HARD PASS at 8rm0.3 (this change).

The graph here is a hermetic, in-memory synthetic reproduction (the same
pattern as ``weld_query_or_fallback_test``): it deliberately does NOT discover
the live repo, so the guard is deterministic and immune to graph drift across
the mechanism change. The node tokenization was verified against
``weld.query_index.node_tokens`` so the group-coverage split (strategy 3/4 with
stemming, test 3/4, doc 4/4) matches the parent issue's analysis exactly.

Graph SHA authored against: c7541e7df90a990762a6904cb121b5434e2e5992 (the
pre-8rm0.3 ``wd query 'boundary entrypoint strategy test'`` returned the
determinism-audit doc, not the strategy/test nodes).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402

_TS = "2026-06-16T12:00:00+00:00"

# The exact failing query from parent 8rm0 (N=4, entity-shaped).
_QUERY = "boundary entrypoint strategy test"

# The three node IDs the parent issue names. The strategy module and its test
# target are the code/entity nodes that should outrank the doc; the
# determinism-audit doc is the heading-only ("diffuse") full-coverage match
# that currently wins.
_STRATEGY_NODE = "file:weld/strategies/boundary_entrypoint"
_TEST_NODE = "file:weld/tests/weld_boundary_entrypoint_test"
_DOC_NODE = "doc:docs/determinism-audit-T1a"


def _boundary_entrypoint_graph() -> Graph:
    """Hermetic reproduction of the parent 8rm0 group-coverage trap.

    Token coverage of the four query groups
    ``[boundary] [entrypoint] [strategy] [test]`` (verified against
    ``weld.query_index.node_tokens``):

    * strategy module -> boundary, entrypoint  = 2/4 (path tokenizes
      ``strategies``; ``strategy`` is not a substring of ``strategies``);
    * test target     -> boundary, entrypoint, test = 3/4;
    * determinism doc -> all four via headings   = 4/4.

    Strict-AND therefore admits only the doc, so OR-fallback never fires and
    the two code nodes are filtered out before ranking -- exactly the defect.
    """
    nodes = {
        # 2/4: 'strategies' on the path, no literal 'strategy' or 'test'.
        _STRATEGY_NODE: {
            "type": "file",
            "label": "boundary_entrypoint",
            "props": {
                "file": "weld/strategies/boundary_entrypoint.py",
                "authority": "canonical",
                "confidence": "definite",
            },
        },
        # 3/4: adds 'test' (and 'tests'); still missing 'strategy'.
        _TEST_NODE: {
            "type": "file",
            "label": "weld_boundary_entrypoint_test",
            "props": {
                "file": "weld/tests/weld_boundary_entrypoint_test.py",
                "authority": "canonical",
                "confidence": "definite",
            },
        },
        # 4/4: the diffuse doc -- all four query tokens appear ONLY in
        # headings (a bag field), none on an identity field. This is the
        # node 8rm0.3 must demote below the admitted code nodes.
        _DOC_NODE: {
            "type": "doc",
            "label": "Determinism Audit T1A",
            "props": {
                "file": "docs/determinism-audit-T1a.md",
                "authority": "canonical",
                "confidence": "definite",
                "headings": [
                    "Boundary handling",
                    "Entrypoint ordering",
                    "Strategy emission order",
                    "Test peer wiring",
                ],
            },
        },
        # Distractor: matches none of the four groups, must never appear.
        "file:weld/unrelated": {
            "type": "file",
            "label": "unrelated",
            "props": {"file": "weld/unrelated.py"},
        },
    }
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    g._data = {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "git_sha": "8rm0_repro",
        },
        "nodes": nodes,
        "edges": [],
    }
    g._build_inverted_index()
    return g


def _ranked_ids(graph: Graph, query: str, limit: int = 5) -> list[str]:
    """Return the ranked match IDs for *query* (top-k order preserved)."""
    result = graph.query(query, limit=limit)
    return [m["id"] for m in result["matches"]]


class MultiTokenCoverageRegressionTest(unittest.TestCase):
    """Parent 8rm0 regression guard (synthetic, hermetic)."""

    def test_admission_surfaces_high_coverage_code(self) -> None:
        """Post-8rm0.3: bounded coverage admission surfaces the code nodes.

        Before 8rm0.3 this pinned the defect (the strategy/test code nodes were
        filtered out before ranking; only the 4/4-coverage doc survived
        strict-AND). 8rm0.3's ``max(2, N-1)`` admission (N=4 -> threshold 3)
        now admits both 3/4 code nodes; the doc remains present (re-ranked, not
        excluded). The distractor (0/4) must still never appear.
        """
        graph = _boundary_entrypoint_graph()
        ids = _ranked_ids(graph, _QUERY, limit=5)
        # The diffuse doc remains in the result set (demoted, not removed).
        self.assertIn(_DOC_NODE, ids)
        # Both high-coverage code nodes are now admitted at 3/4 >= max(2, 3).
        self.assertIn(_STRATEGY_NODE, ids)
        self.assertIn(_TEST_NODE, ids)
        # The 0/4 distractor must never be admitted.
        self.assertNotIn("file:weld/unrelated", ids)

    def test_admitted_code_nodes_carry_partial_coverage_tag(self) -> None:
        """Admitted partial-coverage nodes are tagged; the doc is not.

        ADR 0075 part 1 tags bounded-coverage admissions with
        ``partial_coverage=True`` (consumer signal, mirroring
        ``degraded_match``). The full-coverage doc is admitted by strict-AND,
        so it carries no such tag. The internal ``_diffuse`` ranking tag must
        never leak into the public envelope.
        """
        graph = _boundary_entrypoint_graph()
        matches = {m["id"]: m for m in graph.query(_QUERY, limit=5)["matches"]}
        self.assertTrue(matches[_STRATEGY_NODE].get("partial_coverage"))
        self.assertTrue(matches[_TEST_NODE].get("partial_coverage"))
        # Strict-AND (full-coverage) doc is not a partial-coverage admission.
        self.assertNotIn("partial_coverage", matches[_DOC_NODE])
        # Internal demotion tag must be stripped from every envelope match.
        for match in matches.values():
            self.assertNotIn("_diffuse", match)

    def test_high_coverage_code_outranks_diffuse_doc(self) -> None:
        """Headline golden (8rm0.3): a code/entity node ranks above the doc.

        Top-k membership semantics: at least one of the strategy module / its
        test target must be present AND ranked strictly above the heading-only
        determinism-audit doc. This was ``@unittest.expectedFailure`` through
        8rm0.2 and flips to a HARD PASS at 8rm0.3 once admission + diffuse-doc
        demotion land. The distractor must never appear regardless.
        """
        graph = _boundary_entrypoint_graph()
        ids = _ranked_ids(graph, _QUERY, limit=5)

        self.assertNotIn("file:weld/unrelated", ids)

        # At least one high-coverage code node must surface.
        present_code = [n for n in (_STRATEGY_NODE, _TEST_NODE) if n in ids]
        self.assertTrue(
            present_code,
            "expected the boundary_entrypoint strategy and/or its test target "
            "to surface for the query "
            f"{_QUERY!r}; got ranked ids {ids}",
        )

        # The doc may still be present, but a surfaced code node must outrank
        # it (strictly smaller index == higher rank).
        self.assertIn(
            _DOC_NODE, ids,
            "the determinism-audit doc should remain present (re-ranked, not "
            "excluded)",
        )
        doc_rank = ids.index(_DOC_NODE)
        best_code_rank = min(ids.index(n) for n in present_code)
        self.assertLess(
            best_code_rank, doc_rank,
            "a high-coverage code node must rank ABOVE the heading-only "
            f"doc:docs/determinism-audit-T1a for {_QUERY!r}; ranked ids {ids}",
        )


class DiffuseDocPredicateTest(unittest.TestCase):
    """Unit coverage for the ADR 0075 diffuse-doc discriminator.

    Locks the two diffuse conditions and the identity-field escapes
    (especially ``nid``, which :meth:`Graph._match_token_groups` matches on
    but which is easy to drop from the discriminator).
    """

    def setUp(self) -> None:
        from weld.synonyms import expand_token_groups

        self.groups = expand_token_groups(_QUERY.split())
        # Four query tokens, each in a *separate* heading -> the canonical
        # diffuse shape (bag-only, no co-locating string).
        self._scattered = ["Boundary handling", "Entrypoint ordering",
                           "Strategy emission order", "Test peer wiring"]

    def _is_diffuse(self, nid: str, node: dict) -> bool:
        from weld._coverage_admission import is_diffuse_doc

        return is_diffuse_doc(nid, node, self.groups)

    def test_scattered_headings_are_diffuse(self) -> None:
        node = {"type": "doc", "label": "Determinism",
                "props": {"file": "docs/det.md", "headings": self._scattered}}
        self.assertTrue(self._is_diffuse("doc:docs/determinism-audit-T1a", node))

    def test_scattered_constants_are_diffuse(self) -> None:
        node = {"type": "doc", "label": "Determinism",
                "props": {"file": "docs/det.md",
                          "constants": ["BOUNDARY", "ENTRYPOINT",
                                        "STRATEGY", "TEST"]}}
        self.assertTrue(self._is_diffuse("doc:docs/det", node))

    def test_nid_carrying_a_group_is_not_diffuse(self) -> None:
        """A doc whose *id* names a query group is about the concept.

        Guards the identity-field contract for ``nid`` specifically: the id
        carries ``strategy`` while ``props.file`` does not, so a discriminator
        that ignored ``nid`` would wrongly demote this doc.
        """
        node = {"type": "doc", "label": "Determinism",
                "props": {"file": "docs/det.md",
                          "headings": ["Boundary handling",
                                       "Entrypoint ordering",
                                       "Test peer wiring"]}}
        self.assertFalse(self._is_diffuse("doc:weld/strategy/det-audit", node))

    def test_label_carrying_a_group_is_not_diffuse(self) -> None:
        node = {"type": "doc",
                "label": "boundary entrypoint strategy test guide",
                "props": {"file": "docs/det.md", "headings": self._scattered}}
        self.assertFalse(self._is_diffuse("doc:docs/det", node))

    def test_co_locating_heading_is_not_diffuse(self) -> None:
        """A single heading carrying two groups is the escape hatch (cond b)."""
        node = {"type": "doc", "label": "Determinism",
                "props": {"file": "docs/det.md",
                          "headings": ["Boundary entrypoint handling",
                                       "Strategy emission order",
                                       "Test peer wiring"]}}
        self.assertFalse(self._is_diffuse("doc:docs/det", node))

    def test_non_doc_node_is_never_diffuse(self) -> None:
        node = {"type": "file", "label": "x",
                "props": {"headings": self._scattered}}
        self.assertFalse(self._is_diffuse("file:weld/x", node))


class DualGateInertnessTest(unittest.TestCase):
    """Lock the load-bearing N>=3 dual gate: N<=2 is fully inert."""

    def test_admission_inert_below_three_groups(self) -> None:
        """``coverage_admissions`` returns nothing for N<=2 (any graph)."""
        from weld._coverage_admission import coverage_admissions
        from weld.synonyms import expand_token_groups

        graph = _boundary_entrypoint_graph()
        for term in ("boundary", "boundary entrypoint"):
            groups = expand_token_groups(term.split())
            self.assertLessEqual(len(groups), 2)
            self.assertEqual(
                coverage_admissions(graph._data["nodes"], None, groups, set()),
                [],
                f"admission must be inert for N={len(groups)} ({term!r})",
            )

    def test_diffuse_tag_not_applied_below_three_groups(self) -> None:
        """``tag_match`` never sets ``_diffuse`` for N<=2 (demotion off)."""
        from weld._coverage_admission import tag_match
        from weld.synonyms import expand_token_groups

        node = {"type": "doc", "label": "Determinism",
                "props": {"file": "docs/det.md",
                          "headings": ["Boundary handling", "Test peer wiring"]}}
        groups = expand_token_groups("boundary test".split())
        tagged = tag_match("doc:docs/det", node, groups)
        self.assertNotIn("_diffuse", tagged)


if __name__ == "__main__":
    unittest.main()
