"""The smoke tier's seed set is a diversity floor, not arbitrary numbers.

``weld/tests/equivalence_tests.bzl`` declares one target per seed, and the seeds
themselves are a choice: a handful of integers in a Starlark tuple look identical
whether they were picked to span the generator or copied from whatever was on
screen. This target is what makes the choice checkable. It reads the same set
the macro expands and asserts the property the set was chosen for -- one seed
per mutation round, and between them every import shape, both glob splits, and a
link that crosses a glob boundary (bd ``br7jb``; the generator's three
dimensions are :mod:`weld.tests._equivalence_sweep_repo`'s).

Without it the tier can decay silently in either direction: seeds swapped one at
a time until every case draws the same shape, or a shape added to
``IMPORT_SHAPES`` that no smoke case ever exercises. Both leave a green
tier. That is ADR 0139 mechanism 1's failure -- a check that has quietly
stopped checking -- applied to a test set rather than to a payload.

Pure generator work, no discovery: drawing a case is a ``random.Random`` walk
and a few string builds, so this target costs milliseconds and the real per-seed
runs stay in :mod:`weld.tests.incremental_generative_seed_case_test`. It is a
separate target rather than an extra class there for a caching reason: reading
the whole set would put every seed in each per-seed target's action key, so
adding one seed would invalidate all the others -- exactly the per-case caching
the split exists to buy.
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Mapping

from weld.tests._equivalence_sweep_repo import IMPORT_SHAPES, ROUNDS, generate_case

#: Bazel ``env`` key carrying the whole declared set, comma-separated.
SEEDS_ENV = "WELD_SWEEP_SEEDS"


def declared_seeds(environ: Mapping[str, str] | None = None) -> tuple[int, ...]:
    """Every seed the macro expanded into a target, or a hard failure.

    Fail-closed for the same reason as the per-seed runner's
    :func:`weld.tests.incremental_generative_seed_case_test.declared_seed`: an
    empty set satisfies no coverage assertion honestly, so it must not be
    reachable as a default.
    """
    raw = (os.environ if environ is None else environ).get(SEEDS_ENV)
    if raw is None or not raw.strip():
        raise RuntimeError(
            f"{SEEDS_ENV} is unset or blank -- this target must be declared by "
            "equivalence_tests() in weld/tests/equivalence_tests.bzl, which "
            "supplies the seed set"
        )
    return tuple(int(part) for part in raw.split(","))


class SmokeSeedCoverageTest(unittest.TestCase):
    """What the declared set has to span to be worth a fast-loop target each.

    Spanning is all this polices. The set may also carry a seed for its own
    sake -- bd rwi34's regression case is one -- so these are floors on what
    the tier reaches, never a claim that it is exactly one seed per round.
    """

    def setUp(self) -> None:
        self.seeds = declared_seeds()
        self.cases = [generate_case(seed) for seed in self.seeds]

    def test_every_mutation_round_has_a_seed(self) -> None:
        """Each round is a different incremental code path, so each needs one.

        Deletion rounds reach the provenance purge and the closure passes'
        undos; the edit rounds do not. A tier missing one is silent about it.
        """
        self.assertEqual({case.round for case in self.cases}, set(ROUNDS))

    def test_every_import_shape_is_drawn(self) -> None:
        """A shape no smoke case draws is a resolution path this tier misses.

        Add one to ``IMPORT_SHAPES`` and this fails until a declared seed draws
        it -- which is the intended cost of adding a shape, not an obstacle.
        """
        drawn = {shape for case in self.cases for _, _, shape in case.links}
        self.assertEqual(drawn, set(IMPORT_SHAPES))

    def test_both_glob_splits_are_drawn(self) -> None:
        self.assertEqual({len(case.declared) for case in self.cases}, {2, 3})

    def test_some_case_links_across_a_glob_boundary(self) -> None:
        """The merged-view derivations are unreachable without one.

        bd ``yhz70`` is the divergence that exists only when a call's target
        sits in a glob its caller's does not own.
        """
        crossings = sum(
            any(
                case.packages[caller].root != case.packages[target].root
                for caller, target, _ in case.links
            )
            for case in self.cases
        )
        self.assertGreater(crossings, 0)


class SeedSetDeclarationTest(unittest.TestCase):
    """An undeclared set errors rather than vacuously covering nothing."""

    def test_a_missing_or_blank_declaration_is_an_error(self) -> None:
        for environ in ({}, {SEEDS_ENV: ""}, {SEEDS_ENV: "  "}):
            with self.subTest(environ=environ):
                with self.assertRaises(RuntimeError):
                    declared_seeds(environ)

    def test_a_declared_set_is_read_in_declaration_order(self) -> None:
        self.assertEqual(declared_seeds({SEEDS_ENV: "3,1,2"}), (3, 1, 2))


if __name__ == "__main__":
    unittest.main()
