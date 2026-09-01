"""Contract for the derived cannot-answer markers and the producers behind them.

Bd ``5038-fprr2``, and one file for the two ADR 0139 mechanisms that issue
carries -- a mechanism 3 instance living inside a mechanism 4 fix, in the
architect's ruling R8 recorded there. Mechanism 4 asks that
:data:`weld.tests._graph_invariants._CANNOT_ANSWER_MARKERS` be derived from
``weld/_errors.py`` rather than restated. R8's point is that derivation alone
does not make a skew *impossible*: the same claims have a second producer that
never reads those tables -- :func:`weld.impact_format.format_human` spells the
verdict itself, and
:func:`weld._impact_cannot_answer.uncomputable_repo_reason` writes its own
reason prose -- and mechanism 3 is the standing rule for that, a fact's second
surface owing a parity test fed by the real producers.

So this file holds two different things:

* :class:`DerivedFromTheContractTest` -- mechanism 4. What follows
  ``weld/_errors.py`` cannot be edited apart from it. Each load-bearing case
  rewrites a table entry and shows the derived thing move with it; a
  re-introduced literal would ignore that and fail. Two derivations live here:
  the marker set, and -- since bd ``5038-koqmb`` -- the production block
  :func:`weld._graph_cli.missing_graph_message` emits.
* :class:`ProducerParityTest` -- mechanism 3. Each second producer's own output,
  asserted *alone*, still carries what the contract derives. Every expected value
  is read from the other producer rather than spelled here, which is mechanism
  3's own boundary: "spelling the expected value twice is class 1 wearing a
  parity test's name". "Alone" is the rest of the design -- the CLI writes
  ``format_error_line`` to stderr for every refusal, so a test run against the
  combined streams would pass on that prefix no matter how far the renderer had
  drifted, and would be a parity assertion in name only.

The impact outcome every case reads comes from the real engine -- a ``repo:`` node
minted by :func:`weld.federation_root._build_repo_node`, scored by
:func:`weld.impact_core.impact` -- rather than from a hand-written envelope. That
is ADR 0139 mechanism 1, and the concrete reason for it here: a literal
``{"risk_level": "UNKNOWN", "cannot_answer": ...}`` would be this file asserting
against its own author's belief about the envelope, and the renderer could stop
being reachable at all without a case noticing.
"""

from __future__ import annotations

import importlib
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from weld import _errors
from weld._graph_cli import missing_graph_message
from weld._workspace_schema import ChildEntry
from weld.federation_root import _build_repo_node
from weld.graph import Graph
from weld.impact_cli import _cannot_answer_exit
from weld.impact_core import impact
from weld.impact_format import format_human
from weld.tests import _contract_markers, _graph_invariants
from weld.tests._graph_invariants import (
    assert_answered_empty,
    assert_cannot_answer,
)


def _repo_graph(root: Path, name: str = "alpha") -> tuple[Graph, str]:
    """A root graph holding one ``repo:`` node, minted by its own producer.

    No edges and no ``meta.cross_repo`` stamp: that is the state ADR 0134 calls
    uncomputable, and the only one that reaches the cannot-answer renderer.

    *root* is a real directory because
    :func:`weld._impact_cannot_answer._configured_strategies` opens the
    workspace config beside the graph to decide which of the two reason
    variants to write. Handing it an empty temporary directory rather than
    ``Path(".")`` is what keeps that a decision and not a reading of whatever
    the working directory happened to contain.
    """
    graph = Graph(root)
    node_id, body = _build_repo_node(
        ChildEntry(name=name, path=name, tags={}, remote=None)
    )
    graph.add_node(node_id, body["type"], body["label"], body["props"])
    return graph, node_id


