"""Contract for the derived remediation markers and the producer behind them.

Bd ``5038-hkb8x``, the sibling of ``5038-fprr2`` and the same two ADR 0139
mechanisms in the same arrangement. Mechanism 4 asks that
:data:`weld.tests._graph_invariants._REMEDIATION_MARKERS` be read out of
``weld/_errors.py`` rather than restated; mechanism 3 is the standing rule for
what derivation alone cannot fix -- a fact with a second producer that never
reads those tables owes a parity test fed by the real producers.

So this file holds two different things, as its cannot-answer sibling does:

* :class:`DerivedFromTheContractTest` -- mechanism 4. Each load-bearing case
  rewrites what ``weld/_errors.py`` says and requires the derived marker to move
  with it. A re-introduced literal answers the old wording and fails, which is
  the only difference an equality check against
  :func:`weld.tests._contract_markers.remediation_markers` could not see.
* :class:`ProducerParityTest` -- mechanism 3. ``Then retry:`` has two producers:
  :func:`weld._errors.structured_payload` builds it for the MCP payload and the
  ``find`` refusal, and :func:`weld._graph_cli.missing_graph_message` spells the
  same line itself for the first-run guidance block. The derivation reads the
  first; nothing but a parity case keeps the second saying it.

:class:`RemediationCheckStillBitesTest` is the third thing, and the reason the
derivation fails closed. Every case above would still pass if
:func:`~weld.tests._graph_invariants.assert_cannot_answer` had stopped
*rejecting* anything, because a marker set is only observable through what it
refuses. The graph-missing block is where that matters most: it predates the
structured error line and carries neither the ``error[`` prefix nor the
``hint:`` label, so it is recognised as stating remediation through exactly the
two markers this issue derived and through nothing else.
"""

from __future__ import annotations

import importlib
import unittest
from unittest import mock

from weld import _errors
from weld._graph_cli import _build_retry_hint, missing_graph_message
from weld.tests import _contract_markers, _graph_invariants
from weld.tests._graph_invariants import assert_cannot_answer

#: A hint reworded to open with a different imperative, for both codes the
#: derivation names. Both, because leaving one at the standing wording would
#: keep ``Run:`` in the set and make the "the old marker is gone" half of every
#: case below unfalsifiable.
_RESPELLED_HINTS = {
    _errors.GRAPH_MISSING: "Summon: wd init, then wd discover.",
    _errors.FILE_INDEX_MISSING: "Summon: wd discover.",
}


def _respelled_imperative() -> str:
    """The imperative :data:`_RESPELLED_HINTS` opens with, cut the same way.

    Read through :func:`weld.tests._contract_markers.hint_imperative` under the
    patch rather than spelled a second time here: this is the value the cases
    assert *arrived*, and typing it twice is how an assertion starts agreeing
    with its own author instead of with the derivation.
    """
    with mock.patch.dict(_errors.ERROR_HINTS, _RESPELLED_HINTS):
        return _contract_markers.hint_imperative(_errors.GRAPH_MISSING)


class DerivedFromTheContractTest(unittest.TestCase):
    """The remediation markers are read out of ``weld/_errors.py``."""

    def test_a_reworded_hint_moves_the_imperative(self) -> None:
        """The case a re-introduced literal fails.

        Rewriting both artifact-missing hints has to change what the invariant
        matches on. A hand-copied tuple would go on answering ``Run:`` while
        nothing in the contract said it any more.
        """
        standing = _contract_markers.hint_imperative(_errors.GRAPH_MISSING)
        with mock.patch.dict(_errors.ERROR_HINTS, _RESPELLED_HINTS):
            markers = _contract_markers.remediation_markers()
        self.assertIn(_respelled_imperative(), markers)
        self.assertNotIn(standing, markers)

    def test_the_invariant_module_reads_the_derivation_not_a_copy(self) -> None:
        """The same rewording reaches the constant the assertions actually use.

        The case above shows the derivation follows the tables; this shows
        ``_graph_invariants`` is wired to it. Re-importing under the patched
        table is what tells a live derivation apart from a literal that happens
        to agree with it today.
        """
        respelled = _respelled_imperative()
        try:
            with mock.patch.dict(_errors.ERROR_HINTS, _RESPELLED_HINTS):
                reloaded = importlib.reload(_graph_invariants)
                self.assertIn(respelled, reloaded._REMEDIATION_MARKERS)
        finally:
            # Restore the module's globals for every other case in this run:
            # ``reload`` re-executes into the same ``__dict__``, so the helper
            # imported by value at the top of this file reads the restored
            # markers either way.
            importlib.reload(_graph_invariants)
        self.assertNotIn(respelled, _graph_invariants._REMEDIATION_MARKERS)

    def test_a_reworded_error_line_moves_the_hint_label(self) -> None:
        """The label is cut off the formatter's own output, not spelled.

        The replacement keeps the real hint at the end of the line, because
        that is what the cut locates: change only the token that introduces it
        and the derived label has to follow.
        """
        def relabelled(code: str, detail: str | None = None) -> str:
            return f"error[{code}]: summary / tip: {_errors.ERROR_HINTS[code]}"

        with mock.patch.object(_errors, "format_error_line", relabelled):
            self.assertEqual(_contract_markers.hint_label(), "tip:")

    def test_a_reworded_retry_field_moves_the_retry_label(self) -> None:
        """Likewise for the retry label, cut off a real payload."""
        def relabelled(
            code: str,
            *,
            detail: str | None = None,
            retry_cmd: str | None = None,
        ) -> dict:
            return {"error_code": code, "retry": f"Retry now: {retry_cmd}!"}

        with mock.patch.object(_errors, "structured_payload", relabelled):
            self.assertEqual(_contract_markers.retry_label(), "Retry now:")

    def test_the_named_codes_are_every_code_that_states_an_imperative(self) -> None:
        """The tuple of codes is the one hand-written thing left; read it back.

        A code dropped from it would be invisible any other way: the set
        de-duplicates, both codes yield the same marker today, so removing
        either changes nothing observable about the set's contents. A new
        artifact-missing code added to ``weld/_errors.py`` and not named here
        would be equally quiet. So the expectation comes from the tables --
        every hint that opens with an imperative is named, and nothing else is
        -- decided by the derivation's own predicate rather than by a second
        copy of the rule spelled here.
        """
        def states_an_imperative(code: str) -> bool:
            try:
                _contract_markers.hint_imperative(code)
            except ValueError:
                return False
            return True

        stating = {c for c in _errors.ERROR_HINTS if states_an_imperative(c)}
        self.assertEqual(set(_contract_markers._IMPERATIVE_HINT_CODES), stating)
        markers = _contract_markers.remediation_markers()
        for code in sorted(stating):
            self.assertIn(_contract_markers.hint_imperative(code), markers)


