"""bd cgj3: the eval corpus, answered by all three query impls, not just one.

``weld_query_corpus_gate_test`` runs every reported dogfood-gap query against
impl #1 -- the in-memory JSON ``Graph``. That is one of three code paths a real
``wd query`` can take. Impl #2 (:meth:`weld._sqlite_reader.SqliteBackedGraph.
query`, the sidecar path) and impl #3 (:meth:`weld._federation_eager_index.
EagerFederationIndex.query_child_matches`, the per-child federated path) answer
the same question from the same graph, and a corpus that never asks them cannot
notice when they stop agreeing.

They had already stopped. bd to8x demoted test material below the code it
covers, but wired the dimension into impl #1's ``rank_query_matches`` only; the
shared ``strict_and_sort_key`` the two child-repo impls rank by never got it.
So ``"graph json dump serializer"`` was led by a lint test on sqlite and
federation and by ``weld/serializer`` on the JSON path -- one query, one graph,
three answers. The corpus entry is pinned in :mod:`weld.tests.query_corpus`;
this module is what makes it a *three-backend* assertion.

What is asserted, and what deliberately is not
----------------------------------------------
Each backend must satisfy the same corpus contract -- the subject is
retrievable, no forbidden prefix leads, nothing goes silent -- and the three
must agree on which node leads. Full ranked-list equality is NOT asserted: the
three impls score differently. Impl #1 blends the ADR 0010 hybrid score while
impls #2/#3 rank on BM25 alone, and even those two disagree -- impl #2 counts
documents for IDF, impl #3 counts index tokens (bd ki4u). Pinning the whole
list would pin arithmetic that is currently allowed to differ, and would fail
for a reason that has nothing to do with rank *dimensions*. The lead and the
contract are what a caller actually reads, and they are what a missing
dimension moves.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld import _sqlite_reader as reader
from weld import _sqlite_writer as writer
from weld._federation_eager_index import EagerFederationIndex
from weld._rank_strict_and import strict_and_sort_key
from weld.graph import Graph
from weld.serializer import dumps_graph
from weld.tests.query_corpus import CORPUS, fixture_nodes

#: The one child name the federated impl answers under. Arbitrary; the eager
#: index is keyed by child name and needs one to address the fixture.
_CHILD = "corpus"


class _Backends:
    """The corpus fixture, materialized once and readable by all three impls.

    One graph.json plus its paired sidecar, so the JSON path, the sqlite path
    and the federated path are answering from *the same bytes*. Building three
    separate fixtures would let a divergence hide as a fixture difference.
    """

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / ".weld").mkdir(parents=True, exist_ok=True)
        payload = {"meta": {"schema_version": 1}, "nodes": fixture_nodes(),
                   "edges": []}
        body = dumps_graph(payload).encode("utf-8")
        graph_path = root / ".weld" / "graph.json"
        graph_path.write_bytes(body)
        writer.build_sidecar_for_bytes(payload, body, root / ".weld" / "graph.db")
        self._json = Graph(root)
        self._json.load()
        view = reader.open_sidecar_if_fresh(graph_path)
        assert view is not None, "the sidecar must be fresh for its own graph"
        self._sqlite = view
        self._eager = EagerFederationIndex.build([(_CHILD, view)])
        assert _CHILD in self._eager.eager_children, (
            "the eager index must cover the fixture child, else impl #3 is "
            "silently untested"
        )

    def close(self) -> None:
        self._sqlite.close()
        self._tmp.cleanup()

    def ranked(self, name: str, query: str, limit: int = 20) -> list[str]:
        """Return the ranked node ids *name* answers *query* with."""
        if name == "json":
            matches = self._json.query(query, limit=limit)["matches"]
        elif name == "sqlite":
            matches = self._sqlite.query(query, limit=limit)["matches"]
        elif name == "federation":
            matches = self._eager.query_child_matches(
                self._sqlite, _CHILD, query, limit=limit
            )
        else:  # pragma: no cover -- programming error in this module
            raise AssertionError(f"unknown backend {name!r}")
        return [match["id"] for match in matches]


#: The three impls, named the way the issue and the module docstrings name them.
_BACKENDS = ("json", "sqlite", "federation")


class QueryCorpusBackendParityTest(unittest.TestCase):
    """Every corpus entry, asserted against every backend."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._backends = _Backends()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._backends.close()

    def test_every_backend_retrieves_every_corpus_subject(self) -> None:
        """``must_contain`` is a property of the graph, not of the reader."""
        for entry in CORPUS:
            for backend in _BACKENDS:
                with self.subTest(bd=entry["bd"], backend=backend):
                    ranked = self._backends.ranked(backend, entry["query"])
                    for node_id in entry["must_contain"]:
                        self.assertIn(
                            node_id, ranked,
                            f"bd {entry['bd']}: {entry['query']!r} lost "
                            f"{node_id} on the {backend} path.\n"
                            f"{entry['why']}\nGot: {ranked[:10]}",
                        )

    def test_no_backend_ranks_a_forbidden_node_first(self) -> None:
        """The demotion contract, asserted on all three impls.

        This is the assertion bd cgj3 was filed for: before the shared
        strict-AND key carried ``test_noise_demotion``, the to8x entry was led
        by ``symbol:py:tools.lint_terminal_safety_test:...`` on sqlite and
        federation while the JSON path led with the serializer.
        """
        for entry in CORPUS:
            for backend in _BACKENDS:
                with self.subTest(bd=entry["bd"], backend=backend):
                    ranked = self._backends.ranked(backend, entry["query"])
                    if not ranked:
                        continue
                    for prefix in entry["must_not_rank_first"]:
                        self.assertFalse(
                            ranked[0].startswith(prefix),
                            f"bd {entry['bd']}: {entry['query']!r} is led by "
                            f"{ranked[0]} on the {backend} path, which starts "
                            f"with {prefix!r}.\n{entry['why']}\n"
                            f"Got: {ranked[:10]}",
                        )

    def test_no_backend_goes_silent(self) -> None:
        """An empty envelope on one backend and not another is still a bug."""
        for entry in CORPUS:
            for backend in _BACKENDS:
                with self.subTest(bd=entry["bd"], backend=backend):
                    self.assertTrue(
                        self._backends.ranked(backend, entry["query"]),
                        f"bd {entry['bd']}: {entry['query']!r} returned "
                        f"nothing on the {backend} path.",
                    )

    def test_no_backend_turns_the_demotion_into_a_filter(self) -> None:
        """Demoted material is re-ranked on every impl, never dropped.

        The risk this change carries: a demotion added to a shared key could
        have been read as an exclusion, and the query that wants the test would
        then get nothing. So the lint test the to8x entry demotes must still be
        in the answer on all three paths -- below the serializer, but present.
        """
        entry = next((e for e in CORPUS if e["bd"] == "to8x"), None)
        self.assertIsNotNone(
            entry, "the to8x entry is what this assertion reads; if it moved, "
            "point this test at its replacement rather than deleting it",
        )
        demoted = (
            "symbol:py:tools.lint_terminal_safety_test:"
            "JsonSerializerBoundaryTest.test_unwrapped_graph_dump_is_flagged"
        )
        for backend in _BACKENDS:
            with self.subTest(backend=backend):
                ranked = self._backends.ranked(backend, entry["query"])
                self.assertIn(
                    demoted, ranked,
                    f"the {backend} path dropped the demoted test instead of "
                    f"re-ranking it: {ranked}",
                )
                self.assertGreater(
                    ranked.index(demoted), ranked.index("file:weld/serializer"),
                    f"the {backend} path kept the test but did not demote it: "
                    f"{ranked}",
                )

    def test_every_backend_is_led_by_the_pinned_answer(self) -> None:
        """``must_lead`` entries pin the outright rank-1 id, on all three impls.

        The agreement test below only proves the three impls agree with EACH
        OTHER; it would pass just as happily if all three agreed on the wrong
        node. Where a corpus entry names the unambiguous right answer, assert
        it directly -- bd dyam is the first user (the ranking half of bd
        9ucf: a generic verb outranked the rare subject token on every
        tie-break dimension until the subject dimensions learned to read a
        node's own module summary).
        """
        for entry in CORPUS:
            must_lead = entry.get("must_lead")
            if must_lead is None:
                continue
            for backend in _BACKENDS:
                with self.subTest(bd=entry["bd"], backend=backend):
                    ranked = self._backends.ranked(backend, entry["query"])
                    self.assertTrue(
                        ranked,
                        f"bd {entry['bd']}: {entry['query']!r} returned "
                        f"nothing on the {backend} path.",
                    )
                    self.assertEqual(
                        must_lead, ranked[0],
                        f"bd {entry['bd']}: {entry['query']!r} must lead with "
                        f"{must_lead} on the {backend} path, got {ranked[0]}.\n"
                        f"{entry['why']}\nGot: {ranked[:10]}",
                    )

    def test_the_three_backends_agree_on_the_leading_result(self) -> None:
        """Same query, same graph, same top answer -- whichever impl replied.

        Rank 1 is what a caller reads first and what an agent acts on, so it
        is the parity that matters. Ranks below it are allowed to differ where
        the impls' scoring legitimately differs (see the module docstring).
        """
        for entry in CORPUS:
            with self.subTest(bd=entry["bd"], query=entry["query"]):
                leads = {
                    backend: self._backends.ranked(backend, entry["query"])[:1]
                    for backend in _BACKENDS
                }
                distinct = {tuple(lead) for lead in leads.values()}
                self.assertEqual(
                    len(distinct), 1,
                    f"bd {entry['bd']}: {entry['query']!r} leads with a "
                    f"different node depending on the backend: {leads}\n"
                    f"{entry['why']}",
                )