def _uncomputable_result(name: str = "alpha") -> dict:
    """The impact envelope for a repo node whose dependents cannot be computed.

    The temporary root lives only for the ``impact`` call, which is when the
    config beside the graph is read; nothing downstream touches the directory.
    """
    with tempfile.TemporaryDirectory() as tmp:
        graph, node_id = _repo_graph(Path(tmp), name)
        result = impact(graph, seeds=[node_id], target_input=node_id)
    assert result.get("cannot_answer"), (
        "the fixture stopped reaching the cannot-answer path; every parity "
        f"case below would pass vacuously. Result: {result.get('risk_level')!r}"
    )
    return result


def _cli_refusal(result: dict) -> tuple[int, str]:
    """The exit code and stderr line ``wd impact`` itself emits for *result*.

    Driven through :func:`weld.impact_cli._cannot_answer_exit` rather than
    rebuilt from :func:`weld._errors.format_error_line` here: rebuilding it
    would make these cases assert against this file's own idea of what the CLI
    does, which is the failure mode the whole issue is about.
    """
    err = io.StringIO()
    with redirect_stderr(err):
        code = _cannot_answer_exit(result)
    return code, err.getvalue()


class DerivedFromTheContractTest(unittest.TestCase):
    """The markers and the block are read out of ``weld/_errors.py``."""

    def test_a_reworded_summary_moves_the_marker(self) -> None:
        """The case a re-introduced literal fails.

        Rewriting the table entry has to change what the invariant matches on.
        A hand-copied tuple would answer the old wording here and stay green
        while the product had stopped emitting it.
        """
        reworded = dict(_errors._DEFAULT_ERROR)
        reworded[_errors.GRAPH_MISSING] = "No Weld atlas found."
        with mock.patch.object(_errors, "_DEFAULT_ERROR", reworded):
            markers = _contract_markers.cannot_answer_markers()
        self.assertIn("No Weld atlas found.", markers)
        self.assertNotIn(_errors._DEFAULT_ERROR[_errors.GRAPH_MISSING], markers)

    def test_the_invariant_module_reads_the_derivation_not_a_copy(self) -> None:
        """The same rewording reaches the constant the assertions actually use.

        The case above proves the derivation follows the tables; this proves
        ``_graph_invariants`` is wired to it. Re-importing under the patched
        table is what tells a live derivation apart from a literal that merely
        agrees with it today -- an equality check against
        :func:`~weld.tests._contract_markers.cannot_answer_markers` would not.
        """
        reworded = dict(_errors._DEFAULT_ERROR)
        reworded[_errors.GRAPH_MISSING] = "No Weld atlas found."
        try:
            with mock.patch.object(_errors, "_DEFAULT_ERROR", reworded):
                reloaded = importlib.reload(_graph_invariants)
                self.assertIn(
                    "No Weld atlas found.", reloaded._CANNOT_ANSWER_MARKERS,
                )
        finally:
            # Restore the module's globals for every other case in this run:
            # ``reload`` re-executes into the same ``__dict__``, so the helpers
            # imported by value at the top of this file read the restored
            # markers either way.
            importlib.reload(_graph_invariants)
        self.assertNotIn(
            "No Weld atlas found.", _graph_invariants._CANNOT_ANSWER_MARKERS,
        )

    def test_a_reworded_error_line_moves_the_prefix(self) -> None:
        """The structured prefix is cut from the formatter, not spelled."""
        with mock.patch.object(
            _errors, "format_error_line", lambda code, detail=None: f"fault[{code}]:"
        ):
            self.assertEqual(_contract_markers.error_line_prefix(), "fault[")

    def test_the_verdict_clause_is_split_off_the_aside(self) -> None:
        """A summary that states a verdict then explains it yields the verdict.

        The renderer prints only that leading clause, so matching the whole
        summary would match nothing the reader ever sees.
        """
        clause = _contract_markers.verdict_clause(_errors.RESULT_UNKNOWN)
        summary = _errors.default_summary(_errors.RESULT_UNKNOWN)
        self.assertTrue(summary.startswith(clause), f"{clause!r} vs {summary!r}")
        self.assertLess(len(clause), len(summary))

    def test_a_summary_without_an_aside_stays_whole(self) -> None:
        clause = _contract_markers.verdict_clause(_errors.GRAPH_MISSING)
        self.assertEqual(clause, _errors.default_summary(_errors.GRAPH_MISSING))

    def test_a_collapsed_marker_raises_rather_than_shrinking_the_set(self) -> None:
        """Fail closed: a short set is silent in the direction that matters."""
        collapsed = dict(_errors._DEFAULT_ERROR)
        collapsed[_errors.GRAPH_MISSING] = "none"
        with mock.patch.object(_errors, "_DEFAULT_ERROR", collapsed):
            with self.assertRaises(ValueError):
                _contract_markers.cannot_answer_markers()

    def test_a_hint_naming_nothing_actionable_raises(self) -> None:
        hints = dict(_errors.ERROR_HINTS)
        hints[_errors.RESULT_UNKNOWN] = "Try again later."
        with mock.patch.object(_errors, "ERROR_HINTS", hints):
            with self.assertRaises(ValueError):
                _contract_markers.actionable_hint_tokens(_errors.RESULT_UNKNOWN)

    def test_a_reworded_contract_moves_the_graph_missing_block(self) -> None:
        """The production block follows the tables too (bd ``5038-koqmb``).

        :func:`weld._graph_cli.missing_graph_message` spelled both table values
        as literals, and the parity case below cannot tell that apart from a
        derivation: ``assertIn`` reads the same either way. This rewords both
        entries and requires the block to move with them, which is the case a
        re-introduced literal fails. Both halves matter -- the summary and the
        hint reach the reader on separate lines and could be restated one at a
        time.

        ``patch.dict`` rather than ``patch.object``: the producer imports the
        names, so rebinding the module attribute would leave it reading the
        original dicts and this case would pass on a literal -- the very skew
        it exists to catch.
        """
        standing_summary = _errors.default_summary(_errors.GRAPH_MISSING)
        standing_hint = _errors.ERROR_HINTS[_errors.GRAPH_MISSING]
        reworded_summary = "No Weld atlas found."
        reworded_hint = "Run: wd summon."
        with mock.patch.dict(
            _errors._DEFAULT_ERROR, {_errors.GRAPH_MISSING: reworded_summary}
        ), mock.patch.dict(
            _errors.ERROR_HINTS, {_errors.GRAPH_MISSING: reworded_hint}
        ):
            block = missing_graph_message("wd query alpha")

        self.assertIn(reworded_summary, block)
        self.assertIn(reworded_hint, block)
        self.assertNotIn(standing_summary, block)
        self.assertNotIn(standing_hint, block)