class FailsClosedTest(unittest.TestCase):
    """A derivation that comes out short raises instead of returning."""

    def test_a_hint_that_states_no_imperative_raises(self) -> None:
        """Not every hint has one, and a fragment of prose is not a fallback."""
        no_opening = {_errors.GRAPH_MISSING: "Rebuild the graph with wd discover."}
        with mock.patch.dict(_errors.ERROR_HINTS, no_opening):
            with self.assertRaises(ValueError):
                _contract_markers.hint_imperative(_errors.GRAPH_MISSING)

    def test_a_collapsed_imperative_raises_rather_than_shrinking_the_set(self) -> None:
        """Below the discriminating length the whole set is refused.

        The direction that matters for this set is permissiveness: nothing
        rejects output for lacking a remediation marker, so a marker short
        enough to appear anywhere would let a refusal that states no
        remediation pass the one check that exists to catch it.
        """
        collapsed = {code: "R: wd discover." for code in _RESPELLED_HINTS}
        with mock.patch.dict(_errors.ERROR_HINTS, collapsed):
            with self.assertRaises(ValueError):
                _contract_markers.remediation_markers()

    def test_an_error_line_that_introduces_no_hint_raises(self) -> None:
        """The cut has to find something; an empty label is the worst answer."""
        with mock.patch.object(
            _errors, "format_error_line", lambda code, detail=None: f"error[{code}]:"
        ):
            with self.assertRaises(ValueError):
                _contract_markers.remediation_markers()


class ProducerParityTest(unittest.TestCase):
    """The second producer of ``Then retry:`` still writes what was derived."""

    def test_the_graph_missing_block_states_the_derived_retry(self) -> None:
        """ADR 0139 mechanism 3, for the one label with two producers.

        :func:`weld._graph_cli.missing_graph_message` builds its retry line
        itself and has never read :func:`weld._errors.structured_payload`,
        which is where the derivation cuts the label from. The expected value
        is read from that producer rather than spelled here -- spelling it
        twice would make this a parity test in name only -- and the block is
        asserted alone, because the surfaces that carry ``hint:`` would satisfy
        any marker check no matter how far this one had drifted.
        """
        block = missing_graph_message(_build_retry_hint("query", "alpha"))
        self.assertIn(
            _contract_markers.retry_label(), block,
            "the first-run guidance block no longer introduces its retry the "
            "way weld/_errors.py does, so the two surfaces now tell a reader "
            "to retry in two different vocabularies",
        )


class RemediationCheckStillBitesTest(unittest.TestCase):
    """``assert_cannot_answer`` classifies what it classified before.

    Both directions, because a marker set is only observable through what it
    refuses: the graph-missing refusal still reads as one that states
    remediation, and a refusal that states none is still rejected.
    """

    def test_the_graph_missing_block_still_states_remediation(self) -> None:
        """The refusal with no ``error[`` prefix and no ``hint:`` label.

        It is recognised through the derived imperative and the derived retry
        label and through nothing else, which makes it the surface that would
        have broken had this issue (bd ``5038-hkb8x``) derived either one
        wrongly. The exit code is a non-zero stand-in: which code the CLI exits
        with is :func:`weld._graph_cli.ensure_graph_exists`' fact, held by
        ``weld_missing_graph_guidance_test`` rather than by this file.
        """
        assert_cannot_answer(1, missing_graph_message(_build_retry_hint("query", "a")))

    def test_a_refusal_stating_no_remediation_is_still_rejected(self) -> None:
        """The half that a collapsed derivation would silently switch off.

        The headline alone, from the contract's own table: a genuine
        cannot-answer marker with nothing telling the reader what to do next.
        """
        with self.assertRaises(AssertionError):
            assert_cannot_answer(1, _errors.default_summary(_errors.GRAPH_MISSING))


if __name__ == "__main__":
    unittest.main()
