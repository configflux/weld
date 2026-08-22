"""The orchestrator's node-selection rules (ADR 0103, ADR 0008 s5).

``claim_supersedes``: the evidence-loss veto (ADR 0103).

The discover orchestrator folds one ``StrategyResult`` per source entry into a
single node table. ``dict.update`` let a later entry's evidence-free stub
overwrite the definite, file-bearing definition an earlier entry had walked,
which is how ``symbol:py:weld.discover:discover`` ended up with no file, no
line, no kind and no ``contains`` edge (bd 4ux4).

These pin the veto's exact boundary: it fires only when both sides state a
known confidence and the incoming one is strictly weaker. Everywhere else the
historical last-writer-wins behaviour must survive untouched -- ``weld.init``
and ``weld._init_framework_sources`` deliberately depend on a later batch's
tree-sitter node winning (ADR 0071), and a rule that reached further would
rewrite that silently.

``incremental_claim_wins``: the same veto plus the dirty-scope question the
incremental path alone has to answer -- may a re-run source entry speak about a
file that did not change? The two halves of that answer are pinned separately
below, because they used to be one gate and collapsing them is the bug: a
re-run entry may not *overwrite* a clean file's node, but discarding a claim on
an ID the graph does not hold is not a clobber, it is the node's only chance to
exist, and dropping it made the graph differ between an incremental run and a
full one over an identical tree (bd n0p2).
"""

from __future__ import annotations

import unittest

from weld._discover_node_merge import (
    _CONFIDENCE_RANK,
    claim_supersedes,
    incremental_claim_wins,
)
from weld.contract import CONFIDENCE_VALUES
from weld.ranking import CONFIDENCE_RANK


def _node(confidence, **props):
    """Build a minimal symbol node carrying *confidence* in its props."""
    return {"type": "symbol", "label": "x", "props": {"confidence": confidence, **props}}


class VetoBoundaryTest(unittest.TestCase):
    def test_first_claim_always_lands(self) -> None:
        self.assertTrue(claim_supersedes(None, _node("speculative")))

    def test_speculative_does_not_replace_definite(self) -> None:
        # The bd 4ux4 case: python_callgraph's cross-glob stub vs the
        # definition another glob actually parsed.
        existing = _node("definite", file="lib/core.py", line=1, kind="function")
        self.assertFalse(claim_supersedes(existing, _node("speculative")))

    def test_definite_replaces_speculative(self) -> None:
        # The same collision in the other source order must still upgrade.
        incoming = _node("definite", file="lib/core.py", line=1, kind="function")
        self.assertTrue(claim_supersedes(_node("speculative"), incoming))

    def test_weaker_never_wins_across_the_whole_ordering(self) -> None:
        for stronger, weaker in (
            ("definite", "inferred"),
            ("definite", "speculative"),
            ("inferred", "speculative"),
        ):
            with self.subTest(stronger=stronger, weaker=weaker):
                self.assertFalse(claim_supersedes(_node(stronger), _node(weaker)))
                self.assertTrue(claim_supersedes(_node(weaker), _node(stronger)))

    def test_equal_confidence_keeps_last_writer_wins(self) -> None:
        # ADR 0071's tree-sitter-wins ordering rides on this staying true.
        for value in sorted(CONFIDENCE_VALUES):
            with self.subTest(confidence=value):
                self.assertTrue(claim_supersedes(_node(value), _node(value)))


class IncomparableClaimsFallBackTest(unittest.TestCase):
    """No comparable confidence on either side -> historical behaviour."""

    def test_existing_without_confidence(self) -> None:
        self.assertTrue(claim_supersedes({"props": {}}, _node("speculative")))

    def test_existing_without_props(self) -> None:
        self.assertTrue(claim_supersedes({"type": "symbol"}, _node("speculative")))

    def test_incoming_without_confidence(self) -> None:
        self.assertTrue(claim_supersedes(_node("definite"), {"props": {}}))

    def test_unknown_confidence_vocabulary(self) -> None:
        self.assertTrue(claim_supersedes(_node("definite"), _node("probably")))

    def test_non_string_confidence_does_not_raise(self) -> None:
        # props come from strategy plugins, including project-local ones; an
        # unhashable value must not blow up the whole discover run.
        self.assertTrue(claim_supersedes(_node("definite"), _node(["definite"])))
        self.assertTrue(claim_supersedes(_node({"a": 1}), _node("speculative")))