class ProducerParityTest(unittest.TestCase):
    """R8: each second producer still says what the contract derives."""

    def test_the_impact_renderer_prints_the_derived_verdict(self) -> None:
        """The required parity assertion.

        ``weld/impact_format.py`` builds ``Risk: <level>`` from the envelope and
        has never read ``_DEFAULT_ERROR``; ``weld/_errors.py`` opens the
        ``result_unknown`` summary with the same two words. Nothing but this
        keeps the pair spelling one verdict -- and it is asserted on the human
        render alone, because the CLI's stderr line would satisfy any
        marker-based check on ``error[`` however far the renderer had drifted.
        """
        rendered = format_human(_uncomputable_result())
        derived = _contract_markers.verdict_clause(_errors.RESULT_UNKNOWN)
        self.assertIn(
            derived, rendered,
            "the impact renderer no longer prints the verdict weld/_errors.py "
            f"states for {_errors.RESULT_UNKNOWN}",
        )

    def test_the_uncomputable_reason_names_the_contract_remediation(self) -> None:
        """The other half of the impact pair.

        ``weld/_impact_cannot_answer.py`` writes its own reason prose, and the
        label it interpolates leaves no phrase long enough to match the tables
        on. What both sides must still agree on is where they send the reader,
        so the assertion is the hint's own config keys and paths -- derived from
        ``ERROR_HINTS``, not restated -- appearing in the reason.
        """
        reason = _uncomputable_result()["cannot_answer"]["reason"]
        for token in _contract_markers.actionable_hint_tokens(_errors.RESULT_UNKNOWN):
            self.assertIn(
                token, reason,
                f"the cannot-answer reason no longer names {token!r}, which "
                "the contract hint tells the reader to go and edit",
            )

    def test_the_graph_missing_block_quotes_the_contract_verbatim(self) -> None:
        """The third producer, found while resolving R8's two.

        ``weld._graph_cli.missing_graph_message`` predates the structured line
        and emits both table values in a plain block. It is the producer behind
        the graph-missing marker, so the marker is only worth deriving if this
        still emits what was derived.

        It hand-copied them until bd ``5038-koqmb`` made it read the tables, and
        this case cannot see that difference -- ``assertIn`` passes on either.
        What it still catches is a *dropped* line: the summary and the hint reach
        the reader on separate lines, and a block that stopped carrying one would
        fail here whether the strings are derived or restated.
        ``test_a_reworded_contract_moves_the_graph_missing_block`` above is the
        half that sees the difference.
        """
        block = missing_graph_message("wd query alpha")
        self.assertIn(_errors.default_summary(_errors.GRAPH_MISSING), block)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_MISSING], block)


