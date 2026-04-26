"""Tests for :mod:`weld._gitignore_writer`.

Pure-unit coverage of the helper: idempotency (skip-if-exists),
selective default content, opt-in ignore-all content, and directory
auto-creation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._gitignore_writer import (  # noqa: E402
    IGNORE_ALL_GITIGNORE,
    SELECTIVE_GITIGNORE,
    write_weld_gitignore,
)


class WriteWeldGitignoreTest(unittest.TestCase):
    def test_writes_selective_default(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            wrote = write_weld_gitignore(weld_dir)
            self.assertTrue(wrote)
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"),
                SELECTIVE_GITIGNORE,
            )

    def test_selective_lists_only_volatile_files(self) -> None:
        """Tracking guarantee: discover.yaml / graph.json must NOT be ignored."""
        for must_track in (
            "discover.yaml",
            "workspaces.yaml",
            "agents.yaml",
            "graph.json",
            "agent-graph.json",
        ):
            self.assertNotIn(
                f"\n{must_track}\n", "\n" + SELECTIVE_GITIGNORE,
                f"selective default unexpectedly ignores {must_track}",
            )
        for must_ignore in (
            "discovery-state.json",
            "graph-previous.json",
            "workspace-state.json",
            "workspace.lock",
            "query_state.bin",
        ):
            self.assertIn(
                f"\n{must_ignore}\n", "\n" + SELECTIVE_GITIGNORE,
                f"selective default missing required ignore for {must_ignore}",
            )

    def test_writes_ignore_all(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            wrote = write_weld_gitignore(weld_dir, ignore_all=True)
            self.assertTrue(wrote)
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"),
                IGNORE_ALL_GITIGNORE,
            )
            # Sanity: ignore-all really blanket-ignores.
            self.assertIn("\n*\n", "\n" + IGNORE_ALL_GITIGNORE)
            self.assertIn("!.gitignore", IGNORE_ALL_GITIGNORE)

    def test_idempotent_skip_if_exists(self) -> None:
        """Pre-existing .gitignore must not be overwritten."""
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            custom = "# user-customised\n*.tmp\n"
            (weld_dir / ".gitignore").write_text(custom, encoding="utf-8")
            wrote = write_weld_gitignore(weld_dir)
            self.assertFalse(wrote)
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"), custom,
            )

    def test_idempotent_in_ignore_all_mode_too(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            custom = "# do not touch\n"
            (weld_dir / ".gitignore").write_text(custom, encoding="utf-8")
            wrote = write_weld_gitignore(weld_dir, ignore_all=True)
            self.assertFalse(wrote)
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"), custom,
            )

    def test_creates_weld_dir_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / "does" / "not" / "exist" / ".weld"
            self.assertFalse(weld_dir.exists())
            wrote = write_weld_gitignore(weld_dir)
            self.assertTrue(wrote)
            self.assertTrue(weld_dir.is_dir())
            self.assertTrue((weld_dir / ".gitignore").is_file())


if __name__ == "__main__":
    unittest.main()
