"""Tests for :mod:`weld._gitignore_writer`.

Pure-unit coverage of the helper: idempotency (skip-if-exists),
config-only default content, opt-in track-graphs content, opt-in
ignore-all content, mutual-exclusivity guard, and directory
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
    CONFIG_ONLY_GITIGNORE,
    IGNORE_ALL_GITIGNORE,
    TRACK_GRAPHS_GITIGNORE,
    write_weld_gitignore,
)


class WriteWeldGitignoreTest(unittest.TestCase):
    def test_writes_config_only_default(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            wrote = write_weld_gitignore(weld_dir)
            self.assertTrue(wrote)
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"),
                CONFIG_ONLY_GITIGNORE,
            )

    def test_config_only_ignores_generated_graphs(self) -> None:
        """Default flip: graph.json + agent-graph.json are NOT tracked.

        Tracking guarantee for config: discover.yaml / workspaces.yaml /
        agents.yaml must remain visible (not in the ignore list). The
        generated graphs must be ignored under the new default.
        """
        for must_track in (
            "discover.yaml",
            "workspaces.yaml",
            "agents.yaml",
        ):
            self.assertNotIn(
                f"\n{must_track}\n", "\n" + CONFIG_ONLY_GITIGNORE,
                f"config-only default unexpectedly ignores {must_track}",
            )
        for must_ignore in (
            "discovery-state.json",
            "graph-previous.json",
            "workspace-state.json",
            "workspace.lock",
            "query_state.bin",
            "graph.json",
            "agent-graph.json",
        ):
            self.assertIn(
                f"\n{must_ignore}\n", "\n" + CONFIG_ONLY_GITIGNORE,
                f"config-only default missing required ignore for {must_ignore}",
            )

    def test_track_graphs_keeps_graphs_visible(self) -> None:
        """Opt-in flip: graph.json + agent-graph.json are tracked again."""
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            wrote = write_weld_gitignore(weld_dir, track_graphs=True)
            self.assertTrue(wrote)
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"),
                TRACK_GRAPHS_GITIGNORE,
            )
        # Generated graphs are NOT in the ignore list under track-graphs.
        for must_track in ("graph.json", "agent-graph.json"):
            self.assertNotIn(
                f"\n{must_track}\n", "\n" + TRACK_GRAPHS_GITIGNORE,
                f"track-graphs unexpectedly ignores {must_track}",
            )
        # Per-machine state is still ignored under track-graphs.
        for must_ignore in (
            "discovery-state.json",
            "graph-previous.json",
            "workspace-state.json",
            "workspace.lock",
            "query_state.bin",
        ):
            self.assertIn(
                f"\n{must_ignore}\n", "\n" + TRACK_GRAPHS_GITIGNORE,
                f"track-graphs missing required ignore for {must_ignore}",
            )

    def test_track_graphs_and_ignore_all_are_mutually_exclusive(self) -> None:
        """Passing both flags is a programmer error: raise ValueError."""
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            with self.assertRaises(ValueError) as ctx:
                write_weld_gitignore(
                    weld_dir, ignore_all=True, track_graphs=True,
                )
            self.assertIn("mutually exclusive", str(ctx.exception))
            # No file should have been written.
            self.assertFalse((weld_dir / ".gitignore").exists())

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

    def test_idempotent_in_track_graphs_mode_too(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            custom = "# do not touch\n"
            (weld_dir / ".gitignore").write_text(custom, encoding="utf-8")
            wrote = write_weld_gitignore(weld_dir, track_graphs=True)
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