class InvariantBehaviourUnchangedTest(unittest.TestCase):
    """The two helpers still classify the outcomes they classified before.

    The marker set lost ``cannot be computed`` when it stopped being restated
    (see :mod:`weld.tests._contract_markers`). These cases are the evidence that
    nothing the helpers used to recognise stopped being recognised: the impact
    refusal is still a refusal on both of its streams, and a measured empty
    result is still an answer.

    The *remediation* set later lost ``"See "`` the same way (bd
    ``5038-hkb8x``), and these cases are that drop's evidence too, which is why
    they are named here rather than left implicit. ``assert_cannot_answer``
    checks both sets, and the fixture below reaches the no-resolver branch of
    :func:`weld._impact_cannot_answer.uncomputable_repo_reason` -- the one
    refusal path in the tree that ever emitted ``"See "``. So a marker set
    trimmed to what the contract produces has to keep classifying exactly that
    refusal, or the first case here goes red.
    """

    def test_the_impact_refusal_still_reads_as_cannot_answer(self) -> None:
        result = _uncomputable_result()
        code, stderr = _cli_refusal(result)
        assert_cannot_answer(code, stderr, format_human(result))

    def test_the_json_refusal_still_reads_as_cannot_answer(self) -> None:
        """``--json`` prints no ``Risk:`` line, so this leans on ``error[``.

        The case that would catch a marker set trimmed to what the human render
        happens to carry -- ``--json`` writes the same stderr line and an
        envelope spelling the verdict as ``risk_level``, which no marker
        matches.
        """
        code, stderr = _cli_refusal(_uncomputable_result())
        assert_cannot_answer(code, stderr, stdout="")

    def test_a_measured_empty_result_still_reads_as_answered(self) -> None:
        """ADR 0134's boundary: a real zero must not be demoted to a refusal.

        A non-``repo:`` seed with no dependents, which is the case the
        cannot-answer rule must decline. The node goes in through
        :meth:`weld.graph.Graph.add_node` -- the production write path -- and
        nothing under test reads any prop of it; all that matters is that it is
        not the ``repo:`` shape :mod:`weld._impact_cannot_answer` keys on.
        """
        with tempfile.TemporaryDirectory() as tmp:
            graph = Graph(Path(tmp))
            graph.add_node("file:app/main", "file", "main", {"file": "app/main.py"})
            result = impact(
                graph, seeds=["file:app/main"], target_input="app/main.py",
            )
        self.assertIsNone(result.get("cannot_answer"))
        self.assertEqual(_cannot_answer_exit(result), 0)
        assert_answered_empty(0, format_human(result))


if __name__ == "__main__":
    unittest.main()
