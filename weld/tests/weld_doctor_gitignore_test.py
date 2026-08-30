"""Tests for the read-only ignore-policy doctor checks.

ADR 0131 gave `resync_weld_gitignore` (:mod:`weld._gitignore_writer`) a
write path that self-heals a `.weld/.gitignore` initialised before a
template line existed, but it only runs from `wd init` / `wd workspace
bootstrap`. `check_gitignore_resync` (:mod:`weld._doctor_gitignore`) is
the read-only counterpart: it asks the exact same recognition question
-- "is this file a clean subset of exactly one known template, and if
so, what does that template ship that this file lacks" -- and reports
it instead of acting on it, for checkouts that run `wd discover`
constantly and never re-run `wd init`.

Every case here mirrors a case in `weld_gitignore_resync_test.py`
because the two share one computation
(:func:`weld._gitignore_writer.missing_gitignore_lines`): a stale
recognized file warns, and everything the write path would leave
untouched (foreign content, a recognized-plus-one-custom-line file, a
near-empty file, an already-current file, a missing file, unreadable or
non-UTF-8 content) is silent here too.

`check_seeding_config` is the module's second check and a different
question about the same policy: not "is this file stale" but "does it
keep `.weld/discover.yaml` out of git", which turns off worktree seeding
(ADR 0096 sec. 2 gate 5) for every checkout the repository will ever have
-- field eval 0.23.1 finding 09. Its cases need a real repository,
because the answer is git's, so they build one.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from weld._doctor_gitignore import (
    check_gitignore,
    check_gitignore_resync,
    check_seeding_config,
)
from weld._gitignore_writer import (
    CONFIG_ONLY_GITIGNORE,
    IGNORE_ALL_GITIGNORE,
    TRACK_GRAPHS_GITIGNORE,
)

# Hermetic git: own identity, no ambient user config, stable locale.
# Mirrors _seed_fixture.GIT_ENV.
_GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}


@dataclass
class FakeResult:
    """Stand-in for :class:`weld.doctor.CheckResult` in unit tests."""

    level: str
    message: str
    section: str
    note_id: str | None = None


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        env=_GIT_ENV, check=True,
    )


def _repo(root: Path, gitignore: str, *, root_gitignore: str = "") -> Path:
    """A committed repo whose `.weld/` holds *gitignore* and a config."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    weld_dir = root / ".weld"
    weld_dir.mkdir()
    (weld_dir / "discover.yaml").write_text("sources: []\n", encoding="utf-8")
    (weld_dir / ".gitignore").write_text(gitignore, encoding="utf-8")
    if root_gitignore:
        (root / ".gitignore").write_text(root_gitignore, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return weld_dir


def _drop_lines(template: str, *lines_to_drop: str) -> str:
    """*template* with each of *lines_to_drop* removed -- simulates staleness.

    Same helper as `weld_gitignore_resync_test.py`'s, duplicated rather
    than imported: it is a five-line test fixture utility, not the
    recognition logic the two suites are pinned to share.
    """
    drop = set(lines_to_drop)
    return "".join(
        line for line in template.splitlines(keepends=True)
        if line.rstrip("\n") not in drop
    )


class DoctorGitignoreResyncWarnsTest(unittest.TestCase):
    """The one non-silent case: a recognized template missing lines."""

    def test_stale_config_only_file_warns_naming_missing_lines(self) -> None:
        stale = _drop_lines(
            CONFIG_ONLY_GITIGNORE, ".enrichment-prompted", "auto-refresh.jsonl",
        )
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            results = check_gitignore_resync(weld_dir, FakeResult)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].level, "warn")
            self.assertEqual(results[0].section, "Config")
            self.assertIn(".enrichment-prompted", results[0].message)
            self.assertIn("auto-refresh.jsonl", results[0].message)
            self.assertIn("wd init", results[0].message)

    def test_stale_track_graphs_file_warns(self) -> None:
        stale = _drop_lines(TRACK_GRAPHS_GITIGNORE, ".enrichment-prompted")
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            results = check_gitignore_resync(weld_dir, FakeResult)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].level, "warn")
            self.assertIn(".enrichment-prompted", results[0].message)

    def test_the_check_never_writes_to_the_file(self) -> None:
        """Read-only: bytes and mtime are untouched, unlike resync itself."""
        stale = _drop_lines(CONFIG_ONLY_GITIGNORE, ".enrichment-prompted")
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            target = weld_dir / ".gitignore"
            target.write_text(stale, encoding="utf-8")
            before_bytes = target.read_bytes()
            before_mtime = target.stat().st_mtime_ns
            results = check_gitignore_resync(weld_dir, FakeResult)
            self.assertEqual(len(results), 1)  # sanity: the case did warn
            self.assertEqual(target.read_bytes(), before_bytes)
            self.assertEqual(target.stat().st_mtime_ns, before_mtime)


