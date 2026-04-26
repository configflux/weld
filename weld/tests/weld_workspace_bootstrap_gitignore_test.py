"""Integration tests: ``wd workspace bootstrap`` writes per-child .weld/.gitignore.

Companion to ``weld_workspace_bootstrap_test.py``; isolated to keep the
existing file under the 400-line cap. Covers:

* config-only default written in root + every child,
* idempotent skip-if-exists (a hand-customised .gitignore is left alone),
* ``--ignore-all`` flag writes the heavy-handed variant,
* ``--track-graphs`` flag widens the default to keep canonical graphs
  visible to git,
* per-child ``git status --porcelain`` excludes generated graphs by
  default but tracks them under ``--track-graphs``.
"""

from __future__ import annotations

import subprocess
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
)
from weld._workspace_bootstrap import bootstrap_workspace  # noqa: E402


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        check=True,
    )
    return proc.stdout


def _init_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")


class BootstrapGitignoreTest(unittest.TestCase):
    def test_config_only_gitignore_written_in_root_and_each_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")

            bootstrap_workspace(root)

            for weld_dir in (
                root / ".weld",
                root / "services" / "api" / ".weld",
                root / "services" / "auth" / ".weld",
            ):
                gitignore = weld_dir / ".gitignore"
                self.assertTrue(
                    gitignore.is_file(),
                    f"bootstrap must write {gitignore}",
                )
                self.assertEqual(
                    gitignore.read_text(encoding="utf-8"),
                    CONFIG_ONLY_GITIGNORE,
                )

    def test_track_graphs_writes_widened_gitignore_in_each_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _init_repo(root / "services" / "api")

            bootstrap_workspace(root, track_graphs=True)

            for weld_dir in (
                root / ".weld",
                root / "services" / "api" / ".weld",
            ):
                self.assertEqual(
                    (weld_dir / ".gitignore").read_text(encoding="utf-8"),
                    TRACK_GRAPHS_GITIGNORE,
                )

    def test_idempotent_does_not_overwrite_user_customised_gitignore(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            child = root / "services" / "api"
            _init_repo(child)
            # Pre-place a custom .gitignore in the child weld dir.
            (child / ".weld").mkdir(parents=True, exist_ok=True)
            custom = "# user-managed\n!*.json\n"
            (child / ".weld" / ".gitignore").write_text(custom, encoding="utf-8")

            bootstrap_workspace(root)

            self.assertEqual(
                (child / ".weld" / ".gitignore").read_text(encoding="utf-8"),
                custom,
                "bootstrap must not overwrite a hand-customised child .gitignore",
            )

    def test_ignore_all_writes_full_ignore_in_each_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _init_repo(root / "services" / "api")

            bootstrap_workspace(root, ignore_all=True)

            for weld_dir in (
                root / ".weld",
                root / "services" / "api" / ".weld",
            ):
                self.assertEqual(
                    (weld_dir / ".gitignore").read_text(encoding="utf-8"),
                    IGNORE_ALL_GITIGNORE,
                )

    def test_child_git_status_excludes_generated_graphs_by_default(self) -> None:
        """Default flip acceptance: graph.json is ignored after bootstrap.

        After bootstrap, ``git status --porcelain`` in any child must NOT
        list per-machine weld files (discovery-state.json, graph-previous.json,
        workspace-state.json, workspace.lock, query_state.bin) and must NOT
        list ``graph.json`` -- the new default ignores generated graphs.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            child = root / "services" / "api"
            _init_repo(child)

            bootstrap_workspace(root)

            # Fabricate a discovery-state.json + graph.json in the child to
            # confirm both are ignored. (Bootstrap may not have written one
            # depending on platform timing; this guarantees the test is
            # exercising the ignore rule.)
            (child / ".weld" / "discovery-state.json").write_text(
                "{}\n", encoding="utf-8",
            )
            (child / ".weld" / "graph.json").write_text(
                "{}\n", encoding="utf-8",
            )
            (child / ".weld" / "agent-graph.json").write_text(
                "{}\n", encoding="utf-8",
            )

            # Force git to enumerate every untracked file (default
            # porcelain collapses a fully-untracked dir to "?? .weld/").
            porcelain = _git(child, "status", "--porcelain", "--untracked-files=all")
            self.assertNotIn(
                ".weld/discovery-state.json", porcelain,
                f"discovery-state.json must be ignored in child:\n{porcelain}",
            )
            self.assertNotIn(
                ".weld/workspace.lock", porcelain,
                f"workspace.lock must be ignored in child:\n{porcelain}",
            )
            self.assertNotIn(
                ".weld/graph.json", porcelain,
                "config-only default: .weld/graph.json must be ignored. "
                f"porcelain:\n{porcelain}",
            )
            self.assertNotIn(
                ".weld/agent-graph.json", porcelain,
                "config-only default: .weld/agent-graph.json must be ignored. "
                f"porcelain:\n{porcelain}",
            )

    def test_track_graphs_keeps_graph_json_visible_after_bootstrap(self) -> None:
        """Track-graphs acceptance: graph.json surfaces under git after bootstrap."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            child = root / "services" / "api"
            _init_repo(child)

            bootstrap_workspace(root, track_graphs=True)

            # Fabricate the canonical graph + per-machine state to confirm
            # only the per-machine file is ignored.
            (child / ".weld" / "discovery-state.json").write_text(
                "{}\n", encoding="utf-8",
            )
            (child / ".weld" / "graph.json").write_text(
                "{}\n", encoding="utf-8",
            )
            (child / ".weld" / "agent-graph.json").write_text(
                "{}\n", encoding="utf-8",
            )

            porcelain = _git(child, "status", "--porcelain", "--untracked-files=all")
            self.assertNotIn(
                ".weld/discovery-state.json", porcelain,
                "track-graphs must still ignore per-machine state. "
                f"porcelain:\n{porcelain}",
            )
            self.assertIn(
                ".weld/graph.json", porcelain,
                "track-graphs: .weld/graph.json must remain visible "
                f"to git for tracking. porcelain:\n{porcelain}",
            )
            self.assertIn(
                ".weld/agent-graph.json", porcelain,
                "track-graphs: .weld/agent-graph.json must remain visible "
                f"to git for tracking. porcelain:\n{porcelain}",
            )


class InitGitignoreTest(unittest.TestCase):
    """Single-repo ``wd init`` writes the same config-only .weld/.gitignore."""

    def test_wd_init_writes_config_only_gitignore(self) -> None:
        from weld.init import main as init_main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            init_main([str(root)])
            gitignore = root / ".weld" / ".gitignore"
            self.assertTrue(gitignore.is_file())
            self.assertEqual(
                gitignore.read_text(encoding="utf-8"), CONFIG_ONLY_GITIGNORE,
            )

    def test_wd_init_ignore_all_flag_writes_full_ignore(self) -> None:
        from weld.init import main as init_main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            init_main([str(root), "--ignore-all"])
            self.assertEqual(
                (root / ".weld" / ".gitignore").read_text(encoding="utf-8"),
                IGNORE_ALL_GITIGNORE,
            )

    def test_wd_init_track_graphs_flag_writes_widened_gitignore(self) -> None:
        from weld.init import main as init_main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            init_main([str(root), "--track-graphs"])
            self.assertEqual(
                (root / ".weld" / ".gitignore").read_text(encoding="utf-8"),
                TRACK_GRAPHS_GITIGNORE,
            )

    def test_wd_init_track_graphs_and_ignore_all_are_mutually_exclusive(self) -> None:
        """argparse mutually-exclusive group rejects both flags together."""
        from weld.init import main as init_main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            with self.assertRaises(SystemExit) as ctx:
                init_main([str(root), "--track-graphs", "--ignore-all"])
            # argparse exits with code 2 on usage errors.
            self.assertEqual(ctx.exception.code, 2)

    def test_wd_init_re_run_does_not_overwrite_existing_gitignore(self) -> None:
        from weld.init import main as init_main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            init_main([str(root)])
            custom = "# user-edited\n*.bak\n"
            (root / ".weld" / ".gitignore").write_text(custom, encoding="utf-8")
            # Second `wd init` exits 1 because discover.yaml already exists
            # (correct CLI behavior); we still expect the gitignore to
            # survive untouched, so swallow the SystemExit and assert.
            with self.assertRaises(SystemExit):
                init_main([str(root)])
            self.assertEqual(
                (root / ".weld" / ".gitignore").read_text(encoding="utf-8"),
                custom,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
