"""Tests for ``SqliteBackedGraph.query`` (ADR 0058 Option B).

Asserts envelope shape parity with :meth:`weld.graph.Graph.query`:

- An empty term returns the empty envelope.
- A unique single-token term returns the expected node.
- A multi-token term ANDs the tokens (strict-AND, like the JSON path).
- A term with no matches returns an empty matches list.
- The match set against a sqlite-backed graph equals the match set
  against the JSON-backed Graph for the same fixture (semantic
  parity, the v1 acceptance criterion).
- A SQL-injection-style term (``' OR 1=1 --``) does NOT return every
  node; the parameter binding holds (incl. the per-group UNION path).

Ranked-parity surfaces (coverage admission, OR-fallback) live in
``weld_sqlite_query_parity_test``; the shared sidecar/JSON builders live in
``_sqlite_query_test_fixtures``.
"""

from __future__ import annotations

import unittest

from weld.tests._sqlite_query_test_fixtures import (
    boundary_trap_nodes,
    fixture_nodes,
    open_json_graph,
    open_sqlite_view,
)


class SqliteQueryEnvelopeTest(unittest.TestCase):
    def test_empty_term_returns_empty_envelope(self) -> None:
        view, tmp = open_sqlite_view(fixture_nodes())
        try:
            result = view.query("")
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["neighbors"], [])
            self.assertEqual(result["edges"], [])
            self.assertEqual(result["query"], "")
        finally:
            view.close()
            tmp.cleanup()

    def test_single_unique_token_finds_target(self) -> None:
        view, tmp = open_sqlite_view(fixture_nodes())
        try:
            ids = {m["id"] for m in view.query("auth")["matches"]}
            self.assertIn("service:auth", ids)
            self.assertNotIn("service:billing", ids)
        finally:
            view.close()
            tmp.cleanup()

    def test_term_with_no_hits_returns_empty_matches(self) -> None:
        view, tmp = open_sqlite_view(fixture_nodes())
        try:
            ids = {m["id"] for m in view.query("zzzzz_nothing")["matches"]}
            self.assertEqual(set(), ids)
        finally:
            view.close()
            tmp.cleanup()

    def test_match_set_parity_with_json_graph(self) -> None:
        """Match-set parity against JSON ``Graph.query`` on the same fixture.

        ADR 0058 Option B acceptance: the sqlite path's match IDs must
        equal the JSON path's match IDs (modulo rank, which Option B
        does not yet promise to preserve). We assert set equality here
        and let a separate test pin ordering when we extend to hybrid
        scoring.
        """
        nodes = fixture_nodes()
        view, tmp_sqlite = open_sqlite_view(nodes)
        try:
            json_graph, tmp_json = open_json_graph(nodes)
            try:
                for term in ("auth", "billing", "helper", "README"):
                    sqlite_ids = {
                        m["id"]
                        for m in view.query(term, limit=50)["matches"]
                    }
                    json_ids = {
                        m["id"]
                        for m in json_graph.query(term, limit=50)["matches"]
                    }
                    self.assertEqual(
                        sqlite_ids, json_ids,
                        f"match set diverged for term {term!r}",
                    )
            finally:
                tmp_json.cleanup()
        finally:
            view.close()
            tmp_sqlite.cleanup()


class SqliteQuerySecurityTest(unittest.TestCase):
    """Term-injection probes for the parameter-bound reader."""

    def test_injection_attempt_does_not_widen_to_all_nodes(self) -> None:
        view, tmp = open_sqlite_view(fixture_nodes())
        try:
            # A literal ``' OR 1=1 --`` cannot escape parameter
            # binding, so the substring search returns zero hits.
            self.assertEqual(
                [], view.query("' OR 1=1 --")["matches"],
            )
            # A bare ``%`` must not match every row -- the LIKE
            # wildcard is escaped on the indexed-token side.
            self.assertEqual([], view.query("%")["matches"])
            self.assertEqual([], view.query("_")["matches"])
        finally:
            view.close()
            tmp.cleanup()

    def test_term_with_semicolon_and_drop_does_not_alter_schema(self) -> None:
        """``term`` is bound as a parameter; DDL inside it must not run."""
        view, tmp = open_sqlite_view(fixture_nodes())
        try:
            view.query("'; DROP TABLE token_index; --")
            # If the DDL ran, the next query would fail. The
            # connection is read-only, so even an unbound execution
            # would error -- this is a belt-and-braces check.
            ids = {m["id"] for m in view.query("auth")["matches"]}
            self.assertIn("service:auth", ids)
        finally:
            view.close()
            tmp.cleanup()

    def test_multi_token_union_path_keeps_parameter_binding(self) -> None:
        """The per-group UNION path is parameter-bound too.

        The UNION scan (``_candidates_union``) feeds both the N>=3 admission
        tier and the OR-fallback relaxation. A multi-token injection probe must
        still bind every token literally and never widen to the full node set.

        The probe tokens are chosen so none is a substring of any indexed
        token, so a parameter-bound search yields exactly zero matches. (A
        token like ``or`` would legitimately substring-match real content such
        as "ordering" -- that is correct literal matching, not injection, so it
        is deliberately avoided here to keep the assertion about binding.)
        """
        view, tmp = open_sqlite_view(boundary_trap_nodes())
        try:
            # Three injection-shaped tokens, each free of any substring overlap
            # with the fixture's indexed tokens => N>=3 (union path runs) and a
            # held binding yields nothing.
            result = view.query("zz'; xy=1 qq--")
            self.assertEqual([], result["matches"])
            self.assertNotIn("degraded_match", result)
        finally:
            view.close()
            tmp.cleanup()

    def test_injection_probe_does_not_widen_via_or_fallback(self) -> None:
        """A classic ``' OR 1=1 --`` probe must not widen to the full node set.

        With OR-fallback now reachable on the sqlite path, this asserts the
        stronger invariant directly: the probe may only surface nodes whose
        indexed text literally contains one of the probe tokens as a substring
        (here only the token ``or`` collides, with "ordering"/"order"), and it
        must NEVER return the distractor or every node -- which is what a
        successful injection would do. Binding holds: the literal token did the
        matching, not unescaped SQL.
        """
        nodes = boundary_trap_nodes()
        view, tmp = open_sqlite_view(nodes)
        try:
            result = view.query("' OR 1=1 -- ' OR 1=1 -- ' OR 1=1 --")
            ids = {m["id"] for m in result["matches"]}
            # The distractor contains no probe-token substring; an injection
            # that escaped binding would return it (and every other node).
            self.assertNotIn("file:weld/unrelated", ids)
            self.assertNotEqual(ids, set(nodes), "probe must not widen to all")
            # Every returned node must literally carry a probe token; otherwise
            # the binding leaked.
            from weld._coverage_admission import count_groups_hit
            from weld.synonyms import expand_token_groups
            groups = expand_token_groups(
                "' OR 1=1 -- ' OR 1=1 -- ' OR 1=1 --".lower().split()
            )
            for nid in ids:
                self.assertGreater(
                    count_groups_hit(groups, nid, nodes[nid]), 0,
                    f"{nid} matched without a literal token hit -> binding leak",
                )
        finally:
            view.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
