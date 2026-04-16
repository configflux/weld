"""Tests for workspace ledger, lockfile, and ``wd workspace status``."""

from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld.cli import main as cli_main
from weld.contract import SCHEMA_VERSION
from weld.discover import discover, main as discover_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.workspace_state import (
    WORKSPACE_LOCK_FILENAME,
    WORKSPACE_STATE_FILENAME,
    WorkspaceChildState,
    WorkspaceLock,
    WorkspaceState,
    build_workspace_state,
    save_workspace_state,
)


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    _commit_file(repo_root, "README.md", "# fixture\n", "initial commit")
    return repo_root


def _commit_file(repo_root: Path, rel_path: str, content: str, message: str) -> None:
    target = repo_root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo_root, "add", rel_path)
    _git(repo_root, "commit", "-q", "-m", message)


def _write_graph(repo_root: Path, payload: dict | None = None) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph = payload or {
        "meta": {
            "version": SCHEMA_VERSION,
            "schema_version": 1,
        },
        "nodes": {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(graph, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> WorkspaceConfig:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
    return config


class WorkspaceStateBuildTest(unittest.TestCase):
    def test_build_workspace_state_tracks_all_child_statuses(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            present = _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")
            corrupt = _init_repo(root / "apps" / "frontend")
            _write_graph(present)
            _write_graph(corrupt)
            (corrupt / ".weld" / "graph.json").write_text("{bad json\n", encoding="utf-8")
            (present / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            config = _write_workspaces(
                root,
                [
                    ChildEntry(name="services-api", path="services/api"),
                    ChildEntry(name="services-auth", path="services/auth"),
                    ChildEntry(name="apps-frontend", path="apps/frontend"),
                    ChildEntry(name="libs-shared", path="libs/shared", remote="git@example.com:libs/shared.git"),
                ],
            )

            state = build_workspace_state(root, config, now="2026-04-15T19:30:00Z")

            self.assertEqual(state.version, 1)
            self.assertEqual(sorted(state.children), [
                "apps-frontend",
                "libs-shared",
                "services-api",
                "services-auth",
            ])

            present_state = state.children["services-api"]
            self.assertEqual(present_state.status, "present")
            self.assertTrue(present_state.is_dirty)
            self.assertEqual(present_state.graph_path, "services/api/.weld/graph.json")
            self.assertRegex(present_state.graph_sha256 or "", r"^[0-9a-f]{64}$")
            self.assertRegex(present_state.head_sha or "", r"^[0-9a-f]{40}$")
            self.assertTrue((present_state.head_ref or "").startswith("refs/heads/"))
            self.assertEqual(present_state.last_seen_utc, "2026-04-15T19:30:00Z")

            uninitialized_state = state.children["services-auth"]
            self.assertEqual(uninitialized_state.status, "uninitialized")
            self.assertIsNone(uninitialized_state.graph_sha256)
            self.assertFalse(uninitialized_state.is_dirty)

            corrupt_state = state.children["apps-frontend"]
            self.assertEqual(corrupt_state.status, "corrupt")
            self.assertRegex(corrupt_state.graph_sha256 or "", r"^[0-9a-f]{64}$")
            self.assertIn("JSONDecodeError", corrupt_state.error or "")

            missing_state = state.children["libs-shared"]
            self.assertEqual(missing_state.status, "missing")
            self.assertIsNone(missing_state.graph_sha256)
            self.assertIsNone(missing_state.head_sha)
            self.assertIsNone(missing_state.head_ref)
            self.assertFalse(missing_state.is_dirty)
            self.assertEqual(
                missing_state.remote,
                "git@example.com:libs/shared.git",
            )

    def test_detached_head_uses_null_head_ref(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "services" / "api")
            _commit_file(child, "second.txt", "two\n", "second commit")
            detached_sha = _git(child, "rev-parse", "HEAD~1")
            _git(child, "checkout", "-q", "HEAD~1")
            _write_graph(child)

            config = _write_workspaces(
                root,
                [ChildEntry(name="services-api", path="services/api")],
            )
            state = build_workspace_state(root, config, now="2026-04-15T19:31:00Z")

            child_state = state.children["services-api"]
            self.assertEqual(child_state.status, "present")
            self.assertIsNone(child_state.head_ref)
            self.assertEqual(child_state.head_sha, detached_sha)

    def test_save_workspace_state_uses_os_replace(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = WorkspaceState(
                children={
                    "services-api": WorkspaceChildState(
                        status="present",
                        head_sha="a" * 40,
                        head_ref="refs/heads/main",
                        is_dirty=False,
                        graph_path="services/api/.weld/graph.json",
                        graph_sha256="b" * 64,
                        last_seen_utc="2026-04-15T19:32:00Z",
                    )
                }
            )

            real_replace = os.replace
            with patch("weld.workspace_state.os.replace", side_effect=real_replace) as replace_mock:
                save_workspace_state(root, state)

            replace_mock.assert_called_once()
            tmp_path, final_path = replace_mock.call_args.args
            self.assertIn("workspace-state.json.tmp.", Path(tmp_path).name)
            self.assertEqual(
                Path(final_path),
                root / ".weld" / WORKSPACE_STATE_FILENAME,
            )

    def test_workspace_lock_acquire_publishes_payload_atomically(self) -> None:
        """``WorkspaceLock.acquire`` must close the TOCTOU window between
        lockfile creation and the PID payload write: a concurrent reader
        that opens the final lockfile path the instant it appears on disk
        must see a fully populated file, never an empty one. This also
        covers exclusion (a second acquire fails) and cleanup (no temp
        artifacts leak, permissions stay at 0o644)."""
        from weld.workspace_state import WorkspaceLockedError

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            lock_path = weld_dir / WORKSPACE_LOCK_FILENAME

            real_fdopen = os.fdopen
            snapshots: list[bytes | None] = []

            def fdopen_and_snapshot(fd, *args, **kwargs):
                # Sample the on-disk state of the final path at the moment
                # the payload stream opens -- that is when a racing reader
                # would otherwise observe an empty lockfile.
                try:
                    snapshots.append(lock_path.read_bytes())
                except FileNotFoundError:
                    snapshots.append(None)
                return real_fdopen(fd, *args, **kwargs)

            with patch(
                "weld.workspace_state.os.fdopen",
                side_effect=fdopen_and_snapshot,
            ):
                with WorkspaceLock(root):
                    self.assertTrue(lock_path.is_file())
                    payload = json.loads(lock_path.read_text(encoding="utf-8"))
                    self.assertEqual(payload["pid"], os.getpid())
                    self.assertIn("created_at", payload)
                    self.assertEqual(lock_path.stat().st_mode & 0o777, 0o644)

                    # A second acquire on the held lock must fail without
                    # leaking a staged temp file under .weld/.
                    with self.assertRaises(WorkspaceLockedError):
                        WorkspaceLock(root).acquire()
                    leftovers = [
                        entry.name
                        for entry in weld_dir.iterdir()
                        if entry.name.startswith(
                            f"{WORKSPACE_LOCK_FILENAME}.tmp."
                        )
                    ]
                    self.assertEqual(leftovers, [])

            # Every snapshot the "reader" took is either absent (final path
            # not yet published) or fully populated -- never an empty file.
            self.assertTrue(snapshots)
            for snap in snapshots:
                if snap is None:
                    continue
                self.assertGreater(len(snap), 0)
                observed = json.loads(snap.decode("utf-8"))
                self.assertEqual(observed["pid"], os.getpid())
                self.assertIn("created_at", observed)

            # Release removed the lockfile and no temp artifacts linger.
            self.assertFalse(lock_path.exists())
            leftovers = [
                entry.name
                for entry in weld_dir.iterdir()
                if entry.name.startswith(f"{WORKSPACE_LOCK_FILENAME}.tmp.")
            ]
            self.assertEqual(leftovers, [])


class WorkspaceDiscoverIntegrationTest(unittest.TestCase):
    def test_discover_writes_workspace_state_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "services" / "api")
            _write_graph(child)
            _write_workspaces(
                root,
                [ChildEntry(name="services-api", path="services/api")],
            )

            graph = discover(root, incremental=False)
            state_path = root / ".weld" / WORKSPACE_STATE_FILENAME

            self.assertEqual(graph["meta"]["version"], SCHEMA_VERSION)
            self.assertTrue(state_path.is_file())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["version"], 1)
            self.assertEqual(
                state["children"]["services-api"]["status"],
                "present",
            )
            self.assertIn("head_sha", state["children"]["services-api"])

    def test_discover_returns_nonzero_when_workspace_lock_is_held(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "services" / "api")
            _write_graph(child)
            _write_workspaces(
                root,
                [ChildEntry(name="services-api", path="services/api")],
            )
            save_workspace_state(
                root,
                WorkspaceState(
                    children={
                        "services-api": WorkspaceChildState(
                            status="present",
                            head_sha="a" * 40,
                            head_ref="refs/heads/main",
                            is_dirty=False,
                            graph_path="services/api/.weld/graph.json",
                            graph_sha256="b" * 64,
                            last_seen_utc="2026-04-15T19:33:00Z",
                        )
                    }
                ),
            )

            stderr = io.StringIO()
            with WorkspaceLock(root):
                self.assertTrue((root / ".weld" / WORKSPACE_LOCK_FILENAME).is_file())
                with patch("sys.stderr", stderr):
                    exit_code = discover_main([str(root)])

            self.assertEqual(exit_code, 2)
            self.assertIn("workspace.lock", stderr.getvalue())
            state = json.loads(
                (root / ".weld" / WORKSPACE_STATE_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(
                state["children"]["services-api"]["graph_sha256"],
                "b" * 64,
            )


class WorkspaceStatusCliTest(unittest.TestCase):
    def test_workspace_status_human_and_json_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            present = _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")
            _write_graph(present)
            _write_workspaces(
                root,
                [
                    ChildEntry(name="services-api", path="services/api"),
                    ChildEntry(name="services-auth", path="services/auth"),
                ],
            )
            discover(root, incremental=False)

            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                exit_code = cli_main(["workspace", "status", "--root", str(root)])

            self.assertEqual(exit_code, 0)
            human_output = stdout.getvalue()
            self.assertIn("Workspace status", human_output)
            self.assertIn("services-api: present", human_output)
            self.assertIn("services-auth: uninitialized", human_output)

            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                exit_code = cli_main(
                    ["workspace", "status", "--root", str(root), "--json"]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["version"], 1)
            self.assertEqual(payload["children"]["services-api"]["status"], "present")
            self.assertEqual(
                payload["children"]["services-auth"]["status"],
                "uninitialized",
            )

    def test_top_level_help_mentions_workspace(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = cli_main(["--help"])

        self.assertEqual(exit_code, 0)
        self.assertIn("workspace", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
