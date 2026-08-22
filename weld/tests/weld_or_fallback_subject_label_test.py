"""A node whose label *is* the query subject leads the OR-fallback ranking.

The defect (bd xsdm, fixed by ADR 0107): ``or_fallback_sort_key`` led with
``-group_hits``. ADR 0075 had already found that raw retrieval signal outranking
query-subject relevance was a defect and added ``subject_identity_miss`` -- but
placed it *after* group count, where it can only break ties between candidates
hitting the same number of groups.

So a node whose entire label was the query's subject still lost to any node
overlapping two generic tokens, and not merely in rank. On the real graph:

    query "weld_examples_test"                      -> that node, rank 1, sole match
    query "weld_examples_test discover integration" -> that node absent entirely

Adding one adjacent word evicted the node the query names verbatim. The
invariant these tests pin is the one a user would state: **adding context to a
query must not evict the node the query names.**
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._coverage_admission import or_fallback_sort_key, subject_label_exact_miss
from weld.graph import Graph

#: ADR 0050 requires every edge producer to stamp a confidence rank.
_DEFINITE = {"confidence": "definite", "source_strategy": "test_fixture"}

_TARGET = "file:pkg/tests/widget_examples_test"


def _graph(tmp: str) -> Graph:
    """One node named for the subject, two that merely overlap the other tokens."""
    graph = Graph(Path(tmp))
    graph.load()
    graph.add_node(
        _TARGET, "file", "widget_examples_test",
        {"file": "pkg/tests/widget_examples_test.py"},
    )
    # Decoys: each carries BOTH generic tokens but never the subject, so each
    # outranks the target on group count alone.
    graph.add_node(
        "symbol:py:pkg.audit:IntegrationDiscoverTests", "symbol",
        "IntegrationDiscoverTests",
        {"qualname": "pkg.audit.IntegrationDiscoverTests", "file": "pkg/audit.py",
         "description": "discovery integration coverage"},
    )
    graph.add_node(
        "file:pkg/tests/review_discover_integration_test", "file",
        "review_discover_integration_test",
        {"file": "pkg/tests/review_discover_integration_test.py"},
    )
    return graph


class OrFallbackSubjectLabelTest(unittest.TestCase):
    """ADR 0107 dimension 1: exact subject-label match leads the key."""

    def test_adding_context_does_not_evict_the_named_node(self) -> None:
        """The filed defect, stated as the user would state it."""
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            alone = graph.query("widget_examples_test", limit=10)
            self.assertEqual([_TARGET], [m["id"] for m in alone["matches"]])

            with_context = graph.query(
                "widget_examples_test discover integration", limit=10,
            )
            # Precondition: this is the degraded path, i.e. the ranking tier
            # under test. If strict-AND started matching, this test would be
            # silently exercising something else.
            self.assertEqual("or_fallback", with_context.get("degraded_match"))
            ids = [m["id"] for m in with_context["matches"]]
            self.assertIn(_TARGET, ids, "the node the query names was evicted")
            self.assertEqual(_TARGET, ids[0], "generic overlap outranked an exact name")

    def test_dimension_is_inert_without_an_exact_label_match(self) -> None:
        """No candidate named by the subject -> group count still leads.

        This is what keeps ADR 0075's substring-driven goldens from moving.
        """
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            ids = [
                m["id"] for m in graph.query("discover integration", limit=10)["matches"]
            ]
            self.assertTrue(ids)
            self.assertNotEqual(_TARGET, ids[0])

    def test_exact_equality_not_substring(self) -> None:
        """A label merely *containing* the subject does not earn the lead."""
        node = {"label": "widget_examples_test_helper"}
        self.assertEqual(1, subject_label_exact_miss(node, [["widget_examples_test"]]))
        self.assertEqual(
            0, subject_label_exact_miss(
                {"label": "widget_examples_test"}, [["widget_examples_test"]],
            ),
        )

    def test_only_the_subject_group_counts(self) -> None:
        """A label equal to a *trailing* token must not dominate.

        Without this narrowing a node labelled ``main`` or ``cli`` would lead
        every query that mentions those words.
        """
        node = {"label": "integration"}
        self.assertEqual(
            1, subject_label_exact_miss(node, [["widget_examples_test"], ["integration"]]),
        )

    def test_case_is_folded(self) -> None:
        """Query tokens arrive lowered; a capitalised label must still match."""
        self.assertEqual(
            0, subject_label_exact_miss({"label": "Widget"}, [["widget"]]),
        )

    def test_synonyms_of_the_subject_count(self) -> None:
        """An expanded alternate spelling of the subject is still the subject."""
        self.assertEqual(
            0, subject_label_exact_miss({"label": "config"}, [["configuration", "config"]]),
        )

    def test_degenerate_inputs_do_not_earn_the_lead(self) -> None:
        """No subject, or no label, must never sort as an exact match."""
        self.assertEqual(1, subject_label_exact_miss({"label": "x"}, []))
        self.assertEqual(1, subject_label_exact_miss({}, [["x"]]))
        self.assertEqual(1, subject_label_exact_miss({"label": ""}, [[""]]))

    def test_sort_key_places_the_dimension_first(self) -> None:
        """The ordering claim itself: exact label beats a strictly higher group count."""
        groups = [["widget"], ["discover"]]
        named = or_fallback_sort_key(
            "file:a", {"label": "widget"}, 1, groups, bm25_score=0.0,
        )
        overlapping = or_fallback_sort_key(
            "file:b", {"label": "other"}, 2, groups, bm25_score=99.0,
        )
        self.assertLess(named, overlapping)

    def test_group_count_still_leads_among_equals(self) -> None:
        """Dimension 2 is unchanged when dimension 1 ties."""
        groups = [["widget"], ["discover"]]
        more = or_fallback_sort_key("file:a", {"label": "x"}, 2, groups, bm25_score=0.0)
        fewer = or_fallback_sort_key("file:b", {"label": "y"}, 1, groups, bm25_score=99.0)
        self.assertLess(more, fewer)


if __name__ == "__main__":
    unittest.main()