class DoctorGitignoreResyncSilentTest(unittest.TestCase):
    """Every case ADR 0131's write path would also leave alone."""

    def test_absent_file_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            self.assertEqual(check_gitignore_resync(weld_dir, FakeResult), [])

    def test_missing_weld_dir_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / "does" / "not" / "exist" / ".weld"
            self.assertEqual(check_gitignore_resync(weld_dir, FakeResult), [])

    def test_already_current_file_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(
                CONFIG_ONLY_GITIGNORE, encoding="utf-8",
            )
            self.assertEqual(check_gitignore_resync(weld_dir, FakeResult), [])

    def test_foreign_content_is_silent(self) -> None:
        custom = "# my own rules\n*.tmp\nnode_modules/\n"
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(custom, encoding="utf-8")
            self.assertEqual(check_gitignore_resync(weld_dir, FakeResult), [])

    def test_recognized_lines_plus_one_custom_line_is_silent(self) -> None:
        custom = CONFIG_ONLY_GITIGNORE + "my-private-cache/\n"
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(custom, encoding="utf-8")
            self.assertEqual(check_gitignore_resync(weld_dir, FakeResult), [])

    def test_near_empty_file_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text("# do not touch\n", encoding="utf-8")
            self.assertEqual(check_gitignore_resync(weld_dir, FakeResult), [])

    def test_non_utf8_content_is_silent_and_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_bytes(b"\xff\xfe# not valid utf-8\n")
            self.assertEqual(check_gitignore_resync(weld_dir, FakeResult), [])


class DoctorSeedingConfigTest(unittest.TestCase):
    """`.weld/discover.yaml` out of git means no worktree can ever seed."""

    def test_ignore_all_repo_notes_the_disabled_seeding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = _repo(Path(tmp) / "repo", IGNORE_ALL_GITIGNORE)
            results = check_seeding_config(weld_dir, FakeResult)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "note")
        self.assertEqual(results[0].section, "Config")
        self.assertEqual(results[0].note_id, "worktree-seeding-config-ignored")
        self.assertIn(".weld/discover.yaml", results[0].message)
        self.assertIn("worktree", results[0].message)

    def test_repo_level_gitignore_hiding_weld_also_notes(self) -> None:
        """The policy can live further out than `.weld/.gitignore`."""
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = _repo(
                Path(tmp) / "repo", CONFIG_ONLY_GITIGNORE, root_gitignore=".weld/\n",
            )
            results = check_seeding_config(weld_dir, FakeResult)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "note")

    def test_default_config_only_repo_is_silent(self) -> None:
        """The shipped default tracks the config, so seeding works."""
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = _repo(Path(tmp) / "repo", CONFIG_ONLY_GITIGNORE)
            self.assertEqual(check_seeding_config(weld_dir, FakeResult), [])

    def test_force_added_config_in_an_ignore_all_repo_is_silent(self) -> None:
        """Tracked beats ignored: that repository's worktrees do get the file."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            weld_dir = _repo(root, IGNORE_ALL_GITIGNORE)
            _git(root, "add", "-f", ".weld/discover.yaml")
            _git(root, "commit", "-q", "-m", "track the config")

            self.assertEqual(check_seeding_config(weld_dir, FakeResult), [])

    def test_uncommitted_repo_is_silent(self) -> None:
        """Not yet committed is not the same as ignored -- and not a finding."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            _git(root, "init", "-q")
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text("sources: []\n", encoding="utf-8")
            (weld_dir / ".gitignore").write_text(
                CONFIG_ONLY_GITIGNORE, encoding="utf-8",
            )

            self.assertEqual(check_seeding_config(weld_dir, FakeResult), [])

    def test_outside_a_repository_is_silent(self) -> None:
        """git cannot answer, so there is nothing to report."""
        with tempfile.TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text("sources: []\n", encoding="utf-8")

            self.assertEqual(check_seeding_config(weld_dir, FakeResult), [])

    def test_absent_config_is_silent(self) -> None:
        """`_check_discover_yaml` already fails on that; one finding is enough."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            _git(root, "init", "-q")
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(
                IGNORE_ALL_GITIGNORE, encoding="utf-8",
            )

            self.assertEqual(check_seeding_config(weld_dir, FakeResult), [])


class DoctorGitignoreCompositeTest(unittest.TestCase):
    """The entry point `weld.doctor` calls runs both checks, not one."""

    def test_a_stale_ignore_all_file_yields_both_findings(self) -> None:
        stale = _drop_lines(CONFIG_ONLY_GITIGNORE, ".enrichment-prompted")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            weld_dir = _repo(root, CONFIG_ONLY_GITIGNORE, root_gitignore=".weld/\n")
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")

            results = check_gitignore(weld_dir, FakeResult)

        self.assertEqual([r.level for r in results], ["warn", "note"])


if __name__ == "__main__":
    unittest.main()
