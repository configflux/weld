"""bd 2gvr: the query-quality eval corpus, run as a standing gate.

Nine dogfood-gap issues each carried an exact query and the answer its reporter
actually needed. Re-running those queries by hand after a fix proves nothing --
bd 9ucf showed that *filing* the issue mints a ``concept:`` node from its title
which can answer the query cosmetically, so a reported query can look fixed
while the ranking it was about is untouched, and any later regression stays
hidden behind that node.

So the queries are pinned here instead, against a fixture graph that carries
those same concept nodes on purpose (see :mod:`weld.tests.query_corpus`). Every
entry runs under the adversarial condition, and the ranking assertion is that
the issue's own restatement must not lead.

The gate asserts contract shape -- this node is retrievable, no concept node
leads -- rather than rank arithmetic, so it fails when retrieval breaks and not
when a BM25 weight moves by 0.001.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from weld.graph import Graph
from weld.tests.query_corpus import CORPUS, fixture_nodes


def _write_graph(tmp: Path) -> Path:
    """Materialize the shared corpus fixture as a Mode-A graph on disk."""
    weld_dir = tmp / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps({"nodes": fixture_nodes(), "edges": []}), encoding="utf-8"
    )
    return tmp


class QueryCorpusGateTest(unittest.TestCase):
    """One reported query per assertion, all against one fixture graph."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = _write_graph(Path(cls._tmp.name))
        cls._graph = Graph(root)
        cls._graph.load()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _ranked_ids(self, query: str) -> list[str]:
        result = self._graph.query(query, limit=20)
        return [match["id"] for match in result["matches"]]

    def test_corpus_entries_retrieve_their_subject(self) -> None:
        """Every ``must_contain`` id is reachable for its reported query."""
        for entry in CORPUS:
            with self.subTest(bd=entry["bd"], query=entry["query"]):
                ranked = self._ranked_ids(entry["query"])
                for node_id in entry["must_contain"]:
                    self.assertIn(
                        node_id,
                        ranked,
                        f"bd {entry['bd']}: {entry['query']!r} lost {node_id}.\n"
                        f"{entry['why']}\nGot: {ranked[:10]}",
                    )

    def test_corpus_entries_do_not_rank_forbidden_nodes_first(self) -> None:
        """No entry may be led by a node its ``must_not_rank_first`` forbids.

        This is the bd 9ucf guard. A ``concept:`` lead means an issue title is
        answering the query it reports -- the exact reading that makes a broken
        query look fixed.
        """
        for entry in CORPUS:
            with self.subTest(bd=entry["bd"], query=entry["query"]):
                ranked = self._ranked_ids(entry["query"])
                if not ranked:
                    continue
                for prefix in entry["must_not_rank_first"]:
                    self.assertFalse(
                        ranked[0].startswith(prefix),
                        f"bd {entry['bd']}: {entry['query']!r} is led by "
                        f"{ranked[0]}, which starts with {prefix!r}.\n"
                        f"{entry['why']}\nGot: {ranked[:10]}",
                    )

    def test_corpus_entries_are_led_by_their_pinned_answer(self) -> None:
        """``must_lead`` entries assert the outright rank-1 id, not just a prefix.

        Optional and used only where the reported answer is unambiguous --
        most entries key off ``must_not_rank_first`` instead. bd dyam is the
        first: the ranking (not retrieval) half of bd 9ucf, where a generic
        verb outranked the rare subject token on tie-break dimensions alone.
        """
        for entry in CORPUS:
            must_lead = entry.get("must_lead")
            if must_lead is None:
                continue
            with self.subTest(bd=entry["bd"], query=entry["query"]):
                ranked = self._ranked_ids(entry["query"])
                self.assertTrue(ranked, f"bd {entry['bd']}: {entry['query']!r} returned nothing.")
                self.assertEqual(
                    must_lead, ranked[0],
                    f"bd {entry['bd']}: {entry['query']!r} must lead with "
                    f"{must_lead}, got {ranked[0]}.\n"
                    f"{entry['why']}\nGot: {ranked[:10]}",
                )

    def test_every_corpus_query_returns_something(self) -> None:
        """No reported query may go silent.

        An empty envelope is the one outcome worse than a badly ranked one: it
        gives the caller nothing to distrust.
        """
        for entry in CORPUS:
            with self.subTest(bd=entry["bd"], query=entry["query"]):
                self.assertTrue(
                    self._ranked_ids(entry["query"]),
                    f"bd {entry['bd']}: {entry['query']!r} returned nothing.",
                )

    def test_concept_only_match_does_not_suppress_the_code(self) -> None:
        """ADR 0113 candidacy, stated directly rather than via a corpus entry.

        The mechanism the corpus exists to hold: a query whose ONLY strict-AND
        match is an issue-derived concept node must still reach the code. Before
        ADR 0113 this returned a single match -- the issue's own title -- and
        the OR fallback that finds the code never ran, because strict-AND had
        "succeeded".
        """
        ranked = self._ranked_ids("tree-sitter availability gate")
        self.assertGreater(
            len(ranked), 1,
            "an all-concept strict-AND result must relax to the OR fallback, "
            f"not return the issue title alone; got {ranked}",
        )
        self.assertIn(
            "file:weld/strategies/tree_sitter", ranked,
            f"the subject the reporter needed is still unreachable: {ranked}",
        )

    def test_backlog_query_still_reaches_its_concept(self) -> None:
        """The demotion is a re-rank, not a filter.

        A query that names the backlog must still get the issue node, otherwise
        ADR 0113 would have traded one blind spot for another.
        """
        ranked = self._ranked_ids("bd issue tree-sitter availability gate")
        self.assertTrue(
            any(node_id.startswith("concept:") for node_id in ranked),
            f"naming the backlog must still surface the concept node: {ranked}",
        )


