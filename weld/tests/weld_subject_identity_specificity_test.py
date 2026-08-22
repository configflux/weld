"""Regression guard for the ADR 0119 separator-widening fallout (bd ght0).

bd 2xoj (ADR 0119) widened ``_separator_variants`` from a narrow ``-``/``_``
swap to the index's whole separator alphabet (``/:.·-_``), so a query
token spelled with a dot (``graph.json``) now also reaches a node spelled
with an underscore (``graph_json``). That is a *retrieval* fix -- the node
becomes a candidate that used to be unreachable -- but
:func:`weld._rank_subject.subject_identity_miss` treated the resulting
variant-only hit as equally strong *identity* evidence as the user's own raw
spelling, in the OR-fallback tier's subject tie-break. Live measurement (bd
ght0) found this let seven ``graph_json``-shaped symbols that only
read/locate/check ``graph.json`` (``find_graph_json``, ``_check_graph_json``,
``graph_json_path``, ``sidecar_freshness``, ``sidecar_path_for``,
``compute_source_json_sha``) tie ``weld/serializer`` -- the write funnel bd
dyam pinned at rank 1 -- purely because their ids contain the re-spelled
substring, and BM25 then broke the tie on term density, favoring the short
symbol ids over the correct, longer answer.

An eighth competitor (``weld.doctor._check_graph_json``) ties on a *genuine*
raw hit (its own one-line docstring literally says "graph.json"), which the
tier alone cannot separate from ``weld/serializer`` -- both are tier 0. That
is what :func:`weld._rank_subject.subject_identity_specificity` is for: BM25
scores a node's entire indexed text (exports, headings, constants included),
so a whole-file node is diluted relative to a short function whose one-line
docstring happens to name the same subject, even when the file's own
docstring states the subject as its entire purpose. Comparing only the
matched identity field's length sidesteps that dilution.

This fixture reproduces the shape with synthetic, hermetic names (``pkg``/
``payload.json``) rather than the live repo's real modules, so the guard is
deterministic and immune to graph drift -- the live-repo instance of the same
collision is separately guarded, with the real node ids, by the bd
9ucf/dyam entry in :mod:`weld.tests.query_corpus` (``must_lead``).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._rank_subject import (
    _subject_identity_match,
    subject_identity_miss,
    subject_identity_specificity,
)
from weld.graph import Graph
from weld.synonyms import expand_token_groups

_QUERY = "where is payload.json written"

_WRITER = "file:pkg/writer"
_CHECK_LONG = "symbol:pkg.checks:_check_payload_json"
_FIND_VARIANT = "symbol:pkg.io:find_payload_json"
_PATH_VARIANT = "symbol:pkg.io:PayloadStore.payload_json_path"
_FRESHNESS_VARIANT = "symbol:pkg.io:sidecar_freshness"
_WRITTEN_ONLY = "symbol:pkg.util:written_flag"


def _graph(tmp: str) -> Graph:
    """The write funnel plus its real-world competitor shapes (bd ght0).

    * ``_WRITER`` is the correct answer: a raw hit, and the SHORTEST one.
    * ``_CHECK_LONG`` is a genuine raw hit too (its own docstring says
      "payload.json"), but a longer one -- it must still lose, to
      ``subject_identity_specificity`` rather than to the tier alone.
    * ``_FIND_VARIANT`` / ``_PATH_VARIANT`` / ``_FRESHNESS_VARIANT`` never say
      "payload.json" anywhere; they are reachable ONLY because bd 2xoj widened
      separator-variant matching to reach ``payload_json``. They must lose to
      the tier itself (tier 1 sorts after tier 0), before specificity or BM25
      ever run.
    * ``_WRITTEN_ONLY`` hits the query's other group ("written") by name
      alone and never mentions the subject at all (tier 2) -- the
      already-established defect bd dyam fixed, included so the fixture
      exercises all three tiers at once.
    """
    graph = Graph(Path(tmp))
    graph.load()
    graph.add_node(
        _WRITER, "file", "writer",
        {"file": "pkg/writer.py", "summary": "Canonical writer for ``payload.json``."},
    )
    graph.add_node(
        _CHECK_LONG, "symbol", "_check_payload_json",
        {"file": "pkg/checks.py", "qualname": "_check_payload_json",
         "summary": "Report payload.json presence and schema validity for diagnostics."},
    )
    graph.add_node(
        _FIND_VARIANT, "symbol", "find_payload_json",
        {"file": "pkg/io.py", "qualname": "find_payload_json"},
    )
    graph.add_node(
        _PATH_VARIANT, "symbol", "PayloadStore.payload_json_path",
        {"file": "pkg/io.py", "qualname": "PayloadStore.payload_json_path"},
    )
    graph.add_node(
        _FRESHNESS_VARIANT, "symbol", "sidecar_freshness",
        {"file": "pkg/io.py", "qualname": "sidecar_freshness",
         "summary": "Return freshness paired with *payload_json_path*."},
    )
    graph.add_node(
        _WRITTEN_ONLY, "symbol", "written_flag",
        {"file": "pkg/util.py", "qualname": "written_flag"},
    )
    return graph


def _ranked_ids(graph: Graph, query: str, limit: int = 20) -> list[str]:
    return [m["id"] for m in graph.query(query, limit=limit)["matches"]]


class SubjectIdentityMatchTierTest(unittest.TestCase):
    """Direct unit coverage for the 0/1/2 tier (ADR 0120)."""

    def setUp(self) -> None:
        self.groups = expand_token_groups(_QUERY.split())

    def test_raw_token_hit_is_tier_zero(self) -> None:
        node = {"props": {"summary": "Canonical writer for ``payload.json``."}}
        tier, field = _subject_identity_match(_WRITER, node, self.groups)
        self.assertEqual(tier, 0)
        self.assertEqual(field, "canonical writer for ``payload.json``.")

    def test_separator_variant_only_hit_is_tier_one(self) -> None:
        """The bd 2xoj / bd ght0 case: reachable only via a re-spelling.

        Both ``nid`` and ``qualname`` carry the variant here; the shortest
        (``qualname``) is the one :func:`_subject_identity_match` reports.
        """
        node = {"props": {"qualname": "find_payload_json"}}
        tier, field = _subject_identity_match(_FIND_VARIANT, node, self.groups)
        self.assertEqual(tier, 1)
        self.assertEqual(field, "find_payload_json")

    def test_true_miss_is_tier_two(self) -> None:
        node = {"props": {"qualname": "written_flag"}}
        tier, field = _subject_identity_match(_WRITTEN_ONLY, node, self.groups)
        self.assertEqual(tier, 2)
        self.assertIsNone(field)

    def test_subject_identity_miss_returns_the_tier_verbatim(self) -> None:
        hit = {"props": {"summary": "Canonical writer for ``payload.json``."}}
        variant = {"props": {"qualname": "find_payload_json"}}
        miss = {"props": {"qualname": "written_flag"}}
        self.assertEqual(subject_identity_miss(_WRITER, hit, self.groups), 0)
        self.assertEqual(subject_identity_miss(_FIND_VARIANT, variant, self.groups), 1)
        self.assertEqual(subject_identity_miss(_WRITTEN_ONLY, miss, self.groups), 2)

    def test_single_token_query_is_inert(self) -> None:
        groups = expand_token_groups(["payload.json"])
        node = {"props": {"qualname": "find_payload_json"}}
        self.assertEqual(subject_identity_miss(_FIND_VARIANT, node, groups), 0)


class SubjectIdentitySpecificityTest(unittest.TestCase):
    """Direct unit coverage for the length-of-matched-field tie-break."""

    def setUp(self) -> None:
        self.groups = expand_token_groups(_QUERY.split())

    def test_shorter_identity_field_scores_lower(self) -> None:
        """Ascending sort: the more concise statement of the subject wins."""
        short = {"props": {"summary": "Canonical writer for ``payload.json``."}}
        long = {"props": {
            "summary": "Report payload.json presence and schema validity for diagnostics.",
        }}
        short_score = subject_identity_specificity(_WRITER, short, self.groups)
        long_score = subject_identity_specificity(_CHECK_LONG, long, self.groups)
        self.assertLess(short_score, long_score)

    def test_inert_for_a_tier_two_miss(self) -> None:
        node = {"props": {"qualname": "written_flag"}}
        self.assertEqual(
            subject_identity_specificity(_WRITTEN_ONLY, node, self.groups), 0,
        )

    def test_inert_for_single_token_query(self) -> None:
        groups = expand_token_groups(["payload.json"])
        node = {"props": {"summary": "Canonical writer for ``payload.json``."}}
        self.assertEqual(subject_identity_specificity(_WRITER, node, groups), 0)


class PayloadJsonWrittenRegressionTest(unittest.TestCase):
    """Headline golden: the write funnel leads, every competitor shape loses."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.graph = _graph(self._tmp.name)
        self.groups = expand_token_groups(_QUERY.split())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fixture_reproduces_the_collision(self) -> None:
        """Guard the fixture itself: all three tiers must actually be live."""
        nodes = self.graph._data["nodes"]
        self.assertEqual(
            _subject_identity_match(_WRITER, nodes[_WRITER], self.groups)[0], 0)
        self.assertEqual(
            _subject_identity_match(_CHECK_LONG, nodes[_CHECK_LONG], self.groups)[0], 0)
        for variant_id in (_FIND_VARIANT, _PATH_VARIANT, _FRESHNESS_VARIANT):
            self.assertEqual(
                _subject_identity_match(variant_id, nodes[variant_id], self.groups)[0],
                1,
                f"{variant_id} must be reachable only via a separator variant",
            )
        self.assertEqual(
            _subject_identity_match(_WRITTEN_ONLY, nodes[_WRITTEN_ONLY], self.groups)[0],
            2,
        )

    def test_relaxes_to_or_fallback(self) -> None:
        """No node covers both groups -> OR-fallback, the durable clean-graph path."""
        result = self.graph.query(_QUERY, limit=20)
        self.assertEqual(result.get("degraded_match"), "or_fallback")

    def test_writer_leads(self) -> None:
        """The bd dyam contract, reproduced: the write funnel is rank 1."""
        ids = _ranked_ids(self.graph, _QUERY)
        self.assertEqual(_WRITER, ids[0], f"writer must lead; ranked ids {ids}")

    def test_longer_raw_hit_competitor_still_loses(self) -> None:
        """Tier alone cannot separate two tier-0 hits -- specificity must."""
        ids = _ranked_ids(self.graph, _QUERY)
        self.assertLess(ids.index(_WRITER), ids.index(_CHECK_LONG))

    def test_variant_only_competitors_rank_below_the_writer(self) -> None:
        ids = _ranked_ids(self.graph, _QUERY)
        for variant_id in (_FIND_VARIANT, _PATH_VARIANT, _FRESHNESS_VARIANT):
            self.assertLess(ids.index(_WRITER), ids.index(variant_id))

    def test_written_only_noise_ranks_last(self) -> None:
        """The pre-existing bd dyam defect (a generic verb match) stays fixed."""
        ids = _ranked_ids(self.graph, _QUERY)
        self.assertEqual(ids[-1], _WRITTEN_ONLY)


if __name__ == "__main__":
    unittest.main()