class IncrementalDirtyScopeTest(unittest.TestCase):
    """The re-run entry's licence to speak about a file that did not change."""

    #: The bd n0p2 shape: a ``validator_targets`` stub for the export-less
    #: ``__init__.py`` a freshly-edited lint governs. The lint is dirty; the
    #: ``__init__.py`` named by it is not.
    STUB = {
        "type": "file",
        "label": "__init__.py",
        "props": {"file": "pkg/__init__.py", "confidence": "inferred"},
    }
    DIRTY = frozenset({"tools/lint_thing.py"})

    def test_absent_id_is_minted_for_a_clean_file(self) -> None:
        """The bug. No incumbent means no clobber -- only a hole to fill.

        Rejected here, the stub never lands and the ``validates`` edge that
        needed it dangles and is swept, so a newly governed file stays invisible
        until someone runs ``--full``.
        """
        self.assertTrue(incremental_claim_wins(None, self.STUB, self.DIRTY))

    def test_incumbent_for_a_clean_file_is_not_overwritten(self) -> None:
        """The guard's original job, unchanged.

        A full run's winner for a clean file is decided by source ordering
        across *all* entries; incrementally the clean entries never run, so a
        re-run entry that overwrote here would install its claim where a full
        run kept a later entry's.
        """
        incumbent = _node("definite", file="pkg/__init__.py")
        self.assertFalse(incremental_claim_wins(incumbent, self.STUB, self.DIRTY))

    def test_clean_file_incumbent_holds_even_against_a_stronger_claim(self) -> None:
        """Dirty scope is not a confidence question, so it outranks one.

        Better evidence does not buy a re-run entry the right to speak for a
        file this run never looked at; only ``--full`` re-decides that.
        """
        incumbent = _node("speculative", file="pkg/__init__.py")
        stronger = _node("definite", file="pkg/__init__.py")
        self.assertFalse(incremental_claim_wins(incumbent, stronger, self.DIRTY))

    def test_dirty_file_claim_still_answers_to_the_veto(self) -> None:
        """In scope, so ADR 0103 decides -- in both directions."""
        weaker = _node("speculative", file="tools/lint_thing.py")
        stronger = _node("definite", file="tools/lint_thing.py")
        self.assertFalse(incremental_claim_wins(stronger, weaker, self.DIRTY))
        self.assertTrue(incremental_claim_wins(weaker, stronger, self.DIRTY))

    def test_file_less_claim_still_answers_to_the_veto(self) -> None:
        """``python_callgraph``'s cross-glob stub: no file, so always in scope.

        It has no ``props.file`` to test against the dirty set, which is why the
        veto is the only thing standing between it and the definition another
        entry walked (bd 4ux4).
        """
        definition = _node("definite", file="lib/core.py", line=1, kind="function")
        self.assertFalse(
            incremental_claim_wins(definition, _node("speculative"), self.DIRTY)
        )

    def test_malformed_props_fall_back_instead_of_raising(self) -> None:
        """A strategy plugin must not be able to abort the whole run.

        Read naively, ``props: None`` raises ``AttributeError`` and an
        unhashable ``file`` raises ``TypeError`` out of the set membership
        test. Both shapes name no testable file, so both fall through to the
        veto -- the same fallback ``_confidence_rank`` makes for a confidence
        it cannot compare.
        """
        incumbent = _node("definite", file="pkg/__init__.py")
        for malformed in (
            {"type": "file", "label": "x"},
            {"type": "file", "label": "x", "props": None},
            {"type": "file", "label": "x", "props": ["file"]},
            _node("definite", file=["pkg/__init__.py"]),
            _node("definite", file=None),
        ):
            with self.subTest(node=malformed):
                self.assertTrue(
                    incremental_claim_wins(incumbent, malformed, self.DIRTY)
                )

    def test_empty_dirty_set_still_admits_a_first_claim(self) -> None:
        """Degenerate scope must not turn hole-filling off.

        ``dirty`` is never empty on the path that calls this, but a rule whose
        insertion arm depended on scope would be the same conflation again.
        """
        self.assertTrue(incremental_claim_wins(None, self.STUB, frozenset()))


class ConfidenceTableParityTest(unittest.TestCase):
    """The restated rank table must not drift from its two sources of truth."""

    def test_ordering_matches_ranking(self) -> None:
        self.assertEqual(_CONFIDENCE_RANK, CONFIDENCE_RANK)

    def test_vocabulary_matches_contract(self) -> None:
        self.assertEqual(set(_CONFIDENCE_RANK), set(CONFIDENCE_VALUES))


if __name__ == "__main__":
    unittest.main()
