"""One generated equivalence case, run as its own Bazel target (bd ``br7jb``).

:mod:`weld.tests.incremental_generative_equivalence_test` asserts that the
generator, the differ, and the self-checks *work*. This file asserts the
ordinary thing the ``incremental_*_equivalence_test`` family asserts -- full and
incremental discovery agree, node for node and edge for edge -- over one seeded
case. It is the ``main`` of every per-seed target
``weld/tests/equivalence_tests.bzl`` declares -- ADR 0139 § 5's near tier;
the wide sweep that does not fit the fast loop is bd ``us0u9``'s.

One target per seed rather than one target looping over the set, because the
loop hides three things Bazel would otherwise give for free: a cached case is
skipped instead of re-run, ``tools/changed_test_targets.py`` selects the cases
a diff actually touches, and a failure names the seed in the target label rather
than only in the log. Each case is roughly a second of work -- two full
discovers and one incremental refresh over a repo of a few files -- so every
target sits far inside ADR 0136 § 9's ten-second untagged budget.

The seed arrives in the environment and nowhere else. Starlark cannot read a
Python tuple, so a seed list written here as well as in the macro would be two
sources of truth for one fact; what the set as a whole has to span is asserted
by :mod:`weld.tests.incremental_generative_seed_coverage_test`, which reads the
macro's tuple the same way. A target declaring no seed is an error rather than
a skip, for the reason
:class:`weld.tests._equivalence_sweep.SweepSelfCheckError` exists: a run that
could not have meant anything must not report green.
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Mapping
from functools import lru_cache

from weld.tests import _equivalence_sweep as sweep
from weld.tests._equivalence_sweep_repo import generate_case

#: Bazel ``env`` key carrying this target's seed. Set by ``equivalence_tests``.
SEED_ENV = "WELD_SWEEP_SEED"


def declared_seed(environ: Mapping[str, str] | None = None) -> int:
    """The seed this target was declared with, or a hard failure.

    Fail-closed on purpose: a target whose ``env`` entry was dropped in a macro
    edit would otherwise pick a default seed and go green while testing nothing
    the declaration asked for.
    """
    raw = (os.environ if environ is None else environ).get(SEED_ENV)
    if raw is None or not raw.strip():
        raise RuntimeError(
            f"{SEED_ENV} is unset or blank -- this target must be declared by "
            "equivalence_tests() in weld/tests/equivalence_tests.bzl, which "
            "supplies the seed"
        )
    return int(raw)


@lru_cache(maxsize=None)
def _report(seed: int) -> sweep.DiffReport:
    """One real run per process, shared by the assertions below.

    Same memo as the aggregate suite's, and for the same reason: the two
    assertions read one run from two angles, and running the case twice would
    double a target's wall time without asserting anything the aggregate
    suite's render-determinism test does not already cover.
    """
    return sweep.run_case(generate_case(seed))


class GeneratedCaseEquivalenceTest(unittest.TestCase):
    """Full and incremental discovery agree on this target's generated case."""

    def setUp(self) -> None:
        self.report = _report(declared_seed())

    def test_the_case_is_equivalent(self) -> None:
        self.assertFalse(self.report.divergent, self.report.render())

    def test_the_case_discovers_a_non_trivial_graph(self) -> None:
        """Equality over two empty graphs would satisfy the test above.

        The same guard the aggregate suite keeps for its control seed, restated
        per target: :func:`weld.tests._equivalence_sweep._check_population`
        refuses a graph missing a generated module, and this refuses one that
        anchored the modules and derived nothing from them.
        """
        nodes, edges = self.report.full_population
        self.assertGreater(nodes, 10, self.report.render())
        self.assertGreater(edges, 10, self.report.render())


class SeedDeclarationTest(unittest.TestCase):
    """A target that declares no seed errors; it does not pick a default."""

    def test_a_missing_or_blank_declaration_is_an_error(self) -> None:
        for environ in ({}, {SEED_ENV: ""}, {SEED_ENV: "  "}):
            with self.subTest(environ=environ):
                with self.assertRaises(RuntimeError):
                    declared_seed(environ)

    def test_a_declared_seed_is_read_as_an_int(self) -> None:
        self.assertEqual(declared_seed({SEED_ENV: "7"}), 7)


if __name__ == "__main__":
    unittest.main()
