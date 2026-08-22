"""Tests for :mod:`weld._gitignore_writer`.

Pure-unit coverage of the helper: idempotency (skip-if-exists),
config-only default content, opt-in track-graphs content, opt-in
ignore-all content, mutual-exclusivity guard, and directory
auto-creation.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


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
            "graph-meta.json",  # ADR 0065 volatile-meta sidecar
            "agent-graph.json",
            "graph-communities.json",
            "graph-community-report.md",
            "graph-community-index.md",
            "auto-refresh.jsonl",  # ADR 0051 refresh sidecar log
            # ADR 0110: the file index is rebuilt from the tree by
            # discovery, so "ignore everything weld can rebuild" covers it
            # too. It used to be tracked here purely by omission -- absent
            # from both constants -- which contradicted this mode's own
            # stated principle in every downstream repo (bd hvht).
            "file-index.json",
            "file-index-state.json",
            # ADR 0052 first-run-enrichment sentinel (bd lt96): per-machine
            # "was the user already prompted" flag, never rebuildable and
            # never shareable -- must be ignored like every other sidecar.
            ".enrichment-prompted",
        ):
            self.assertIn(
                f"\n{must_ignore}\n", "\n" + CONFIG_ONLY_GITIGNORE,
                f"config-only default missing required ignore for {must_ignore}",
            )

    def test_config_only_ignores_every_artifact_track_graphs_adds(self) -> None:
        """The two modes differ by exactly the warm-checkout artifact set.

        Stated as a relation rather than two lists, so an artifact added
        to Mode B can never be left tracked in Mode A by omission -- the
        failure mode bd hvht found.
        """
        tracked_in_b = {
            "graph.json",
            "agent-graph.json",
            "discovery-state.json",
            "file-index.json",
            "file-index-state.json",
        }
        ignored_in_a = set(CONFIG_ONLY_GITIGNORE.split())
        self.assertEqual(tracked_in_b - ignored_in_a, set())

    def test_persisted_user_intent_files_stay_tracked_by_design(self) -> None:
        """viz-views.json / telemetry.disabled are user intent, not output.

        bd lt96: both hold a decision the user made (saved viz-server views;
        an explicit telemetry opt-out), not something weld can regenerate
        from source. Neither gitignore template may ignore them -- that is
        a deliberate, already-made decision (see the module docstring), not
        an oversight. This test is the pin so a future sweep does not
        re-litigate it by adding either line to a template.
        """
        for template in (CONFIG_ONLY_GITIGNORE, TRACK_GRAPHS_GITIGNORE):
            names = set(template.split())
            self.assertNotIn("viz-views.json", names)
            self.assertNotIn("telemetry.disabled", names)

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
        # Generated graphs are NOT in the ignore list under track-graphs --
        # and neither is the record that explains each of them (ADR 0110).
        # A graph shipped without its inventory is the ADR 0101 hole: the
        # clone holds a complete graph and no account of what it read.
        for must_track in (
            "graph.json",
            "agent-graph.json",
            "discovery-state.json",
            "file-index.json",
            "file-index-state.json",
        ):
            self.assertNotIn(
                f"\n{must_track}\n", "\n" + TRACK_GRAPHS_GITIGNORE,
                f"track-graphs unexpectedly ignores {must_track}",
            )
        # Per-machine state is still ignored under track-graphs. Crucially
        # the volatile-meta sidecar (ADR 0065) stays ignored even when the
        # graph itself is tracked -- that split is the whole point.
        for must_ignore in (
            "graph-previous.json",
            "workspace-state.json",
            "workspace.lock",
            "query_state.bin",
            "graph-meta.json",  # ADR 0065 volatile-meta sidecar
            "graph-communities.json",
            "graph-community-report.md",
            "graph-community-index.md",
            "auto-refresh.jsonl",  # ADR 0051 refresh sidecar log
            # ADR 0052 first-run-enrichment sentinel (bd lt96): per-machine
            # state, stays ignored even in track-graphs mode -- Mode B
            # tracks graph artifacts and their claims, not arbitrary
            # per-machine flags.
            ".enrichment-prompted",
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
