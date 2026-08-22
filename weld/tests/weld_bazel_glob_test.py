"""Bounded ``glob()`` evaluation in BUILD files (bd mhn7, ADR 0025/0108) and
inside macro bodies via ``native.glob()`` (bd x9lg, ADR 0044 amendment).

The rule under test is "never invent a member". Every case below is a way the
evaluator could hand back a file the package does not actually own -- crossing
a subpackage boundary, ignoring an ``exclude`` it could not read, following a
symlink, or truncating a listing so it merely *looks* complete -- plus, for
the macro-body cases, resolving against the wrong package's files entirely.
"""

from __future__ import annotations

import ast
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from weld.strategies import _bazel_glob
from weld.strategies._bazel_eval import (
    GLOB_RESOLVER_KEY,
    UNEVALUATABLE,
    eval_expr,
)
from weld.strategies._bazel_glob import compile_pattern, evaluate_glob
from weld.strategies._bazel_starlark import parse_targets


def _write(root: Path, rel: str, text: str = "x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class PatternTest(unittest.TestCase):

    def test_star_does_not_cross_a_separator(self) -> None:
        rx = compile_pattern("*.py")
        self.assertTrue(rx.match("a.py"))
        self.assertFalse(rx.match("sub/a.py"))

    def test_doublestar_matches_zero_or_more_segments(self) -> None:
        rx = compile_pattern("**/*.py")
        self.assertTrue(rx.match("a.py"))
        self.assertTrue(rx.match("x/a.py"))
        self.assertTrue(rx.match("x/y/z/a.py"))
        self.assertFalse(rx.match("a.txt"))

    def test_trailing_doublestar_matches_everything_below(self) -> None:
        rx = compile_pattern("static/**")
        self.assertTrue(rx.match("static/a.css"))
        self.assertTrue(rx.match("static/deep/a.css"))
        self.assertFalse(rx.match("other/a.css"))

    def test_a_pattern_is_anchored_at_both_ends(self) -> None:
        rx = compile_pattern("a.py")
        self.assertFalse(rx.match("xa.py"))
        self.assertFalse(rx.match("a.pyc"))

    def test_adjacent_doublestars_collapse(self) -> None:
        """``**/**`` selects exactly what ``**`` selects."""
        for subject in ("a.py", "x/a.py", "x/y/z/a.py", "a.txt"):
            self.assertEqual(
                bool(compile_pattern("**/**/**/*.py").match(subject)),
                bool(compile_pattern("**/*.py").match(subject)),
                subject,
            )

    def test_repeated_doublestar_does_not_backtrack_catastrophically(self) -> None:
        """A hostile BUILD file must not be able to hang discovery (ADR 0025).

        Each ``**`` compiles to a ``(?:[^/]+/)*`` group, so an uncollapsed
        ``**/**/**/...`` stacks nested quantifiers over the same input -- the
        classic catastrophic-backtracking shape. Measured before the collapse,
        this exact case took 77 seconds to reject one path.
        """
        rx = compile_pattern("**/" * 12 + "*.py")
        # The collapse is structural: adjacent ``**`` runs compile to the
        # exact regex a single ``**`` compiles to, so there is only ever one
        # ``(?:[^/]+/)*`` group for the run and nothing left to backtrack.
        self.assertEqual(rx.pattern, compile_pattern("**/*.py").pattern)
        subject = "a/" * 24 + "b.txt"  # non-matching: forces the backtracking
        started = time.monotonic()
        self.assertIsNone(rx.match(subject))
        print(f"advisory: hostile-glob reject took {time.monotonic() - started:.3f}s")

    def test_traversal_patterns_select_nothing(self) -> None:
        """A pattern filters the package listing; it never drives the walk."""
        for evil in ("../../etc/passwd", "/etc/passwd", "../../../**/*"):
            self.assertEqual(compile_pattern(evil).match("a.py"), None, evil)


class EvaluateGlobTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_matches_files_the_package_owns(self) -> None:
        _write(self.root, "a.py")
        _write(self.root, "sub/b.py")
        self.assertEqual(
            evaluate_glob(["**/*.py"], [], self.root, {}), ["a.py", "sub/b.py"],
        )

    def test_stops_at_a_subpackage_boundary(self) -> None:
        """A nested BUILD file owns its own files; the parent may not claim them."""
        _write(self.root, "a.py")
        _write(self.root, "child/BUILD.bazel", "filegroup(name='c')")
        _write(self.root, "child/b.py")
        _write(self.root, "child/deep/c.py")
        self.assertEqual(evaluate_glob(["**/*.py"], [], self.root, {}), ["a.py"])

    def test_plain_BUILD_also_marks_a_subpackage(self) -> None:
        _write(self.root, "a.py")
        _write(self.root, "child/BUILD", "filegroup(name='c')")
        _write(self.root, "child/b.py")
        self.assertEqual(evaluate_glob(["**/*.py"], [], self.root, {}), ["a.py"])

    def test_exclude_removes_members(self) -> None:
        _write(self.root, "a.py")
        _write(self.root, "a_test.py")
        self.assertEqual(
            evaluate_glob(["*.py"], ["*_test.py"], self.root, {}), ["a.py"],
        )

    def test_directories_are_never_members(self) -> None:
        """``exclude_directories = 1`` is bazel's default and the only mode here."""
        _write(self.root, "pkgdir/a.py")
        self.assertEqual(evaluate_glob(["*"], [], self.root, {}), [])

    def test_symlinks_are_not_followed(self) -> None:
        _write(self.root, "real/a.py")
        try:
            os.symlink(self.root / "real", self.root / "link")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual(
            evaluate_glob(["**/*.py"], [], self.root, {}), ["real/a.py"],
        )

    def test_pruned_directories_are_not_walked(self) -> None:
        _write(self.root, "a.py")
        _write(self.root, ".git/objects/b.py")
        _write(self.root, "node_modules/pkg/c.py")
        self.assertEqual(evaluate_glob(["**/*.py"], [], self.root, {}), ["a.py"])

    def test_dotted_source_directories_are_kept(self) -> None:
        """bazel globs ``.weld`` / ``.github``; pruning them under-reported 34 files."""
        _write(self.root, ".weld/strategies/todo.py")
        self.assertEqual(
            evaluate_glob(["**/*.py"], [], self.root, {}),
            [".weld/strategies/todo.py"],
        )

    def test_over_the_file_cap_is_unevaluatable_not_truncated(self) -> None:
        """A partial list is indistinguishable from a complete one downstream."""
        _write(self.root, "a.py")
        with unittest.mock.patch.object(_bazel_glob, "MAX_GLOB_FILES", 0):
            self.assertIsNone(evaluate_glob(["**/*"], [], self.root, {}))

    def test_missing_directory_is_unevaluatable(self) -> None:
        self.assertIsNone(evaluate_glob(["*"], [], self.root / "nope", {}))

    def test_a_wide_empty_tree_is_bounded_too(self) -> None:
        """Directories count against the cap: an empty tree has no files to."""
        for i in range(6):
            (self.root / f"d{i}" / f"e{i}").mkdir(parents=True)
        with unittest.mock.patch.object(_bazel_glob, "MAX_GLOB_FILES", 3):
            self.assertIsNone(evaluate_glob(["**/*"], [], self.root, {}))

    def test_listing_is_memoised_per_package(self) -> None:
        _write(self.root, "a.py")
        cache: dict = {}
        evaluate_glob(["*.py"], [], self.root, cache)
        with unittest.mock.patch.object(
            _bazel_glob, "package_files", side_effect=AssertionError("re-walked"),
        ):
            self.assertEqual(evaluate_glob(["*.py"], [], self.root, cache), ["a.py"])


class GlobInBuildFileTest(unittest.TestCase):
    """The evaluator half: ``glob()`` only resolves with a resolver installed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _write(self.root, "a.py")
        _write(self.root, "b.py")
        _write(self.root, "a_test.py")

    def _bindings(self) -> dict:
        cache: dict = {}
        return {
            GLOB_RESOLVER_KEY: lambda inc, exc: evaluate_glob(
                inc, exc, self.root, cache,
            ),
        }

    def _eval(self, source: str, bindings: dict | None = None):
        node = ast.parse(source, mode="eval").body
        return eval_expr(node, self._bindings() if bindings is None else bindings)

    def test_glob_resolves_to_real_files(self) -> None:
        self.assertEqual(self._eval('glob(["*.py"])'), ["a.py", "a_test.py", "b.py"])

    def test_glob_honours_exclude(self) -> None:
        self.assertEqual(
            self._eval('glob(["*.py"], exclude = ["*_test.py"])'), ["a.py", "b.py"],
        )

    def test_glob_composes_with_list_concatenation(self) -> None:
        self.assertEqual(
            self._eval('glob(["b.py"]) + ["extra.py"]'), ["b.py", "extra.py"],
        )

    def test_without_a_resolver_glob_stays_unevaluatable(self) -> None:
        """ADR 0108's asymmetry: no filesystem context means no guess."""
        self.assertIs(self._eval('glob(["*.py"])', {}), UNEVALUATABLE)

    def test_unevaluatable_include_does_not_glob_a_guess(self) -> None:
        self.assertIs(self._eval("glob(SOMETHING)"), UNEVALUATABLE)

    def test_unevaluatable_exclude_does_not_yield_a_superset(self) -> None:
        """Ignoring an unreadable exclude would over-report membership."""
        self.assertIs(self._eval('glob(["*.py"], exclude = MYSTERY)'), UNEVALUATABLE)

    def test_exclude_directories_zero_is_declined(self) -> None:
        self.assertIs(
            self._eval('glob(["*"], exclude_directories = 0)'), UNEVALUATABLE,
        )

    def test_other_calls_are_still_unevaluatable(self) -> None:
        self.assertIs(self._eval('select({"//c": ["a.py"]})'), UNEVALUATABLE)
        self.assertIs(self._eval('my_macro(["a.py"])'), UNEVALUATABLE)

    def test_a_filegroup_srcs_glob_becomes_real_members(self) -> None:
        """The bd mhn7 headline: file -> filegroup is connected at the first hop."""
        targets = parse_targets(
            'filegroup(name = "fg", srcs = glob(["*.py"]))',
            ("filegroup",),
            self._bindings(),
        )
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["srcs"], ["a.py", "a_test.py", "b.py"])

    def test_native_glob_resolves_the_same_as_bare_glob(self) -> None:
        """``native.glob(...)`` is the only spelling legal inside a ``.bzl``
        macro body (bd x9lg); it must resolve identically to the bare form."""
        self.assertEqual(
            self._eval('native.glob(["*.py"])'), ["a.py", "a_test.py", "b.py"],
        )

    def test_native_prefixed_non_glob_attribute_is_still_unevaluatable(self) -> None:
        """The recognizer is exactly ``native.glob``, not every ``native.*``."""
        self.assertIs(self._eval('native.select({"//c": ["a.py"]})'), UNEVALUATABLE)
        self.assertIs(self._eval('other.glob(["*.py"])'), UNEVALUATABLE)


if __name__ == "__main__":
    unittest.main()