class SharedStrictAndKeyDemotionTest(unittest.TestCase):
    """The shared key's demotion dimension, asserted behaviourally.

    Grepping :mod:`weld._rank_strict_and` for the predicate name would prove it
    is mentioned, not that it outranks the BM25 score -- and placement is the
    whole decision here. So these compare real keys instead.
    """

    _GROUPS = [["dump", "dumps"], ["graph", "graphs"]]

    @staticmethod
    def _node(file_path: str) -> dict:
        return {
            "type": "symbol", "label": "dumps_graph",
            "props": {"authority": "derived", "confidence": "definite",
                      "file": file_path, "roles": ["implementation"]},
        }

    def test_a_test_node_sorts_after_its_subject_despite_a_higher_score(
        self,
    ) -> None:
        """The dimension must sit AHEAD of ``-bm25``, not behind it.

        The test is handed the higher BM25 score on purpose: that is the real
        asymmetry (a test names its subject in a sentence, the subject names
        itself in a word), and a demotion placed after the score would lose
        to it.
        """
        subject = strict_and_sort_key(
            "symbol:py:weld.serializer:dumps_graph",
            self._node("weld/serializer.py"), self._GROUPS, 1.0,
        )
        test_noise = strict_and_sort_key(
            "symbol:py:tools.lint_terminal_safety_test:T.test_graph_dump",
            self._node("tools/lint_terminal_safety_test.py"),
            self._GROUPS, 9.0,
        )
        self.assertLess(subject, test_noise)

    def test_the_demotion_is_off_when_the_query_names_tests(self) -> None:
        """Asking for tests must still rank them by score alone."""
        groups = [["test", "spec"], ["graph", "graphs"]]
        subject = strict_and_sort_key(
            "symbol:py:weld.serializer:dumps_graph",
            self._node("weld/serializer.py"), groups, 1.0,
        )
        test_noise = strict_and_sort_key(
            "symbol:py:tools.lint_terminal_safety_test:T.test_graph_dump",
            self._node("tools/lint_terminal_safety_test.py"), groups, 9.0,
        )
        self.assertLess(test_noise, subject)

    def test_the_key_has_the_documented_dimension_shape(self) -> None:
        """Arity and slot positions, so a dimension cannot go missing quietly.

        This pins the shape the module docstring describes; that the shape
        *agrees with impl #1* is what the corpus-level lead-agreement test
        above proves, since only a real query exercises both keys at once.
        """
        key = strict_and_sort_key(
            "symbol:py:weld.serializer:dumps_graph",
            self._node("weld/serializer.py"), self._GROUPS, 1.0,
        )
        self.assertEqual(len(key), 11, key)
        # slot 2: summary_only_match_demotion -- inert here (self._GROUPS is
        # a 2-group query; the dimension only ever fires for exactly 1 group).
        self.assertEqual(key[2], 0, "inert outside single-token queries")
        # slots 4 and 5: partial_coverage_subject_miss, test_noise_demotion.
        self.assertEqual(key[4], 0, "a strict-AND match is never a partial")
        self.assertEqual(key[5], 0, "a non-test node is not demoted")
        self.assertEqual(key[6], 0, "a non-concept node is not demoted")
        self.assertEqual(key[7], -1.0, "the score follows the demotions")


if __name__ == "__main__":
    unittest.main()
