"""Tests for the bench cpp grammar precondition itself (bd gjli).

A precondition is the one piece of a suite that nothing else covers: it
runs before the assertions, and on a healthy host it returns silently, so
every branch that matters -- the ones taken when a grammar is *absent* --
is unreachable on the machine running the tests. That is how the guard
this replaces went a whole issue cycle probing only the ``tree_sitter``
umbrella while its failure message claimed it was holding
``@pypi//tree_sitter_cpp`` in place too: nothing ever executed the branch
that would have said otherwise.

So the absence is injected here, both polarities, rather than waited for.
:func:`test_cpp_grammar_missing_is_reported` is the direct regression pin
for bd gjli -- it fails against a guard that probes the umbrella alone.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


from weld.bench.adapters import weld as weld_adapter  # noqa: E402

from weld.tests.bench import bench_grammar_precondition as precondition  # noqa: E402


class _Recorder(unittest.TestCase):
    """A throwaway case to receive ``fail`` / ``skipTest`` calls.

    The no-op is deliberately not named ``runTest``: that name is a test
    method to the loader, so the recorder would be collected and reported
    as a passing test of nothing -- a phantom green in a file whose whole
    subject is guards that pass without checking anything.
    """

    def _noop(self) -> None:
        pass

    def __init__(self) -> None:
        super().__init__("_noop")


def _no_blocker_under_bazel() -> dict:
    return {"TEST_SRCDIR": "/fake/runfiles", precondition.BLOCKER_ENV: ""}


class MissingGrammarProbeTest(unittest.TestCase):
    """What the probe reports, with each module's absence injected."""

    def test_both_present_under_bazel_reports_nothing_missing(self) -> None:
        # The wiring pin, and the reason this target declares both
        # wheels: under Bazel they are in the runfiles by declaration, so
        # anything missing here means a dep regressed in BUILD.bazel.
        # Drop @pypi//tree_sitter_cpp from this target and this fails --
        # which is the enforcement bd gjli found the old guard lacking.
        if not os.environ.get("TEST_SRCDIR"):
            self.skipTest("not running under Bazel; the wheels are a host fact here")
        if os.environ.get(precondition.BLOCKER_ENV) == "1":
            self.skipTest("an operator-declared tree-sitter blocker is active")
        self.assertEqual((), precondition.missing_grammar_modules())

    def test_umbrella_missing_is_reported(self) -> None:
        with patch.object(
            weld_adapter, "_is_tree_sitter_available", return_value=False,
        ):
            missing = precondition.missing_grammar_modules()
        self.assertIn(precondition.UMBRELLA_MODULE, missing)

    def test_cpp_grammar_missing_is_reported(self) -> None:
        # bd gjli regression pin. The umbrella imports fine; only the cpp
        # grammar is gone -- exactly the case the previous guard waved
        # through while claiming to enforce it.
        with patch.object(precondition, "grammar_available", return_value=False):
            missing = precondition.missing_grammar_modules()
        self.assertIn(precondition.GRAMMAR_MODULE, missing)
        self.assertNotIn(precondition.UMBRELLA_MODULE, missing)

    def test_grammar_probe_asks_about_cpp(self) -> None:
        # Not just "some grammar": the language probed must be the one
        # the bench cases parse.
        with patch.object(
            precondition, "grammar_available", return_value=True,
        ) as probe:
            precondition.missing_grammar_modules()
        probe.assert_called_once_with("cpp")


class RequiredLabelsTest(unittest.TestCase):
    """The diagnostic may only name wheels the probe actually imports."""

    def test_labels_are_derived_from_the_probed_modules(self) -> None:
        # The defect bd gjli filed was a message naming a wheel nothing
        # checked. Deriving both from the probed module names is what
        # makes that unrepresentable, so pin the derivation.
        self.assertEqual(
            (
                f"@pypi//{precondition.UMBRELLA_MODULE}",
                f"@pypi//{precondition.GRAMMAR_MODULE}",
            ),
            precondition.REQUIRED_LABELS,
        )

    def test_grammar_module_matches_the_strategy_convention(self) -> None:
        # Derived via grammar_module_name rather than spelled out, so the
        # guard imports the same module load_ts_language will.
        self.assertEqual("tree_sitter_cpp", precondition.GRAMMAR_MODULE)


