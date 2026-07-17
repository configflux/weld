"""Stopword filtering in wd query tokenization (bd 5038-10ui).

Natural-language / conceptual queries ("how does auth work", "where is
retry logic for publishing") used to fail because function-word stopwords
padded the strict-AND across many token-groups, forcing a drop to the noisy
OR-fallback where a node matching only a stopword-adjacent token ("work",
"does") could dominate. :func:`weld.synonyms.filter_stopwords` drops a tight
function-word set BEFORE strict-AND so the content-bearing tokens drive
matching and the leading content token becomes the OR-fallback subject
(``token_groups[0]``).

The filter is applied at the single :func:`weld.synonyms.expand_token_groups`
chokepoint that every query path (JSON ``Graph`` read path, its sqlite peer,
federation) routes through, so the paths cannot drift on stopword handling.

Two guards keep already-good queries byte-identical (asserted below): a
single-token query (a bare symbol / lexical term) is never stripped, and an
all-stopword query is returned unchanged rather than collapsing to a
match-everything empty token set. The graphs here are hermetic in-memory
synthetic reproductions (same pattern as ``weld_query_or_fallback_test`` and
``weld_synonym_expansion_test``) so the guards are deterministic and immune
to live-graph drift.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.graph import Graph
from weld.synonyms import (
    _QUERY_STOPWORDS,
    expand_token_groups,
    filter_stopwords,
)


def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create a hermetic in-memory Graph with the given nodes and edges."""
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    g._data = {"meta": {"version": 1}, "nodes": nodes, "edges": edges or []}
    g._build_inverted_index()
    return g


class FilterStopwordsUnitTest(unittest.TestCase):
    """Pure-function contract for :func:`filter_stopwords`."""

    def test_removes_leading_function_words(self) -> None:
        # The motivating live query: "how does auth work" -> content tokens.
        self.assertEqual(
            filter_stopwords(["how", "does", "auth", "work"]),
            ["auth", "work"],
        )

    def test_removes_interior_and_trailing_function_words(self) -> None:
        # "where is retry logic for publishing".
        self.assertEqual(
            filter_stopwords(
                ["where", "is", "retry", "logic", "for", "publishing"]
            ),
            ["retry", "logic", "publishing"],
        )

    def test_order_is_preserved(self) -> None:
        # Subject (leading content token) must stay first: it seeds the
        # OR-fallback subject tie-break (token_groups[0]).
        self.assertEqual(
            filter_stopwords(["what", "is", "the", "circle", "area"]),
            ["circle", "area"],
        )

    def test_single_token_query_is_never_stripped(self) -> None:
        # A bare lexical/symbol query is exactly one token; even a lone
        # stopword is left alone so single-token behaviour is unchanged.
        self.assertEqual(filter_stopwords(["the"]), ["the"])
        self.assertEqual(filter_stopwords(["auth"]), ["auth"])
        self.assertEqual(
            filter_stopwords(["symbol:py:weld.graph_query:query_graph"]),
            ["symbol:py:weld.graph_query:query_graph"],
        )

    def test_all_stopword_query_returned_unchanged(self) -> None:
        # Nothing content-bearing survives -> keep the original tokens so the
        # query does not collapse to an empty (match-everything) token set.
        self.assertEqual(
            filter_stopwords(["how", "does", "it"]),
            ["how", "does", "it"],
        )

    def test_work_is_not_a_stopword(self) -> None:
        # "work" is a borderline CONTENT word (worker, workflow, workspace):
        # it must survive so it can name code, per the tight function-word set.
        self.assertNotIn("work", _QUERY_STOPWORDS)
        self.assertEqual(filter_stopwords(["work", "queue"]), ["work", "queue"])

    def test_pure_content_query_unchanged(self) -> None:
        self.assertEqual(
            filter_stopwords(["retry", "publish"]), ["retry", "publish"]
        )

    def test_empty_and_idempotent(self) -> None:
        self.assertEqual(filter_stopwords([]), [])
        once = filter_stopwords(["how", "does", "auth", "work"])
        self.assertEqual(filter_stopwords(once), once)  # deterministic/stable

    def test_stopword_set_is_tight_function_words_only(self) -> None:
        # Guard against scope creep into content-ish words. These must NOT be
        # treated as stopwords (they routinely name code).
        for content in ("work", "test", "set", "get", "run", "log", "call",
                        "data", "type", "and", "or", "not"):
            self.assertNotIn(content, _QUERY_STOPWORDS, content)


