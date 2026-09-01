"""The merge train's wide equivalence sweep (ADR 0139, bd ``us0u9``).

The generative family runs in two tiers. The near one is a handful of pinned
seeds as ordinary untagged targets, which every fast loop pays for
(:mod:`weld.tests.incremental_generative_seed_case_test`, bd ``br7jb``). This
is the far one: several hundred generated cases through both discovery paths,
too slow for the inner loop, carrying ``integration`` as a *cost* label under
the admission ADR 0139 § "Admitting the class-5 sweep to the ``--config=ci``
lane" opens. It therefore runs where ADR 0136 already puts the merge train --
``bazel test --config=ci``, and so the repository gate at its ``--scope=ci``,
which is the pre-push one -- and nowhere earlier.

What this target is *for* is worth stating precisely, because it is the first
test in the repo whose passing is not evidence of anything. It is a search, and
a search that finds nothing has found nothing. Acceptance for bd ``us0u9`` is
mechanism-works, not found-a-real-bug: gating a sweep's landing on discovering
a divergence rewards sitting on the issue, or quietly weakening the comparison
until the search is cheap. So the assertion that a green run here means
anything lives elsewhere, in the permanent negative control
:mod:`weld.tests.incremental_generative_sweep_control_test` -- untagged, so the
fast loop runs it, which is the lane where this file's machinery gets edited.

Two things this file adds over one big ``assertFalse``:

* **A floor on how much was actually swept.** ``0 divergent of 0 run`` passes
  every assertion about divergence. :data:`_range.MINIMUM_CASES` is what
  notices a collapsed window.
* **The exclusion ledger, re-checked.** Every seed
  :data:`_range.KNOWN_DIVERGENCES` skips is re-run here and has to still
  diverge in the shape its issue records. An exclusion is the one lever that
  can make this target green without fixing anything, so it is kept honest
  rather than trusted (ADR 0113's pinning loop; the ledger's rationale is in
  :mod:`weld.tests._equivalence_sweep_range`).
"""

from __future__ import annotations

import sys
import unittest
from functools import lru_cache

from weld.tests import _equivalence_sweep_range as _range


@lru_cache(maxsize=None)
def _outcome() -> _range.SweepOutcome:
    """The window, run once per process and shared by every assertion below.

    Both classes' assertions read one run from different angles; running the
    window twice would double a minute of work to learn nothing new.
    """
    return _range.run_window(_range.swept_seeds(), report_to=sys.stdout)


class MergeTrainSweepTest(unittest.TestCase):
    """Full and incremental discovery agree across the whole window."""

    def test_no_swept_case_diverges(self) -> None:
        outcome = _outcome()
        self.assertEqual(outcome.divergent, (), outcome.failure_message())

    def test_the_run_swept_a_wide_window_and_not_a_handful(self) -> None:
        """The vacuity guard: a green verdict over three cases is not a sweep.

        Deliberately phrased against :data:`_range.MINIMUM_CASES` rather than
        against ``WINDOW``, so shrinking the window does not shrink the
        assertion along with it.
        """
        self.assertGreaterEqual(len(_outcome().ran), _range.MINIMUM_CASES)


class KnownDivergenceLedgerTest(unittest.TestCase):
    """Every skipped seed still diverges, and still for its recorded reason.

    Without this the ledger decays in both directions. A cited bug that gets
    fixed leaves a permanent skip nobody revisits, narrowing the sweep for
    free; and a seed excluded by number alone goes on absorbing whatever it
    diverges for next, which need not be what it was excluded for.
    """

    def test_the_ledger_is_a_strict_subset_of_the_window(self) -> None:
        """A skip outside the window is a skip of nothing -- a stale entry."""
        self.assertTrue(
            set(_range.excluded_seeds()) < set(_range.window_seeds()),
            f"{_range.excluded_seeds()} vs window "
            f"{_range.BASE_SEED}..{_range.BASE_SEED + _range.WINDOW - 1}",
        )

    def test_every_excluded_seed_still_diverges_in_its_recorded_shape(self) -> None:
        for known in _range.KNOWN_DIVERGENCES:
            with self.subTest(seed=known.seed, issue=known.issue):
                outcome = _range.run_window([known.seed])
                self.assertEqual(
                    len(outcome.divergent),
                    1,
                    f"seed {known.seed} no longer diverges. If {known.issue} is "
                    "fixed, drop its KnownDivergence entry so the sweep covers "
                    "the seed again.",
                )
                report = outcome.divergent[0]
                self.assertIn(
                    known.node_only_in_incremental,
                    report.nodes_only_incremental,
                    f"seed {known.seed} diverges, but not in the shape "
                    f"{known.issue} records -- so the exclusion is now hiding "
                    f"something else:\n{report.render()}",
                )


if __name__ == "__main__":
    unittest.main()
