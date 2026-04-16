"""Root-write safety tests: atomic graph.json write, lockfile enforcement,
ledger consistency.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld.contract import SCHEMA_VERSION
from weld.discover import discover
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.workspace_state import (
    WORKSPACE_LOCK_FILENAME,
    WORKSPACE_STATE_FILENAME,
    WorkspaceLock,
    WorkspaceLockedError,
    atomic_write_text,
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
    readme = repo_root / "README.md"
    readme.write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _write_child_graph(repo_root: Path, payload: dict | None = None) -> None:
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


# ---------------------------------------------------------------------------
# atomic_write_text helper
# ---------------------------------------------------------------------------


class AtomicWriteTest(unittest.TestCase):
    def test_writes_content_to_final_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / ".weld" / "graph.json"
            atomic_write_text(final, "{\"payload\": 1}\n")
            self.assertEqual(
                final.read_text(encoding="utf-8"),
                "{\"payload\": 1}\n",
            )

    def test_creates_parent_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "deep" / "path" / "graph.json"
            atomic_write_text(final, "x\n")
            self.assertTrue(final.is_file())

    def test_uses_os_replace_with_temp_in_same_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "graph.json"

            real_replace = os.replace
            with patch("weld.workspace_state.os.replace", side_effect=real_replace) as replace_mock:
                atomic_write_text(final, "content\n")

            replace_mock.assert_called_once()
            tmp_path, final_path = replace_mock.call_args.args
            self.assertEqual(Path(final_path), final)
            self.assertEqual(Path(tmp_path).parent, final.parent)
            self.assertIn("graph.json.tmp.", Path(tmp_path).name)

    def test_temp_file_is_cleaned_on_error_during_write(self) -> None:
        """Failed rename must not leave partial final file or temp debris."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "graph.json"
            with patch(
                "weld.workspace_state.os.replace",
                side_effect=OSError("simulated mid-rename failure"),
            ), self.assertRaises(OSError):
                atomic_write_text(final, "content\n")

            self.assertFalse(final.exists())
            leftovers = [p.name for p in root.iterdir()]
            self.assertFalse(
                any("graph.json.tmp." in name for name in leftovers),
                f"leftover temp file(s): {leftovers}",
            )

    def test_previous_content_preserved_on_error(self) -> None:
        """QA crash-mid-write equivalent: existing final file is intact."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "graph.json"
            final.write_text("prev\n", encoding="utf-8")
            with patch(
                "weld.workspace_state.os.replace",
                side_effect=OSError("simulated"),
            ), self.assertRaises(OSError):
                atomic_write_text(final, "new\n")

            self.assertEqual(final.read_text(encoding="utf-8"), "prev\n")


# ---------------------------------------------------------------------------
# WorkspaceLock -- stale-PID cleanup
# ---------------------------------------------------------------------------


def _unused_pid() -> int:
    """A PID that is not currently running (best-effort probe)."""
    for candidate in (9_999_991, 9_999_983, 9_999_973, 9_999_881):
        try:
            os.kill(candidate, 0)
        except OSError:
            return candidate
    raise RuntimeError("could not find an unused pid for the test")


class StaleLockCleanupTest(unittest.TestCase):
    def test_stale_lock_with_dead_pid_is_cleaned_up(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            lock_path = root / ".weld" / WORKSPACE_LOCK_FILENAME
            lock_path.write_text(
                json.dumps({"pid": _unused_pid(), "created_at": "2026-04-15T00:00:00Z"}),
                encoding="utf-8",
            )

            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                lock = WorkspaceLock(root)
                acquired = lock.acquire()
            try:
                self.assertTrue(lock_path.is_file())
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["pid"], os.getpid())
                message = stderr.getvalue()
                self.assertIn("stale", message.lower())
                self.assertIn("workspace.lock", message)
            finally:
                acquired.release()

    def test_live_holder_lock_is_not_cleaned_up(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            lock_path = root / ".weld" / WORKSPACE_LOCK_FILENAME
            lock_path.write_text(
                json.dumps({"pid": os.getpid(), "created_at": "2026-04-15T00:00:00Z"}),
                encoding="utf-8",
            )
            with self.assertRaises(WorkspaceLockedError):
                WorkspaceLock(root).acquire()
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["pid"], os.getpid())

    def test_malformed_lock_is_treated_as_stale(self) -> None:
        """Unparseable lockfile content is treated as stale, not fatal."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            lock_path = root / ".weld" / WORKSPACE_LOCK_FILENAME
            lock_path.write_text("not-json{", encoding="utf-8")

            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                lock = WorkspaceLock(root).acquire()
            try:
                self.assertIn("stale", stderr.getvalue().lower())
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["pid"], os.getpid())
            finally:
                lock.release()


