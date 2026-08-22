"""Tests for :mod:`weld._gitattributes_writer` (ADR 0110).

Two halves, and the second is the one that matters. Writing the file is
easy to get right; what this module actually claims is that the resulting
pair -- a tracked ``.gitattributes`` plus a registered driver -- makes git
resolve a real conflict on a tracked graph without leaving markers. So the
merge cases run ``git`` against a scratch repository rather than asserting
on the text.

The negative case is deliberate too: without the registration, git must
still conflict. That is what tells a reader the registration is
load-bearing and not decoration -- ``ours`` looks like a git built-in and
is not (only ``text``, ``binary`` and ``union`` are).
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._gitattributes_writer import (  # noqa: E402
    MERGE_DRIVER_NAME,
    TRACK_GRAPHS_GITATTRIBUTES,
    ManagedPolicy,
    _ignore_in_effect,
    register_merge_driver,
    write_repo_git_policy,
    write_weld_gitattributes,
)


#: Global and system git config are scrubbed, not merely overridden. The
#: negative case below asserts that git *does* conflict without the local
#: registration -- which a developer who had once set
#: ``merge.weld-regenerable.driver`` globally would silently invert into a
#: pass. Signing and templates are ruled out for the same reason.
_HERMETIC_GIT = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "LC_ALL": "C",
}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        check=False, env=_HERMETIC_GIT, timeout=30,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "weld test")
    _git(root, "config", "commit.gpgsign", "false")


class WriteWeldGitattributesTest(unittest.TestCase):
    def test_writes_track_graphs_content(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            self.assertTrue(write_weld_gitattributes(weld_dir))
            self.assertEqual(
                (weld_dir / ".gitattributes").read_text(encoding="utf-8"),
                TRACK_GRAPHS_GITATTRIBUTES,
            )

    def test_idempotent_skip_if_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitattributes").write_text("mine\n", encoding="utf-8")
            self.assertFalse(write_weld_gitattributes(weld_dir))
            self.assertEqual(
                (weld_dir / ".gitattributes").read_text(encoding="utf-8"), "mine\n",
            )

    def test_creates_weld_dir_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / "nested" / ".weld"
            self.assertTrue(write_weld_gitattributes(weld_dir))
            self.assertTrue((weld_dir / ".gitattributes").is_file())

    def test_covers_every_artifact_mode_b_tracks(self) -> None:
        """The attributes file and the ignore policy must not drift apart.

        Anything Mode B commits and weld regenerates has to carry the
        merge rule; an artifact tracked without it is the conflict this
        ADR exists to remove, quietly reintroduced.
        """
        for name in (
            "graph.json",
            "agent-graph.json",
            "discovery-state.json",
            "file-index.json",
            "file-index-state.json",
        ):
            self.assertIn(
                f"\n{name} merge={MERGE_DRIVER_NAME}\n",
                "\n" + TRACK_GRAPHS_GITATTRIBUTES,
                f"{name} is tracked in Mode B but carries no merge rule",
            )

    def test_header_names_the_clone_side_command(self) -> None:
        """A clone inherits the file but not the config; say so in the file."""
        self.assertIn(
            f"git config merge.{MERGE_DRIVER_NAME}.driver true",
            TRACK_GRAPHS_GITATTRIBUTES,
        )


class RegisterMergeDriverTest(unittest.TestCase):
    def test_registers_in_local_config(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            self.assertTrue(register_merge_driver(root))
            got = _git(root, "config", "--local", f"merge.{MERGE_DRIVER_NAME}.driver")
            self.assertEqual(got.stdout.strip(), "true")

    def test_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            self.assertTrue(register_merge_driver(root))
            self.assertTrue(register_merge_driver(root))

    def test_reports_failure_outside_a_git_checkout(self) -> None:
        """Not a git repo: report it, never raise -- init must still finish."""
        with TemporaryDirectory() as tmp:
            self.assertFalse(register_merge_driver(Path(tmp)))


class MergeResolutionTest(unittest.TestCase):
    """The behaviour the whole module exists for, exercised through git."""

    def _repo_with_conflict(self, root: Path) -> None:
        """Two branches that rewrote the same tracked-graph line."""
        _init_repo(root)
        weld = root / ".weld"
        weld.mkdir()
        write_weld_gitattributes(weld)
        (weld / "graph.json").write_text('{\n"nodes": {},\n"edges": []\n}\n')
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "base")
        _git(root, "checkout", "-qb", "feature")
        (weld / "graph.json").write_text('{\n"nodes": {"a": 1},\n"edges": []\n}\n')
        _git(root, "commit", "-qam", "feature graph")
        _git(root, "checkout", "-q", "main")
        (weld / "graph.json").write_text('{\n"nodes": {"b": 2},\n"edges": []\n}\n')
        _git(root, "commit", "-qam", "main graph")

    def test_registered_driver_resolves_without_markers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo_with_conflict(root)
            register_merge_driver(root)
            merged = _git(root, "merge", "feature", "-m", "merge")
            self.assertEqual(merged.returncode, 0, merged.stderr)
            body = (root / ".weld" / "graph.json").read_text(encoding="utf-8")
            self.assertNotIn("<<<<<<<", body)
            # "Keep ours": the current branch's copy survives, and the
            # merged HEAD is what the next read then finds it behind.
            self.assertIn('"b": 2', body)
            self.assertNotIn('"a": 1', body)
            self.assertEqual(_git(root, "ls-files", "-u").stdout.strip(), "")

    def test_without_registration_git_still_conflicts(self) -> None:
        """`merge=ours` is not a git built-in; the config is load-bearing."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo_with_conflict(root)
            merged = _git(root, "merge", "feature", "-m", "merge")
            self.assertNotEqual(merged.returncode, 0)
            self.assertIn(
                "<<<<<<<", (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
            )


class IgnoreInEffectNonUtf8Test(unittest.TestCase):
    """Regression: a non-UTF-8 `.weld/.gitignore` must degrade, not crash.

    `UnicodeDecodeError` is a `ValueError` subclass, not an `OSError`, so the
    original ``except OSError`` around the read in `_ignore_in_effect` let it
    escape uncaught -- crashing `wd init` / `wd workspace bootstrap` instead
    of reporting the mode as not in effect the way an unreadable file already
    does. Mirrors :mod:`weld._gitignore_writer`'s own
    `resync_weld_gitignore`, which already treats
    ``except (OSError, UnicodeDecodeError)`` as "leave it alone" for the same
    file.
    """

    _NON_UTF8 = b"\xff\xfe# not valid utf-8\n"

    def test_ignore_in_effect_degrades_instead_of_raising(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_bytes(self._NON_UTF8)
            policy = _ignore_in_effect(
                root, weld_dir, ignore_all=False, track_graphs=False,
            )
            self.assertEqual(policy, ManagedPolicy(False, None))

    def test_write_repo_git_policy_does_not_crash_on_non_utf8_gitignore(self) -> None:
        """The exact entry point the bug report names: `wd init`'s call chain."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_bytes(self._NON_UTF8)
            policy = write_repo_git_policy(root, weld_dir, announce=False)
            self.assertEqual(policy, ManagedPolicy(False, None))
            # Untouched: an unreadable file is reported on, never rewritten.
            self.assertEqual(
                (weld_dir / ".gitignore").read_bytes(), self._NON_UTF8,
            )


if __name__ == "__main__":
    unittest.main()
