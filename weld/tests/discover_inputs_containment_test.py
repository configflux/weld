"""Direct containment tests for ``weld._discover_inputs`` (bd 566g).

``discovered_from`` is graph-authored data, so both ``_within_root`` and
``stale_directory_marker`` treat it as untrusted: an entry that does not
resolve to a real path under *root* -- a lexical ``..`` escape, or a symlink
whose target sits outside the tree -- must never reach a stat call on the
unresolved path (see the module docstring on ``weld/_discover_inputs.py``).
This is the read-side half of the same untrusted-graph-data invariant
``weld_bazel_loads_containment_test`` guards on the write side.

Both functions were previously exercised only behaviorally, through
``_discover_single_repo`` fixtures (bd 0t5p, bd a4q8) -- the escape branch
itself had no direct test, so a regression that stat'd the unresolved path
instead of the ``resolve()`` + ``relative_to()``-checked one would not have
been caught. This module pins the resolved-path discipline directly, at the
function, for both the lexical-escape and symlink-escape shapes, plus the
two non-escaping shapes (a same-tree symlink, and an ordinary contained
entry) that the escape check must not also reject.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._discover_inputs import _within_root, stale_directory_marker


class _ArenaTestCase(unittest.TestCase):
    """``root`` and ``outside`` as siblings, so ``../outside/...`` is always
    a clean, one-hop lexical escape regardless of the platform temp dir."""

    def setUp(self) -> None:
        self._arena = tempfile.TemporaryDirectory()
        arena = Path(self._arena.name)
        self.root = arena / "root"
        self.outside = arena / "outside"
        self.root.mkdir()
        self.outside.mkdir()

    def tearDown(self) -> None:
        self._arena.cleanup()


class WithinRootContainmentTest(_ArenaTestCase):
    """``_within_root`` refuses anything whose real bytes live outside
    *root*, however the path that names it is spelled."""

    def test_dotdot_escape_is_refused(self) -> None:
        secret = self.outside / "secret.py"
        secret.write_text("STOLEN = 1\n", encoding="utf-8")
        self.assertFalse(_within_root(self.root, "../outside/secret.py"))

    def test_symlink_out_of_tree_is_refused(self) -> None:
        target = self.outside / "secret.py"
        target.write_text("STOLEN = 1\n", encoding="utf-8")
        (self.root / "escape.py").symlink_to(target)
        self.assertFalse(_within_root(self.root, "escape.py"))

    def test_symlink_inside_tree_resolves_to_contained_target(self) -> None:
        """Characterization: ``resolve()`` follows the symlink to its real
        target, which sits under root, so ``relative_to`` succeeds and
        ``is_file()`` sees a regular file -- the check is about where the
        bytes live (the docstring's own words), and a same-tree symlink's
        bytes live in-tree, so this is accepted, unlike the out-of-tree
        case above."""
        real = self.root / "real.py"
        real.write_text("x = 1\n", encoding="utf-8")
        (self.root / "link.py").symlink_to(real)
        self.assertTrue(_within_root(self.root, "link.py"))

    def test_ordinary_contained_file_passes(self) -> None:
        (self.root / "plain.py").write_text("x = 1\n", encoding="utf-8")
        self.assertTrue(_within_root(self.root, "plain.py"))


class StaleDirectoryMarkerContainmentTest(_ArenaTestCase):
    """``stale_directory_marker`` mirrors ``_within_root``'s containment
    discipline for the trailing-slash directory-marker shape: an escaping
    entry counts as stale and is dropped rather than stat'd."""

    def test_dotdot_escape_is_treated_as_stale(self) -> None:
        (self.outside / "pkg").mkdir()
        self.assertTrue(stale_directory_marker(self.root, "../outside/pkg/"))

    def test_symlink_out_of_tree_is_treated_as_stale(self) -> None:
        target_dir = self.outside / "pkg"
        target_dir.mkdir()
        (self.root / "pkg").symlink_to(target_dir, target_is_directory=True)
        self.assertTrue(stale_directory_marker(self.root, "pkg/"))

    def test_symlink_to_contained_directory_is_not_stale(self) -> None:
        """Characterization: same resolve()-follows-the-symlink behavior as
        ``_within_root`` above -- the marker's target directory is under
        root, so ``is_dir()`` is True and the marker is kept, not dropped."""
        real_dir = self.root / "real_pkg"
        real_dir.mkdir()
        (self.root / "linked_pkg").symlink_to(real_dir, target_is_directory=True)
        self.assertFalse(stale_directory_marker(self.root, "linked_pkg/"))

    def test_existing_directory_entry_is_not_stale(self) -> None:
        (self.root / "pkg").mkdir()
        self.assertFalse(stale_directory_marker(self.root, "pkg/"))


if __name__ == "__main__":
    unittest.main()