# ---------------------------------------------------------------------------
# Root discover: atomic graph.json write + ledger consistency
# ---------------------------------------------------------------------------


class RootDiscoverWriteSafetyTest(unittest.TestCase):
    def _build_federated_workspace(self, root: Path) -> None:
        _init_repo(root)
        child = _init_repo(root / "services" / "api")
        _write_child_graph(child)
        _write_workspaces(
            root,
            [ChildEntry(name="services-api", path="services/api")],
        )

    def test_discover_returns_graph_and_writes_ledger_atomically(self) -> None:
        """Ledger graph_sha256 matches child graph.json bytes; no temp debris."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_federated_workspace(root)

            graph = discover(root, incremental=False)
            self.assertEqual(graph["meta"]["version"], SCHEMA_VERSION)

            ledger_path = root / ".weld" / WORKSPACE_STATE_FILENAME
            self.assertTrue(ledger_path.is_file())

            # Ledger sha must match on-disk bytes for each present child.
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            child_entry = ledger["children"]["services-api"]
            self.assertEqual(child_entry["status"], "present")
            child_graph_bytes = (
                root / "services" / "api" / ".weld" / "graph.json"
            ).read_bytes()
            self.assertEqual(
                child_entry["graph_sha256"],
                hashlib.sha256(child_graph_bytes).hexdigest(),
            )

            # No debris in .weld/ from the write.
            leftovers = [
                p.name
                for p in (root / ".weld").iterdir()
                if ".tmp." in p.name
            ]
            self.assertEqual(leftovers, [], f"temp-file debris: {leftovers}")

    def test_discover_leaves_no_partial_graph_when_writer_fails(self) -> None:
        """QA crash-mid-write probe: previous .weld/graph.json is untouched."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_federated_workspace(root)

            root_graph_path = root / ".weld" / "graph.json"
            previous = "{\"prev\": true}\n"
            root_graph_path.write_text(previous, encoding="utf-8")

            # Make the final os.replace step fail once the temp is written.
            with patch("weld.workspace_state.os.replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    discover(root, incremental=False, write_root_graph=True)

            self.assertEqual(
                root_graph_path.read_text(encoding="utf-8"),
                previous,
                "previous root graph.json must survive a failed write",
            )
            # No orphaned temp file in .weld/.
            leftovers = [
                p.name
                for p in (root / ".weld").iterdir()
                if ".tmp." in p.name
            ]
            self.assertEqual(leftovers, [], f"temp-file debris: {leftovers}")

    def test_discover_writes_root_graph_when_requested(self) -> None:
        """write_root_graph=True atomically writes .weld/graph.json."""
        from weld.serializer import dumps_graph

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_federated_workspace(root)

            graph = discover(root, incremental=False, write_root_graph=True)
            root_graph_path = root / ".weld" / "graph.json"
            self.assertTrue(root_graph_path.is_file())
            self.assertEqual(
                root_graph_path.read_text(encoding="utf-8"),
                dumps_graph(graph),
            )

    def test_concurrent_root_write_blocks_second_caller(self) -> None:
        """Nested discover under a held lock raises WorkspaceLockedError."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_federated_workspace(root)
            outer = WorkspaceLock(root).acquire()
            try:
                with self.assertRaises(WorkspaceLockedError):
                    discover(root, incremental=False, write_root_graph=True)
            finally:
                outer.release()

            leftovers = [
                p.name
                for p in (root / ".weld").iterdir()
                if ".tmp." in p.name
            ]
            self.assertEqual(leftovers, [], f"temp-file debris: {leftovers}")


# ---------------------------------------------------------------------------
# Concurrent subprocess smoke test (Probe 3)
# ---------------------------------------------------------------------------


class ConcurrentDiscoverSubprocessTest(unittest.TestCase):
    """Subprocess smoke test for the CLI lockfile surface."""

    def test_second_discover_exits_nonzero_with_lock_message(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            child = _init_repo(root / "services" / "api")
            _write_child_graph(child)
            _write_workspaces(
                root,
                [ChildEntry(name="services-api", path="services/api")],
            )

            # Hold the lock, then invoke `wd discover` in a subprocess --
            # it must refuse cleanly. The holder's PID (this process)
            # must appear in the error message so operators can identify
            # the stuck run without reading the lockfile by hand.
            held = WorkspaceLock(root).acquire()
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "weld.discover",
                        str(root),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                held.release()

            self.assertNotEqual(proc.returncode, 0)
            combined = proc.stdout + proc.stderr
            self.assertIn("workspace.lock", combined)
            self.assertIn(f"pid {os.getpid()}", combined)


if __name__ == "__main__":
    unittest.main()