class ReferencesErrorShapeTest(unittest.TestCase):
    """bd hp6e: a node id that names nothing is an error, not 'no references'.

    ``wd references`` printed "no references" and exited 0 for an id that does
    not exist, while ``wd callers`` and ``wd context`` both exited nonzero for
    the same id. The disagreeing verb was the one whose answer a reader acts on:
    "no references" is what you see before deleting a symbol as dead, and a
    typo, a stale pasted id, or a symbol that has since MOVED all rendered as
    "nothing uses this".
    """

    _MISSING = "symbol:py:weld.totally_made_up:no_such_symbol_at_all"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = _write_graph(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *argv: str) -> tuple[int, str]:
        """Invoke the CLI in-process; return ``(exit_code, stdout)``.

        In-process rather than ``python -m weld``: under Bazel the runfiles
        tree has no executable ``weld.__main__``, and the exit code is the
        whole subject of this test, so it has to come from the real dispatch
        rather than a subprocess that never started. ``main`` signals success
        by returning and failure via ``SystemExit``, so a clean return is 0.
        """
        from weld._graph_cli import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                main(["--root", str(self._root), *argv])
            except SystemExit as exc:
                code = exc.code
                return (code if isinstance(code, int) else 1), buffer.getvalue()
        return 0, buffer.getvalue()

    def test_references_exits_nonzero_for_a_missing_node_id(self) -> None:
        code, out = self._run("references", self._MISSING)
        self.assertNotEqual(
            code, 0, f"references must fail like callers does; stdout={out}"
        )
        self.assertIn("not found", out)

    def test_references_agrees_with_callers_on_a_missing_node_id(self) -> None:
        """The three read verbs must not disagree about whether a node exists."""
        references_code, _ = self._run("references", self._MISSING)
        callers_code, _ = self._run("callers", self._MISSING)
        self.assertEqual(
            references_code != 0, callers_code != 0,
            "references and callers disagree about a nonexistent node: "
            f"references rc={references_code}, callers rc={callers_code}",
        )

    def test_references_json_carries_the_structured_error_code(self) -> None:
        _, out = self._run("references", self._MISSING, "--json")
        self.assertEqual(json.loads(out).get("error_code"), "node_not_found")

    def test_a_resolvable_name_still_succeeds(self) -> None:
        """The error path must not swallow the ordinary answer."""
        code, out = self._run("references", "Tool")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
