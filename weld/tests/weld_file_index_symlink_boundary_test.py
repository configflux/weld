"""The ``wd find`` index never reads through a symlink.

The file index opened and tokenized whatever a tracked symlink resolved to,
including a target outside the checkout, so content weld was never given
could land in a searchable ``.weld/file-index.json``::

    repo/linked.sh -> /outside/outside.sh    indexed via the extension rule
    repo/linked    -> /outside/outside.sh    indexed via the shebang rule

Git tracks a symlink as an ordinary entry, so it arrives from
``iter_repo_files`` looking like any other file and both admission rules
accepted it. The ``.sh`` shape is what dates the defect: it predates the
extensionless-shebang rule (bd 0edz), which extended the same behaviour to
one more filename shape rather than creating it (bd a2gr).

The rule is that a symlink is not index surface, checked before any
allow-list so no extension can route around it. That matches the two
places the repo already states this posture --
``validator_targets._safe_direct_path`` drops symlinks outright and
``glob_match.walk_glob`` never follows them -- rather than making the index
a third, weaker policy that has to resolve correctly through chains, races,
and bind mounts to be worth anything.

Both consumers of the predicate are pinned here, because a skip in the full
walk that the incremental hash surface did not share would re-add and
re-drop the file on alternating refreshes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._file_index_coverage import surface_paths
from weld._file_index_incremental import _surface_hashes
from weld.file_index import _is_indexed_file, build_file_index
from weld.file_index_search import find_files

#: A token that exists only in the out-of-repo file. Its presence anywhere
#: in the index is proof the boundary was crossed.
_OUTSIDE_TOKEN = "OutsideSecretHelper"

_OUTSIDE_BODY = (
    "#!/usr/bin/env bash\n"
    f"# {_OUTSIDE_TOKEN}\n"
    f"{_OUTSIDE_TOKEN}() {{ echo out; }}\n"
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class SymlinkIsNotIndexSurfaceTest(unittest.TestCase):
    """The predicate itself, over both admission rules the report named."""

    def setUp(self) -> None:
        super().setUp()
        self._repo = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo.cleanup)
        self.addCleanup(self._outside.cleanup)
        self.root = Path(self._repo.name)
        self.outside = _write(
            Path(self._outside.name) / "outside.sh", _OUTSIDE_BODY
        )

    def test_extension_rule_does_not_admit_an_escaping_symlink(self) -> None:
        link = self.root / "linked.sh"
        link.symlink_to(self.outside)
        # It would sail through the allow-list on its name alone.
        self.assertEqual(link.suffix, ".sh")
        self.assertFalse(_is_indexed_file(link))

    def test_shebang_rule_does_not_admit_an_escaping_symlink(self) -> None:
        link = self.root / "linked"
        link.symlink_to(self.outside)
        # And through the shebang rule on its first two bytes.
        self.assertEqual(link.suffix, "")
        self.assertFalse(_is_indexed_file(link))

    def test_in_repo_symlink_is_skipped_too(self) -> None:
        """Not only the escaping ones -- see the module docstring.

        The cost is small and recoverable: the alias loses its own entry
        while the file it points at is indexed on its own name.
        """
        real = _write(self.root / "real.py", "REAL_CONSTANT = 1\n")
        alias = self.root / "alias.py"
        alias.symlink_to(real)
        self.assertFalse(_is_indexed_file(alias))
        self.assertTrue(_is_indexed_file(real))

    def test_a_real_file_beside_the_link_is_still_admitted(self) -> None:
        """The skip is targeted, not a blanket refusal of the directory."""
        (self.root / "linked.sh").symlink_to(self.outside)
        self.assertTrue(_is_indexed_file(_write(self.root / "m.py", "x = 1\n")))

    def test_broken_symlink_is_skipped_without_raising(self) -> None:
        link = self.root / "dangling.py"
        link.symlink_to(self.root / "never-existed.py")
        self.assertFalse(_is_indexed_file(link))


class OutsideContentStaysOutOfTheIndexTest(unittest.TestCase):
    """End to end: the outside file's tokens must not be findable."""

    def _repo_with_link(self, link_name: str) -> tuple[Path, dict]:
        repo = tempfile.TemporaryDirectory()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(repo.cleanup)
        self.addCleanup(outside.cleanup)
        root = Path(repo.name)
        target = _write(Path(outside.name) / "outside.sh", _OUTSIDE_BODY)
        (root / link_name).symlink_to(target)
        _write(root / "kept.py", "KEPT_CONSTANT = 1\n")
        return root, build_file_index(root)

    def test_escaping_sh_symlink_contributes_nothing(self) -> None:
        root, index = self._repo_with_link("linked.sh")
        self.assertNotIn("linked.sh", index)
        self.assertIn("kept.py", index)
        self.assertNotIn(
            _OUTSIDE_TOKEN,
            [token for tokens in index.values() for token in tokens],
        )
        self.assertEqual(find_files(index, _OUTSIDE_TOKEN)["files"], [])

    def test_escaping_extensionless_symlink_contributes_nothing(self) -> None:
        _, index = self._repo_with_link("linked")
        self.assertNotIn("linked", index)
        self.assertEqual(find_files(index, _OUTSIDE_TOKEN)["files"], [])