class GuardBranchTest(unittest.TestCase):
    """Each branch the guard takes when a module is absent."""

    def test_returns_silently_when_nothing_is_missing(self) -> None:
        precondition.skip_or_fail_without_grammars(_Recorder(), missing=())

    def test_fails_under_bazel_when_a_module_is_missing(self) -> None:
        with patch.dict(os.environ, _no_blocker_under_bazel()):
            with self.assertRaises(AssertionError) as raised:
                precondition.skip_or_fail_without_grammars(
                    _Recorder(), missing=(precondition.GRAMMAR_MODULE,),
                )
        message = str(raised.exception)
        # The diagnostic must name what is missing, both labels to
        # restore, and the BUILD file that declares them -- a fixer sent
        # to the wrong file is the failure mode this replaces.
        self.assertIn(precondition.GRAMMAR_MODULE, message)
        for label in precondition.REQUIRED_LABELS:
            self.assertIn(label, message)
        self.assertIn(precondition.DECLARED_IN, message)

    def test_failure_is_not_a_skip(self) -> None:
        # A skip would colour the regressed wiring green, which is the
        # whole point of failing instead.
        with patch.dict(os.environ, _no_blocker_under_bazel()):
            with self.assertRaises(AssertionError) as raised:
                precondition.skip_or_fail_without_grammars(
                    _Recorder(), missing=(precondition.UMBRELLA_MODULE,),
                )
        self.assertNotIsInstance(raised.exception, unittest.SkipTest)

    def test_skips_when_an_operator_declares_a_blocker(self) -> None:
        # The kept escape hatch (bd 2z8w): the variable has no producer in
        # this tree (ADR 0104), so its presence means an operator asked
        # for the block -- the one case where "restore the deps" would be
        # wrong advice.
        env = {"TEST_SRCDIR": "/fake/runfiles", precondition.BLOCKER_ENV: "1"}
        with patch.dict(os.environ, env):
            with self.assertRaises(unittest.SkipTest) as raised:
                precondition.skip_or_fail_without_grammars(
                    _Recorder(), missing=(precondition.GRAMMAR_MODULE,),
                )
        reason = str(raised.exception)
        self.assertIn(precondition.BLOCKER_ENV, reason)
        # The skip must be attributable, not merely silent.
        self.assertIn("operator", reason)

    def test_skips_outside_bazel(self) -> None:
        # Off the Bazel path the wheels are a host fact, so absence is
        # legitimate and skipping is correct.
        env = {precondition.BLOCKER_ENV: ""}
        with patch.dict(os.environ, env):
            os.environ.pop("TEST_SRCDIR", None)
            with self.assertRaises(unittest.SkipTest) as raised:
                precondition.skip_or_fail_without_grammars(
                    _Recorder(), missing=(precondition.GRAMMAR_MODULE,),
                )
        self.assertIn("not running under Bazel", str(raised.exception))

    def test_reason_names_every_missing_module(self) -> None:
        with patch.dict(os.environ, _no_blocker_under_bazel()):
            with self.assertRaises(AssertionError) as raised:
                precondition.skip_or_fail_without_grammars(
                    _Recorder(),
                    missing=(
                        precondition.UMBRELLA_MODULE,
                        precondition.GRAMMAR_MODULE,
                    ),
                )
        message = str(raised.exception)
        self.assertIn(precondition.UMBRELLA_MODULE, message)
        self.assertIn(precondition.GRAMMAR_MODULE, message)


if __name__ == "__main__":
    unittest.main()
