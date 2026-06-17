"""Ranked-parity tests for the sqlite-backed query path.

Two parity surfaces, both asserting the sqlite path matches the in-memory
JSON ``Graph.query`` path:

- Coverage-aware admission + diffuse-doc demotion (ADR 0075) on an
  entity-shaped multi-token query where strict-AND admits only a diffuse doc.
- OR-fallback relaxation: when strict-AND yields zero on a multi-token query,
  the sqlite path relaxes to the per-group UNION (ranked the same way) and
  tags ``degraded_match='or_fallback'``. Before this fix the sqlite surface
  returned an empty envelope while the JSON surface returned ranked union
  results.

The graphs are hermetic synthetic reproductions (the builders live in
``_sqlite_query_test_fixtures``) so the guards are deterministic and immune to
graph drift.
"""

from __future__ import annotations

import unittest

from weld.tests._sqlite_query_test_fixtures import (
    DOC_NODE,
    QUERY,
    STRATEGY_NODE,
    TEST_NODE,
    boundary_trap_nodes,
    disjoint_token_nodes,
    open_json_graph,
    open_sqlite_view,
)


class SqliteCoverageAdmissionTest(unittest.TestCase):
    """ADR 0075 parity for impl #2 (sqlite child-repo path)."""

    def test_admission_surfaces_high_coverage_code_above_diffuse_doc(self) -> None:
        """3/4 code nodes are admitted AND outrank the 4/4 diffuse doc.

        ``max(2, N-1)`` admission (N=4 -> threshold 3) admits both 3/4 code
        nodes and the diffuse-doc demotion re-ranks the doc below them.
        """
        view, tmp = open_sqlite_view(boundary_trap_nodes())
        try:
            ids = [m["id"] for m in view.query(QUERY, limit=5)["matches"]]
            self.assertNotIn("file:weld/unrelated", ids)
            present_code = [n for n in (STRATEGY_NODE, TEST_NODE) if n in ids]
            self.assertTrue(
                present_code,
                f"expected a high-coverage code node for {QUERY!r}; got {ids}",
            )
            self.assertIn(
                DOC_NODE, ids,
                "the diffuse doc must remain present (re-ranked, not excluded)",
            )
            self.assertLess(
                min(ids.index(n) for n in present_code), ids.index(DOC_NODE),
                f"a code node must outrank the diffuse doc for {QUERY!r}; {ids}",
            )
        finally:
            view.close()
            tmp.cleanup()

    def test_admitted_nodes_tagged_and_diffuse_tag_not_leaked(self) -> None:
        """Admissions carry ``partial_coverage``; ``_diffuse`` never leaks."""
        view, tmp = open_sqlite_view(boundary_trap_nodes())
        try:
            matches = {m["id"]: m
                       for m in view.query(QUERY, limit=5)["matches"]}
            self.assertTrue(matches[STRATEGY_NODE].get("partial_coverage"))
            self.assertTrue(matches[TEST_NODE].get("partial_coverage"))
            # The strict-AND (full-coverage) doc is not a partial admission.
            self.assertNotIn("partial_coverage", matches[DOC_NODE])
            for match in matches.values():
                self.assertNotIn("_diffuse", match)
        finally:
            view.close()
            tmp.cleanup()

    def test_admission_inert_below_three_tokens(self) -> None:
        """N<=2 is fully inert: no 2/4-only node leaks in via admission.

        For the 2-token query ``boundary entrypoint`` the threshold is full
        coverage (max(2,1)=2), so strict-AND already admits exactly the nodes
        that cover both tokens; the distractor never appears and no demotion
        re-orders the (single) doc-free result.
        """
        view, tmp = open_sqlite_view(boundary_trap_nodes())
        try:
            ids = {m["id"]
                   for m in view.query("boundary entrypoint", limit=5)["matches"]}
            # All three real nodes cover {boundary, entrypoint}; the distractor
            # does not. Admission adds nothing strict-AND would not.
            self.assertNotIn("file:weld/unrelated", ids)
            self.assertEqual(
                ids,
                {STRATEGY_NODE, TEST_NODE, DOC_NODE},
                "N=2 strict-AND set should be exactly the both-token coverers",
            )
        finally:
            view.close()
            tmp.cleanup()

    def test_parity_with_json_graph_on_entity_query(self) -> None:
        """The sqlite path's N>=3 ordering now matches impl #1 (JSON Graph).

        ADR 0075 brings impl #2 to parity with impl #1, so the entity-shaped
        query that impl #1's headline golden pins must produce the same ranked
        match list here (not merely the same set).
        """
        nodes = boundary_trap_nodes()
        view, tmp_sqlite = open_sqlite_view(nodes)
        try:
            json_graph, tmp_json = open_json_graph(nodes)
            try:
                sqlite_ids = [m["id"]
                              for m in view.query(QUERY, limit=20)["matches"]]
                json_ids = [m["id"]
                            for m in json_graph.query(QUERY, limit=20)["matches"]]
                self.assertEqual(
                    sqlite_ids, json_ids,
                    f"sqlite vs JSON ranked order diverged for {QUERY!r}",
                )
            finally:
                tmp_json.cleanup()
        finally:
            view.close()
            tmp_sqlite.cleanup()


