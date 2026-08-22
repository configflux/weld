"""Tests for the operation-scoped repo-boundary snapshot (bd jbpb).

The git-visible file listing behind :func:`weld.repo_boundary.get_repo_boundary`
is a *point-in-time* observation of the working tree. Caching it for the life of
the process made every long-lived host (the MCP stdio server, weld embedded as a
library) blind to files created after its first walk of a root: ``wd discover``
reported "no files changed" with the new file on disk and committed.

These tests pin the replacement contract:

* inside a :func:`weld.repo_boundary.repo_boundary_scope` the snapshot is taken
  once per root and stays stable, so one discovery run cannot see the tree drift
  underneath it and pays exactly one ``git ls-files``;
* outside a scope the boundary is read fresh, so no operation can inherit a
  previous operation's view;
* nested scopes join the enclosing one rather than re-snapshotting;
* the self-bounding helpers (``iter_repo_files``, ``filter_repo_paths``) snapshot
  once per call even with no ambient scope.
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from weld import repo_boundary
from weld.repo_boundary import (
    filter_repo_paths,
    get_repo_boundary,
    iter_repo_files,
    path_within_repo_boundary,
    repo_boundary_scope,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")


def _commit_file(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"add {rel}")
    return path


class RepoBoundaryScopeTest(unittest.TestCase):
    """Snapshot lifetime: one per operation, never longer."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "repo"
        _init_repo(self.root)
        _commit_file(self.root, "src/app.py", "def app():\n    return True\n")

    def _visible(self, root: Path | None = None) -> frozenset[str]:
        boundary = get_repo_boundary(root or self.root)
        return boundary.visible_files or frozenset()

    def _count_loads(self):
        """Patch the snapshot loader with a counting pass-through."""
        return mock.patch.object(
            repo_boundary,
            "_load_repo_boundary",
            side_effect=repo_boundary._load_repo_boundary,
        )

    def test_new_file_is_visible_to_a_later_operation(self) -> None:
        """The regression: a second operation must not inherit the first's view."""
        self.assertIn("src/app.py", self._visible())

        _commit_file(self.root, "src/added.py", "def added():\n    return 1\n")

        self.assertIn(
            "src/added.py",
            self._visible(),
            "a file committed after the first boundary read must be visible to "
            "the next read -- caching the listing for the process lifetime is "
            "what made long-lived servers go blind",
        )

    def test_scope_holds_one_stable_snapshot(self) -> None:
        """Within one scope the tree cannot drift and git is shelled once."""
        with self._count_loads() as load:
            with repo_boundary_scope():
                first = self._visible()
                _commit_file(self.root, "src/mid.py", "def mid():\n    return 2\n")
                second = self._visible()

            self.assertEqual(
                first,
                second,
                "a scope is one point-in-time snapshot: a file appearing "
                "mid-operation must not change the answer under the caller",
            )
            self.assertNotIn("src/mid.py", second)
            self.assertEqual(
                load.call_count,
                1,
                "one scope must cost exactly one 'git ls-files' per root",
            )

    def test_snapshot_is_dropped_when_the_scope_exits(self) -> None:
        with repo_boundary_scope():
            self.assertNotIn("src/late.py", self._visible())
            _commit_file(self.root, "src/late.py", "def late():\n    return 3\n")

        with repo_boundary_scope():
            self.assertIn(
                "src/late.py",
                self._visible(),
                "the next scope must re-read the tree, not resume the old one",
            )

    def test_nested_scopes_join_the_outer_snapshot(self) -> None:
        """Re-entrancy: an inner helper must not re-snapshot mid-operation."""
        with self._count_loads() as load:
            with repo_boundary_scope():
                outer = self._visible()
                _commit_file(self.root, "src/nested.py", "def nested():\n    return 4\n")
                with repo_boundary_scope():
                    inner = self._visible()
                    # filter_repo_paths self-bounds: the real nesting case.
                    filter_repo_paths(self.root, [self.root / "src" / "app.py"])

            self.assertEqual(outer, inner)
            self.assertEqual(
                load.call_count,
                1,
                "nested scopes must join the enclosing snapshot, not replace it",
            )

    def test_unscoped_reads_are_fresh(self) -> None:
        """No ambient scope means no cache: correctness beats a saved subprocess."""
        with self._count_loads() as load:
            self._visible()
            self._visible()

        self.assertEqual(
            load.call_count,
            2,
            "outside a scope every read must observe the tree as it is now",
        )

    def test_distinct_roots_get_distinct_snapshots(self) -> None:
        other = Path(self._tmp.name).resolve() / "other"
        _init_repo(other)
        _commit_file(other, "lib/other.py", "def other():\n    return 5\n")

        with repo_boundary_scope():
            self.assertIn("src/app.py", self._visible())
            self.assertIn("lib/other.py", self._visible(other))
            self.assertNotIn("src/app.py", self._visible(other))

    def test_scope_does_not_leak_across_threads(self) -> None:
        """A worker thread must not inherit (or corrupt) another's snapshot."""
        seen: list[frozenset[str]] = []

        with repo_boundary_scope():
            self._visible()
            _commit_file(self.root, "src/thread.py", "def thr():\n    return 6\n")

            worker = threading.Thread(target=lambda: seen.append(self._visible()))
            worker.start()
            worker.join()

            self.assertNotIn("src/thread.py", self._visible())

        self.assertEqual(len(seen), 1)
        self.assertIn(
            "src/thread.py",
            seen[0],
            "scope state is per-thread: an unscoped thread reads the live tree",
        )