class SurfaceConsumersAgreeTest(unittest.TestCase):
    """All three callers of the predicate must see the same surface."""

    def test_symlink_is_absent_from_every_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as out:
            root = Path(tmp)
            target = _write(Path(out) / "outside.sh", _OUTSIDE_BODY)
            (root / "linked.sh").symlink_to(target)
            _write(root / "kept.py", "KEPT_CONSTANT = 1\n")

            self.assertNotIn("linked.sh", build_file_index(root))
            self.assertNotIn("linked.sh", _surface_hashes(root))
            self.assertNotIn("linked.sh", surface_paths(root))
            # The agreement is only meaningful if the surface is non-empty.
            self.assertIn("kept.py", _surface_hashes(root))
            self.assertIn("kept.py", surface_paths(root))


@unittest.skipIf(shutil.which("git") is None, "git not available")
class TrackedSymlinkTest(unittest.TestCase):
    """The reported shape: a symlink *committed* to the repository.

    The non-git tests above exercise the ``os.walk`` fallback in
    ``iter_repo_files``. The report came from the git-backed path, where
    ``git ls-files`` lists a symlink as an ordinary entry -- so that path
    needs its own pin rather than inheriting one.
    """

    def test_tracked_escaping_symlink_is_not_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as out:
            root = Path(tmp)
            target = _write(Path(out) / "outside.sh", _OUTSIDE_BODY)
            (root / "linked.sh").symlink_to(target)
            _write(root / "kept.py", "KEPT_CONSTANT = 1\n")

            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "weld-test",
                "GIT_AUTHOR_EMAIL": "weld-test@example.invalid",
                "GIT_COMMITTER_NAME": "weld-test",
                "GIT_COMMITTER_EMAIL": "weld-test@example.invalid",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
            }
            for argv in (
                ["init", "--quiet"],
                ["add", "linked.sh", "kept.py"],
                ["commit", "--quiet", "-m", "tracked symlink"],
            ):
                subprocess.run(
                    ["git", "-C", str(root), *argv],
                    check=True, capture_output=True, env=env,
                )

            # Precondition: git really does track it as a symlink, so the
            # test would fail loudly if the fixture stopped reproducing.
            listed = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-s", "linked.sh"],
                check=True, capture_output=True, text=True, env=env,
            ).stdout
            self.assertTrue(listed.startswith("120000"), listed)

            index = build_file_index(root)
            self.assertNotIn("linked.sh", index)
            self.assertIn("kept.py", index)
            self.assertEqual(find_files(index, _OUTSIDE_TOKEN)["files"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