class SqliteOrFallbackTest(unittest.TestCase):
    """The sqlite path relaxes to OR-fallback on zero-strict-AND multi-token."""

    def test_multi_token_disjoint_relaxes_to_or_fallback(self) -> None:
        """Both tokens hit different nodes -> ranked union + degraded flag.

        This is the headline parity case: a multi-word query where no single
        node covers all token groups but each group has matches on its own.
        Strict-AND returns nothing; the OR fallback now surfaces both nodes
        and tags the envelope so consumers know the result was relaxed.
        """
        view, tmp = open_sqlite_view(disjoint_token_nodes())
        try:
            result = view.query("discovery strategy", limit=5)
            ids = {m["id"] for m in result["matches"]}
            self.assertIn("module:discovery", ids)
            self.assertIn("module:strategy", ids)
            self.assertNotIn("module:unrelated", ids)
            self.assertEqual(result.get("degraded_match"), "or_fallback")
        finally:
            view.close()
            tmp.cleanup()

    def test_one_token_zero_other_nonzero_returns_other(self) -> None:
        """A token with no hits does not zero the whole multi-token result.

        When one token has zero candidates but another has matches, the OR
        fallback returns the matching token's nodes instead of the empty
        envelope strict-AND would have produced.
        """
        view, tmp = open_sqlite_view(disjoint_token_nodes())
        try:
            result = view.query("discovery zzznonexistent", limit=5)
            ids = {m["id"] for m in result["matches"]}
            self.assertIn("module:discovery", ids)
            self.assertNotIn("module:strategy", ids)
            self.assertEqual(result.get("degraded_match"), "or_fallback")
        finally:
            view.close()
            tmp.cleanup()

    def test_multi_token_both_zero_returns_empty_no_flag(self) -> None:
        """Zero strict-AND AND zero OR -> honest empty envelope, no flag."""
        view, tmp = open_sqlite_view(disjoint_token_nodes())
        try:
            result = view.query("zzznonexistent xyznonexistent", limit=5)
            self.assertEqual(result["matches"], [])
            self.assertNotIn("degraded_match", result)
        finally:
            view.close()
            tmp.cleanup()

    def test_single_token_zero_is_empty_no_fallback(self) -> None:
        """Single-token queries skip the fallback (OR == AND for one group)."""
        view, tmp = open_sqlite_view(disjoint_token_nodes())
        try:
            result = view.query("zzznonexistent", limit=5)
            self.assertEqual(result["matches"], [])
            self.assertNotIn("degraded_match", result)
        finally:
            view.close()
            tmp.cleanup()

    def test_ranked_parity_with_json_graph_on_zero_and_query(self) -> None:
        """The sqlite OR-fallback ranked list equals the JSON path's.

        Equivalent ranked results (not merely the same set) for the same
        zero-strict-AND multi-token query, including the same
        ``degraded_match`` signal.
        """
        nodes = disjoint_token_nodes()
        view, tmp_sqlite = open_sqlite_view(nodes)
        try:
            json_graph, tmp_json = open_json_graph(nodes)
            try:
                term = "discovery strategy"
                sqlite_env = view.query(term, limit=20)
                json_env = json_graph.query(term, limit=20)
                sqlite_ids = [m["id"] for m in sqlite_env["matches"]]
                json_ids = [m["id"] for m in json_env["matches"]]
                self.assertEqual(
                    sqlite_ids, json_ids,
                    f"sqlite vs JSON OR-fallback order diverged for {term!r}",
                )
                self.assertEqual(
                    sqlite_env.get("degraded_match"),
                    json_env.get("degraded_match"),
                )
                self.assertEqual(sqlite_env.get("degraded_match"), "or_fallback")
            finally:
                tmp_json.cleanup()
        finally:
            view.close()
            tmp_sqlite.cleanup()


if __name__ == "__main__":
    unittest.main()
