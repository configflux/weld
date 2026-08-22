"""bd ek4y: summary-only match demotion.

ADR 0124 gave Go/Rust ``symbol:`` nodes ``props.summary`` from their own doc
comments; the field is unconditionally part of the match surface (ADR 0114),
so a doc comment that merely *mentions* another symbol's name in passing
(e.g. "Area returns pi * r^2, satisfying shapes.Shape") made the mentioning
node a same-strength single-token candidate as the node the sentence is
actually about. This pins the mechanical demotion
(:func:`weld._summary_match.summary_only_match_demotion`) that fixed the
bundled Go tier1 fixture's criterion-7 F1 regression (0.8927 -> 1.0), plus
its wiring into both shared strict-AND sort keys (:mod:`weld.ranking`'s
in-memory path and :mod:`weld._rank_strict_and`'s sqlite/federation path) so
the three query backends cannot drift on this dimension the way ADR 0113
warned about.
"""

from __future__ import annotations

import unittest

from weld._rank_strict_and import strict_and_sort_key
from weld._summary_match import summary_only_match_demotion
from weld.ranking import rank_query_matches
from weld.synonyms import expand_token_groups


def _node(
    node_id: str,
    label: str,
    *,
    summary: str = "",
    file: str = "",
    qualname: str = "",
) -> dict:
    return {
        "id": node_id,
        "type": "symbol",
        "label": label,
        "props": {
            "summary": summary,
            "file": file,
            "qualname": qualname or label,
            "authority": "derived",
            "confidence": "definite",
        },
    }


class SummaryOnlyMatchDemotionTest(unittest.TestCase):
    """Direct unit contract for the pure predicate."""

    def test_no_summary_is_never_demoted(self) -> None:
        node = _node("symbol:go:pkg:Circle", "Circle")
        groups = expand_token_groups(["circle"])
        self.assertEqual(summary_only_match_demotion(node, groups), 0)

    def test_summary_only_match_is_demoted(self) -> None:
        """The measured Area/Shape shape: mentions another symbol's name.

        ``Area`` (a geometry-package method) is not itself named "Shape" and
        carries no "shape" token in id/label/file/qualname -- its summary is
        the only reason it would ever be a candidate for a "Shape" query.
        """
        area = _node(
            "symbol:go:geometry.geometry:Area",
            "Area",
            summary="Area returns pi * r^2, satisfying shapes.Shape.",
            file="geometry/geometry.go",
        )
        groups = expand_token_groups(["shape"])
        self.assertEqual(summary_only_match_demotion(area, groups), 1)

    def test_independent_support_is_not_demoted_even_with_a_matching_summary(
        self,
    ) -> None:
        """A node whose id/label ALSO carries the token is unaffected by
        whatever its summary additionally says."""
        shape = _node(
            "symbol:go:shapes.shapes:Shape",
            "Shape",
            summary="Shape is the interface every concrete shape satisfies.",
            file="shapes/shapes.go",
        )
        groups = expand_token_groups(["shape"])
        self.assertEqual(summary_only_match_demotion(shape, groups), 0)

    def test_file_field_is_independent_support(self) -> None:
        """A node matching via a non-summary field (here: file path) is not
        demoted just because its summary happens to also carry the token."""
        node = _node(
            "symbol:go:shapes.shapes:Base",
            "Base",
            summary="Base is an embeddable struct.",
            file="shapes/shapes.go",
        )
        groups = expand_token_groups(["shapes"])
        self.assertEqual(summary_only_match_demotion(node, groups), 0)

    def test_empty_summary_short_circuits(self) -> None:
        node = _node("symbol:go:pkg:Thing", "Thing", summary="")
        groups = expand_token_groups(["thing"])
        self.assertEqual(summary_only_match_demotion(node, groups), 0)

    def test_inert_on_multi_token_queries(self) -> None:
        """A multi-token query already requires every group to hit somewhere;
        letting summary carry one of several groups is ADR 0114/0116's
        deliberate behaviour, not the bug this dimension fixes."""
        area = _node(
            "symbol:go:geometry.geometry:Area",
            "Area",
            summary="Area returns pi * r^2, satisfying shapes.Shape.",
        )
        groups = expand_token_groups(["shape", "returns"])
        self.assertEqual(summary_only_match_demotion(area, groups), 0)

    def test_synonym_expanded_token_still_counts_as_the_one_group(self) -> None:
        """bd ek4y's query-test/FormatLabel shape: the query token "test"
        synonym-expands to "fixture", and a
        node whose summary mentions "fixture" only in a meta,
        self-referential sense is still a single token GROUP (not multiple
        groups), so the dimension must still fire."""
        node = _node(
            "symbol:go:shapes.shapes:FormatLabel",
            "FormatLabel",
            summary=(
                "FormatLabel trims a label. It is a free function so the "
                "fixture exercises a non-method symbol."
            ),
            file="shapes/shapes.go",
        )
        groups = expand_token_groups(["test"])
        self.assertEqual(len(groups), 1, "single typed token stays one group")
        self.assertEqual(summary_only_match_demotion(node, groups), 1)


class RankQueryMatchesIntegrationTest(unittest.TestCase):
    """End-to-end: the demotion actually reorders ``rank_query_matches``."""

    def test_summary_only_candidate_sorts_after_independent_matches(self) -> None:
        from weld.bm25 import BM25Corpus

        shape = _node(
            "symbol:go:shapes.shapes:Shape",
            "Shape",
            summary="Shape is the interface every concrete shape satisfies.",
        )
        area = _node(
            "symbol:go:geometry.geometry:Area",
            "Area",
            summary="Area returns pi * r^2, satisfying shapes.Shape.",
        )
        matches = [(area["id"], area), (shape["id"], shape)]
        groups = expand_token_groups(["shape"])
        bm25 = BM25Corpus.from_nodes({nid: node for nid, node in matches})
        ranked = rank_query_matches(matches, groups, bm25, {})
        self.assertEqual(
            [nid for nid, _ in ranked],
            [shape["id"], area["id"]],
            "the exact symbol match must lead; the summary-only mention "
            "of 'Shape' must not outrank it",
        )


class StrictAndSortKeyParityTest(unittest.TestCase):
    """The sqlite/federation shared key must carry the same dimension.

    ADR 0113 recorded that a dimension added to one strict-AND key and not
    its sibling is a drift defect a dogfood report finds three issues
    later, not a gate. Pinning both keys directly here (in addition to the
    corpus-level ``weld_query_backend_parity_test``) keeps this specific
    dimension from being that defect.
    """

    def test_summary_only_match_is_demoted_in_the_shared_strict_and_key(
        self,
    ) -> None:
        area = _node(
            "symbol:go:geometry.geometry:Area",
            "Area",
            summary="Area returns pi * r^2, satisfying shapes.Shape.",
        )
        shape = _node(
            "symbol:go:shapes.shapes:Shape",
            "Shape",
            summary="Shape is the interface every concrete shape satisfies.",
        )
        groups = expand_token_groups(["shape"])
        area_key = strict_and_sort_key(area["id"], area, groups, 0.5)
        shape_key = strict_and_sort_key(shape["id"], shape, groups, 0.1)
        self.assertLess(
            shape_key, area_key,
            "the exact match must sort first even when the demoted "
            "summary-only node has a higher bm25 score",
        )


if __name__ == "__main__":
    unittest.main()