class BoundedHelperTest(unittest.TestCase):
    """The list-answering helpers cost one snapshot per call, never per path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "repo"
        _init_repo(self.root)
        for name in ("a", "b", "c"):
            _commit_file(self.root, f"src/{name}.py", f"def {name}():\n    return 0\n")

    def _count_loads(self):
        return mock.patch.object(
            repo_boundary,
            "_load_repo_boundary",
            side_effect=repo_boundary._load_repo_boundary,
        )

    def test_filter_repo_paths_snapshots_once_per_call(self) -> None:
        paths = sorted(self.root.glob("src/*.py"))
        self.assertEqual(len(paths), 3)

        with self._count_loads() as load:
            kept = filter_repo_paths(self.root, paths)

        self.assertEqual(len(kept), 3)
        self.assertEqual(
            load.call_count,
            1,
            "filter_repo_paths must not shell git once per candidate path",
        )

    def test_iter_repo_files_snapshots_once_per_call(self) -> None:
        with self._count_loads() as load:
            files = iter_repo_files(self.root)

        self.assertIn(self.root / "src" / "a.py", files)
        self.assertEqual(load.call_count, 1)

    def test_helpers_see_files_added_between_calls(self) -> None:
        """Bounding one call must never become bounding the process."""
        self.assertNotIn(self.root / "src" / "d.py", iter_repo_files(self.root))
        _commit_file(self.root, "src/d.py", "def d():\n    return 0\n")
        self.assertIn(
            self.root / "src" / "d.py",
            iter_repo_files(self.root),
            "self-bounding must scope one call, not the process",
        )

    def test_path_within_repo_boundary_tracks_new_files(self) -> None:
        added = self.root / "src" / "e.py"
        self.assertFalse(path_within_repo_boundary(self.root, added))
        _commit_file(self.root, "src/e.py", "def e():\n    return 0\n")
        self.assertTrue(path_within_repo_boundary(self.root, added))


class LongLivedProcessDiscoverTest(unittest.TestCase):
    """The reported bug, end to end: two discovers in one process.

    ``_discover_single_repo`` is the warm refresh entry the MCP stdio server
    drives per read (ADR 0074), so discovering twice in one process is exactly
    what a long-lived host does. The second pass must see a file that appeared
    after the first.
    """

    # Two globs on purpose: each resolves through its own self-bounding
    # ``filter_repo_paths``, so "one snapshot per run" only holds if the run
    # itself opened the enclosing scope.
    DISCOVER_YAML = (
        "sources:\n"
        '  - glob: "*.py"\n'
        "    type: symbol\n"
        "    strategy: python_module\n"
        '  - glob: "src/*.py"\n'
        "    type: symbol\n"
        "    strategy: python_module\n"
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "repo"
        _init_repo(self.root)
        weld_dir = self.root / ".weld"
        weld_dir.mkdir(parents=True, exist_ok=True)
        (weld_dir / "discover.yaml").write_text(self.DISCOVER_YAML, encoding="utf-8")
        (weld_dir / ".gitignore").write_text("*\n", encoding="utf-8")
        _commit_file(self.root, "alpha.py", "def alpha():\n    return 1\n")

    def _discover(self) -> set[str]:
        from weld.discover import _discover_single_repo

        graph = _discover_single_repo(self.root, write_graph=True)
        return set(graph.get("nodes") or {})

    def test_second_discover_sees_a_file_added_after_the_first(self) -> None:
        first = self._discover()
        self.assertIn("file:alpha", first)
        self.assertNotIn("file:beta", first)

        _commit_file(self.root, "beta.py", "def beta():\n    return 2\n")

        self.assertIn(
            "file:beta",
            self._discover(),
            "a long-lived process must not report 'no files changed' for a file "
            "that is on disk and committed (bd jbpb)",
        )

    def test_a_discovery_run_snapshots_the_boundary_once(self) -> None:
        """Correctness must not cost the run extra ``git ls-files`` calls."""
        with mock.patch.object(
            repo_boundary,
            "_load_repo_boundary",
            side_effect=repo_boundary._load_repo_boundary,
        ) as load:
            self._discover()

        self.assertEqual(
            load.call_count,
            1,
            "the discover-level scope must cover glob resolution, strategy "
            "runs and finalization -- one snapshot for the whole run",
        )


if __name__ == "__main__":
    unittest.main()
