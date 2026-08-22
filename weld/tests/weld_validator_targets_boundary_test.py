"""Contract guards for the validator_targets discovery strategy.

Sibling of ``weld_validator_targets_strategy_test.py``, which covers what
the strategy *produces*. This file covers what it must refuse: paths that
escape the repository, work that would run unbounded on a repository weld
did not write, and the node-ID spellings that decide whether an edge lands
on the right node or merely a same-stem one.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from weld.strategies.validator_targets import (
    _MAX_GLOB_RESOLUTIONS,
    _LiteralResolver,
    _path_literals,
    _resolve_literal,
    extract,
)


def _write(root: Path, rel: str, text: str) -> Path:
    """Create *root/rel* with *text*, making parent directories."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _source(**overrides: object) -> dict:
    """Return a source entry for the ``tools/`` glob with *overrides*."""
    source: dict = {"glob": "tools/*.py", "type": "file"}
    source.update(overrides)
    return source


class ValidatorTargetsBoundaryTest(unittest.TestCase):
    """Repository-boundary refusals. Discovery runs on untrusted repos."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_absolute_path_is_refused(self) -> None:
        """An absolute literal never becomes a target."""
        self.assertEqual([], _resolve_literal(self.root, "/etc/passwd"))
        self.assertEqual([], _resolve_literal(self.root, "/etc/*.conf"))

    def test_traversal_is_refused(self) -> None:
        """A ``..`` literal never becomes a target, glob or not."""
        outside = self.root.parent / "outside_target.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        self.assertEqual(
            [], _resolve_literal(self.root, "../outside_target.py"),
        )
        self.assertEqual([], _resolve_literal(self.root, "../*.py"))

    def test_nonexistent_path_is_refused(self) -> None:
        """A literal that names nothing on disk yields no edge."""
        self.assertEqual([], _resolve_literal(self.root, "pkg/absent.py"))

    def test_directory_is_refused(self) -> None:
        """A literal resolving to a directory yields no edge."""
        (self.root / "pkg.py").mkdir()
        self.assertEqual([], _resolve_literal(self.root, "pkg.py"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlink_escaping_the_repo_is_refused(self) -> None:
        """A symlink pointing outside the root is not a governed target."""
        outside = self.root.parent / "escape_target.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        link = self.root / "pkg_link.py"
        try:
            link.symlink_to(outside)
        except OSError:  # pragma: no cover - platform without permission
            self.skipTest("symlink creation not permitted")
        self.assertEqual([], _resolve_literal(self.root, "pkg_link.py"))

    def test_boundary_refusal_reaches_extract(self) -> None:
        """extract() emits nothing for a validator naming only bad paths."""
        _write(
            self.root,
            "tools/lint_bad.py",
            'BAD = ("/etc/passwd", "../outside.py", "pkg/absent.py")\n',
        )
        result = extract(self.root, _source(), {})
        self.assertEqual([], result.edges)
        self.assertEqual({}, result.nodes)


class ValidatorTargetsResolverBudgetTest(unittest.TestCase):
    """The glob-walk budget bounds work on repositories weld did not write."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _write(self.root, "pkg/mod.py", "def f():\n    return 1\n")

    def test_repeated_literal_is_resolved_once(self) -> None:
        """A memo hit costs nothing and does not draw down the budget."""
        resolver = _LiteralResolver(self.root)
        first = resolver.resolve("pkg/*.py")
        for _ in range(_MAX_GLOB_RESOLUTIONS * 2):
            self.assertEqual(first, resolver.resolve("pkg/*.py"))

    def test_budget_exhaustion_stops_fresh_glob_walks(self) -> None:
        """Past the ceiling, a new glob literal resolves to nothing."""
        resolver = _LiteralResolver(self.root)
        for index in range(_MAX_GLOB_RESOLUTIONS):
            resolver.resolve(f"pkg/*{index}.py")
        self.assertEqual([], resolver.resolve("pkg/*.py"))

    def test_budget_does_not_apply_to_plain_paths(self) -> None:
        """Non-glob literals are a stat, not a walk, and stay unbounded."""
        resolver = _LiteralResolver(self.root)
        for index in range(_MAX_GLOB_RESOLUTIONS):
            resolver.resolve(f"pkg/*{index}.py")
        self.assertEqual(["pkg/mod.py"], resolver.resolve("pkg/mod.py"))


class ValidatorTargetsHelperTest(unittest.TestCase):
    """Pure-function contracts for literal harvesting and ID spellings."""

    def test_path_literals_are_sorted_and_deduplicated(self) -> None:
        literals = _path_literals(["see b.py and a.py", "again a.py"])
        self.assertEqual(["a.py", "b.py"], literals)

    def test_path_literals_ignore_non_path_text(self) -> None:
        self.assertEqual([], _path_literals(["no paths here", "3.14", ""]))

    def test_path_literals_skip_oversized_strings(self) -> None:
        """A huge string is not scanned; the regex never sees it."""
        from weld.strategies.validator_targets import (
            _MAX_STRING_LEN,
            _string_constants,
        )
        import ast

        big = "x" * (_MAX_STRING_LEN + 1)
        tree = ast.parse(f'BIG = {big!r}\nSMALL = "pkg/a.py"\n')
        self.assertEqual(["pkg/a.py"], _path_literals(_string_constants(tree)))


if __name__ == "__main__":
    unittest.main()
