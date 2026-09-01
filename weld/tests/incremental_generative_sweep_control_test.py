"""The merge-train sweep's permanent negative control (ADR 0139, bd ``us0u9``).

:mod:`weld.tests.incremental_generative_sweep_test` is a search. A search that
finds nothing has found nothing, so its green run carries no information on its
own -- it looks identical whether the comparison is working and the tree is
sound, or the comparison has quietly stopped comparing. This file is what makes
the difference observable, and ADR 0139 requires it to ship *with* the sweep and
stay: "a sweep that silently stops comparing is green forever: class 1 in its
purest form."

Permanent, and untagged, are both deliberate:

* **Permanent.** The control is not a one-off proof that the sweep worked the
  day it landed. It plants a divergence on every run, so the sweep's green
  verdict is re-earned rather than assumed. Deleting it, or reducing it to a
  fixture that no longer reaches the real comparison, is the regression it
  exists to catch.
* **Untagged.** The sweep itself sits behind ``integration`` and only the
  ``--config=ci`` lane reaches it, but this file's subject is the sweep's own
  machinery, which is edited in the inner loop like anything else. A control
  that ran only where the sweep runs would be absent from every lane where the
  code it guards actually changes. It stays cheap enough to belong there: two
  generated cases, well inside ADR 0136 § 9's ten-second untagged budget.

The divergence is *planted into a real discovery output* through
:func:`weld.tests._equivalence_sweep.run_case`'s ``sabotage`` seam, never by
writing a graph out by hand. A hand-authored graph would prove the comparison
detects a shape the producer cannot emit, which is a different and much weaker
claim. Injection over a real bug is what keeps the control permanent, too: the
sweep's one real finding (bd ``rwi34``) will one day be fixed, and a control
resting on it would then have to be rewritten. Re-checking *that* finding is
the exclusion ledger's job, in the sweep target, where the skip is applied.
"""

from __future__ import annotations

import unittest

from weld.tests import _equivalence_sweep as sweep
from weld.tests import _equivalence_sweep_range as _range

#: The seed the controls run on: the first the merge train actually sweeps, so
#: the control is exercising a member of the real window rather than a case
#: chosen beside it.
CONTROL_SEED = _range.swept_seeds()[0]

#: A node id no discovery run mints, so its presence in the comparison is
#: unambiguously the planted difference and not a coincidence of the case.
PLANTED_NODE = "symbol:unresolved:sweep_window_control"


def _plant_a_node(graph: dict) -> None:
    """Copy an existing node onto an id nothing produces.

    Copied rather than constructed: the payload is whatever the producer just
    emitted, so this cannot drift into asserting a node shape discovery has
    stopped using (ADR 0139 mechanism 1).
    """
    donor = sorted(graph["nodes"])[0]
    graph["nodes"][PLANTED_NODE] = graph["nodes"][donor]


class PlantedDivergenceTest(unittest.TestCase):
    """The window run goes red on an injected difference, and says which seed."""

    def test_the_control_seed_is_equivalent_when_nothing_is_planted(self) -> None:
        """The control's other half.

        Without it a comparison that reported DIVERGENT unconditionally would
        satisfy every assertion below, and the sweep would be red forever
        rather than green forever -- the same defect, louder.
        """
        outcome = _range.run_window([CONTROL_SEED])
        self.assertEqual(outcome.divergent, (), outcome.failure_message())
        self.assertEqual(outcome.ran, (CONTROL_SEED,))

    def test_a_planted_node_makes_the_window_report_a_divergence(self) -> None:
        outcome = _range.run_window([CONTROL_SEED], sabotage=_plant_a_node)
        self.assertEqual(len(outcome.divergent), 1)
        self.assertIn(
            PLANTED_NODE, outcome.divergent[0].nodes_only_incremental
        )

    def test_the_failure_message_names_the_seed_and_how_to_reproduce_it(self) -> None:
        """What a merge-train failure hands a reader who did not run it.

        The whole value of a CI-lane sweep failing is the one-line repro it
        prints; a message that only said "cases diverged" would send its reader
        back to re-derive a sixty-second run.
        """
        outcome = _range.run_window([CONTROL_SEED], sabotage=_plant_a_node)
        message = outcome.failure_message()
        self.assertIn(f"seed {CONTROL_SEED}:", message)
        self.assertIn(sweep.REPRO_TEMPLATE.format(seed=CONTROL_SEED), message)
        self.assertIn(PLANTED_NODE, message)
        self.assertIn("1 of 1 generated cases diverged", message)

    def test_the_remedy_sends_a_finding_to_a_pin_and_not_to_the_skip_list(self) -> None:
        """ADR 0113: the artifact of a finding is a pinned case, not a green run.

        A reader who reaches for the exclusion ledger to clear a *new*
        divergence has taken the one route that loses the finding, so the
        message has to name the pinning route first and rule that one out by
        name.
        """
        message = _range.run_window(
            [CONTROL_SEED], sabotage=_plant_a_node
        ).failure_message()
        self.assertIn("ADR 0113", message)
        self.assertIn("equivalence_tests.bzl", message)
        self.assertIn("Do NOT narrow the window", message)


class VacuousRunTest(unittest.TestCase):
    """The ways a window can compare nothing are refused, not reported green."""

    def test_an_empty_window_raises_rather_than_passing(self) -> None:
        with self.assertRaises(sweep.SweepSelfCheckError):
            _range.run_window([])

    def test_the_declared_window_clears_its_own_floor(self) -> None:
        """The floor the sweep asserts has to be reachable by the window.

        Shrinking ``WINDOW`` below ``MINIMUM_CASES`` would leave the sweep red
        for a reason unrelated to discovery; this fails first, in the fast
        loop, and says so.
        """
        self.assertGreaterEqual(len(_range.swept_seeds()), _range.MINIMUM_CASES)

    def test_the_floor_is_a_real_floor(self) -> None:
        """A ``MINIMUM_CASES`` of nought would make the sweep's guard vacuous."""
        self.assertGreater(_range.MINIMUM_CASES, 0)


class LedgerDisciplineTest(unittest.TestCase):
    """What an exclusion has to carry before it may narrow the sweep.

    Pure policy checks, no discovery: that each skipped seed *still* diverges
    for its stated reason costs a real run and belongs with the sweep, where
    the skip is applied. What belongs here is the part that must hold in the
    fast loop -- an entry cannot be added without an issue to answer for it.
    """

    def test_every_exclusion_cites_a_bd_issue(self) -> None:
        for known in _range.KNOWN_DIVERGENCES:
            with self.subTest(seed=known.seed):
                self.assertTrue(
                    known.cites_an_issue(),
                    f"seed {known.seed} is excluded citing {known.issue!r}, "
                    "which is not a bd issue id -- an untracked skip is how a "
                    "divergence stops being a finding",
                )

    def test_every_exclusion_names_the_shape_it_was_excluded_for(self) -> None:
        for known in _range.KNOWN_DIVERGENCES:
            with self.subTest(seed=known.seed):
                self.assertTrue(known.node_only_in_incremental.strip())

    def test_the_ledger_holds_no_duplicate_seeds(self) -> None:
        """Two entries for one seed means one of them is unverifiable."""
        seeds = _range.excluded_seeds()
        self.assertEqual(len(set(seeds)), len(seeds))

    def test_exclusions_do_not_consume_the_window(self) -> None:
        """The ledger is a skip list, not a second way to shrink the sweep."""
        self.assertLess(
            len(_range.excluded_seeds()), len(_range.window_seeds()) // 10
        )


if __name__ == "__main__":
    unittest.main()