class ExpandTokenGroupsStopwordTest(unittest.TestCase):
    """The filter is embedded in the shared expand_token_groups chokepoint."""

    def test_stopwords_do_not_add_and_clauses(self) -> None:
        # "where is auth" -> a single content group (auth), not three.
        groups = expand_token_groups(["where", "is", "auth"])
        self.assertEqual(len(groups), 1)
        self.assertIn("auth", groups[0])

    def test_single_token_group_unaffected(self) -> None:
        # Regression: a bare content token expands exactly as before
        # (itself + synonyms + stems), unperturbed by the filter.
        self.assertEqual(expand_token_groups(["shape"]), [["shape", "shapes"]])

    def test_all_stopword_query_keeps_its_groups(self) -> None:
        # No content survives -> the raw tokens are kept, so this stays a
        # 2-group query (its prior, non-empty behaviour) rather than [].
        groups = expand_token_groups(["how", "does"])
        self.assertEqual(len(groups), 2)


class StopwordQueryIntegrationTest(unittest.TestCase):
    """End-to-end on a synthetic graph: content drives matching, not noise."""

    # An auth-identity node and a "work"-only noise node that mimics the live
    # failure (a framework test whose id carries "work"/"does" but not "auth").
    _NODES = {
        "file:services/auth/session": {
            "type": "file",
            "label": "auth session handler",
            "props": {"file": "services/auth/session.py"},
        },
        "symbol:py:tools.framework_test:test_worker_does_not_fire": {
            "type": "symbol",
            "label": "test_worker_does_not_fire",
            "props": {
                "file": "tools/framework_test.py",
                "qualname": "tools.framework_test.test_worker_does_not_fire",
            },
        },
    }

    def setUp(self) -> None:
        self.graph = _make_graph(self._NODES)

    def test_stopword_prefixed_query_equals_bare_content_query(self) -> None:
        # "the auth" must return exactly what "auth" returns (stopword "the"
        # filtered) -- the single-token regression path stays intact.
        with_stop = [m["id"] for m in self.graph.query("the auth")["matches"]]
        bare = [m["id"] for m in self.graph.query("auth")["matches"]]
        self.assertEqual(with_stop, bare)
        self.assertIn("file:services/auth/session", bare)

    def test_content_subject_outranks_stopword_adjacent_noise(self) -> None:
        # The headline: for "how does auth work" the auth-subject node must
        # outrank the work/does noise node. Without the filter the noise node
        # hits two groups ("does","work") and wins; with it, the subject
        # ("auth", the leading content group) leads.
        ids = [m["id"] for m in self.graph.query("how does auth work")["matches"]]
        self.assertIn("file:services/auth/session", ids)
        noise = "symbol:py:tools.framework_test:test_worker_does_not_fire"
        self.assertLess(
            ids.index("file:services/auth/session"),
            ids.index(noise) if noise in ids else len(ids),
            f"auth subject should outrank stopword-adjacent noise; got {ids}",
        )

    def test_symbol_query_is_unchanged(self) -> None:
        # A bare symbol id (single token) resolves to itself, untouched.
        ids = [
            m["id"]
            for m in self.graph.query(
                "symbol:py:tools.framework_test:test_worker_does_not_fire"
            )["matches"]
        ]
        self.assertIn(
            "symbol:py:tools.framework_test:test_worker_does_not_fire", ids
        )


if __name__ == "__main__":
    unittest.main()
