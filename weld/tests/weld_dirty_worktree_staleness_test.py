"""Tests for working-tree-aware staleness (ADR 0017 refinement).

``compute_stale_info`` historically diffed only ``graph_sha..HEAD``
commits, so an uncommitted edit to a tracked source file left the graph
reported as fresh -- an agent mid-edit queried a graph that ignored its
own changes. These tests pin the new behaviour:

- ``working_tree_dirty_sources`` returns the dirty tracked paths under
  the ``discovered_from`` prefixes (staged, unstaged, untracked).
- weld bookkeeping dirt (``.weld/graph.json`` and siblings) never counts.
- an empty ``discovered_from`` short-circuits before any git call.
- ``compute_stale_info`` flips ``source_stale`` (and the ``stale`` alias)
  to True for an uncommitted tracked edit even when HEAD has not moved.
- ``auto_refresh_if_stale`` fires on a dirty tracked tree.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


from weld._git import (  # noqa: E402
    get_git_sha,
    working_tree_dirty_sources,
)
from weld._staleness import compute_stale_info  # noqa: E402


def _run(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=20,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _git_init(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--quiet"], root)
    _run(["git", "config", "user.email", "test@test.com"], root)
    _run(["git", "config", "user.name", "Test"], root)
    _run(["git", "config", "commit.gpgsign", "false"], root)


def _commit_all(root: Path, msg: str) -> None:
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-m", msg, "--quiet"], root)


class WorkingTreeDirtySourcesTest(unittest.TestCase):
    """Direct coverage for ``working_tree_dirty_sources``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git_init(self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "README.md").write_text("hi\n", encoding="utf-8")
        _commit_all(self.root, "initial")

    def test_clean_tree_returns_empty(self) -> None:
        self.assertEqual(
            working_tree_dirty_sources(self.root, ["src/"]), []
        )

    def test_unstaged_edit_to_tracked_source_is_dirty(self) -> None:
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        dirty = working_tree_dirty_sources(self.root, ["src/"])
        self.assertIn("src/a.py", dirty)

    def test_staged_edit_to_tracked_source_is_dirty(self) -> None:
        (self.root / "src" / "a.py").write_text("x = 3\n", encoding="utf-8")
        _run(["git", "add", "src/a.py"], self.root)
        dirty = working_tree_dirty_sources(self.root, ["src/"])
        self.assertIn("src/a.py", dirty)

    def test_untracked_new_file_under_prefix_is_dirty(self) -> None:
        (self.root / "src" / "b.py").write_text("y = 1\n", encoding="utf-8")
        dirty = working_tree_dirty_sources(self.root, ["src/"])
        self.assertIn("src/b.py", dirty)

    def test_edit_outside_prefix_is_not_dirty(self) -> None:
        (self.root / "README.md").write_text("hi again\n", encoding="utf-8")
        self.assertEqual(
            working_tree_dirty_sources(self.root, ["src/"]), []
        )

    def test_bookkeeping_dirt_is_never_source(self) -> None:
        # A broad discovered_from ("./") that would otherwise match
        # .weld/graph.json must still exclude weld bookkeeping.
        (self.root / ".weld" / "graph.json").write_text(
            "{}\n", encoding="utf-8"
        )
        self.assertEqual(
            working_tree_dirty_sources(self.root, ["./"]), []
        )

    def test_bookkeeping_sidecars_are_never_source(self) -> None:
        # ``file-index-state.json`` is the bd 85tb.2 surface-hash companion:
        # every refresh-on-read rewrites it untracked, so if it were missing
        # from the bookkeeping set a broad ``./`` discovered_from would count
        # it as source drift and leave the repo perpetually ``source_stale``.
        # ``auto-refresh.jsonl`` (ADR 0051 sidecar log) has the identical
        # shape: every auto-refresh appends to it. The rest arrived in the
        # bd eqc4 sweep that enumerated the whole family at once instead of
        # waiting for each to strand a checkout in turn -- ``telemetry.jsonl``
        # (ADR 0035, every command) and ``.enrichment-prompted`` (ADR 0052,
        # first run) being the two that bite with no user action at all.
        for name in (
            "graph.json", "discovery-state.json", "graph-meta.json",
            "graph.db", "file-index.json", "file-index-state.json",
            "auto-refresh.jsonl", "telemetry.jsonl", "graph-previous.json",
            "workspace-state.json", "workspace.lock", "agent-graph.json",
            "graph-communities.json", "graph-community-report.md",
            "graph-community-index.md", "review-state.json",
            ".enrichment-prompted",
        ):
            (self.root / ".weld" / name).write_text("x\n", encoding="utf-8")
        self.assertEqual(
            working_tree_dirty_sources(self.root, ["./"]), []
        )

    def test_auto_refresh_writer_output_is_never_source(self) -> None:
        # Bound to the *real* writer instead of a hard-coded filename: this
        # fails both if the sidecar path drops out of the bookkeeping set and
        # if ``_record_sidecar_event`` ever renames the log out from under it.
        from weld._auto_refresh import _record_sidecar_event
        _record_sidecar_event(
            self.root, files_changed=1, elapsed_ms=7, incremental=True,
        )
        written = sorted(p.name for p in (self.root / ".weld").iterdir())
        self.assertTrue(
            written, "auto-refresh writer produced no sidecar to test against"
        )
        self.assertEqual(
            working_tree_dirty_sources(self.root, ["./"]), [],
            f"auto-refresh sidecar counted as source drift: {written}",
        )

    def test_graph_write_lock_output_is_never_source(self) -> None:
        # ADR 0096 gate 5 takes the ADR 0094 write lock to seed a fresh
        # worktree, so a plain read now leaves a lock file where only
        # mutating verbs used to. Bound to the *real* lock rather than a
        # hard-coded filename, like the auto-refresh case above: without
        # the bookkeeping entry, one read makes a checkout whose
        # ``.weld/.gitignore`` predates ADR 0094 permanently source-stale,
        # re-discovering on every read thereafter.
        from weld._graph_write_lock import graph_write_lock

        with graph_write_lock(self.root):
            pass
        written = sorted(p.name for p in (self.root / ".weld").iterdir())
        self.assertTrue(
            written, "the write lock produced no file to test against"
        )
        self.assertEqual(
            working_tree_dirty_sources(self.root, ["./"]), [],
            f"graph write lock counted as source drift: {written}",
        )

    def test_telemetry_writer_output_is_never_source(self) -> None:
        # bd eqc4, the third writer-bound case. ADR 0035 telemetry is
        # default-on and appends on *every* ``wd`` command, so a checkout
        # whose ``.weld/.gitignore`` predates the ``telemetry.jsonl``
        # template line reads its own log as user source drift and answers
        # ``source_stale`` forever after. Bound to the real ``Recorder``, so
        # a rename of the log fails here too. ``graph.json`` (itself
        # bookkeeping) makes ``resolve_path`` see a weld root;
        # ``cli_flag=True`` pins telemetry on whatever the ambient opt-out.
        import io

        from weld._telemetry import Recorder

        (self.root / ".weld" / "graph.json").write_text("{}\n", encoding="utf-8")
        with Recorder(
            surface="cli", command="query", flags=[], root=self.root,
            cli_flag=True, stderr=io.StringIO(),
        ):
            pass
        written = sorted(p.name for p in (self.root / ".weld").iterdir())
        self.assertIn(
            "telemetry.jsonl", written,
            f"telemetry writer produced no log to test against: {written}",
        )
        self.assertEqual(
            working_tree_dirty_sources(self.root, ["./"]), [],
            f"telemetry log counted as source drift: {written}",
        )

    def test_dirty_source_under_root_prefix_still_counts(self) -> None:
        # "./" tracks everything; a real source edit must still surface
        # even though bookkeeping under .weld/ is filtered.
        (self.root / "src" / "a.py").write_text("x = 9\n", encoding="utf-8")
        (self.root / ".weld" / "graph.json").write_text(
            "{}\n", encoding="utf-8"
        )
        dirty = working_tree_dirty_sources(self.root, ["./"])
        self.assertIn("src/a.py", dirty)
        self.assertNotIn(".weld/graph.json", dirty)

    def test_empty_discovered_from_short_circuits(self) -> None:
        # Even with a dirty tree, an empty tracked list yields nothing
        # (and must not require a git call to do so).
        (self.root / "src" / "a.py").write_text("x = 5\n", encoding="utf-8")
        self.assertEqual(working_tree_dirty_sources(self.root, []), [])

    def test_non_git_root_returns_empty(self) -> None:
        non_git = Path(tempfile.mkdtemp())
        try:
            (non_git / "src").mkdir()
            (non_git / "src" / "a.py").write_text("z\n", encoding="utf-8")
            self.assertEqual(
                working_tree_dirty_sources(non_git, ["src/"]), []
            )
        finally:
            import shutil
            shutil.rmtree(non_git, ignore_errors=True)


class ComputeStaleInfoDirtyTreeTest(unittest.TestCase):
    """``compute_stale_info`` must treat uncommitted tracked edits as stale."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git_init(self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "README.md").write_text("hi\n", encoding="utf-8")
        _commit_all(self.root, "initial")
        self._sha0 = get_git_sha(self.root)
        assert self._sha0 is not None

    def _info(self, *, discovered_from: list[str]) -> dict:
        graph_path = self.root / ".weld" / "graph.json"
        meta = {"git_sha": self._sha0, "discovered_from": discovered_from}
        return compute_stale_info(graph_path, meta)

    def test_clean_tree_is_not_stale(self) -> None:
        r = self._info(discovered_from=["src/"])
        self.assertFalse(r["stale"])
        self.assertFalse(r["source_stale"])
        self.assertFalse(r["sha_behind"])

    def test_uncommitted_tracked_edit_is_stale(self) -> None:
        # The core bug: HEAD has NOT moved (sha_behind=False) but a
        # tracked source file has uncommitted edits -> must be stale.
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        r = self._info(discovered_from=["src/"])
        self.assertTrue(r["source_stale"], r)
        self.assertTrue(r["stale"], r)
        # No commit happened, so HEAD is unchanged.
        self.assertFalse(r["sha_behind"], r)
        # No discovery-state.json here (see module docstring) -- the
        # undecidable fallback under-reports detail rather than inventing it.
        self.assertEqual(r["stale_sources"], [])

    def test_uncommitted_untracked_prefix_edit_is_not_stale(self) -> None:
        # README is outside discovered_from; an uncommitted edit must
        # NOT mark the graph stale.
        (self.root / "README.md").write_text("hi again\n", encoding="utf-8")
        r = self._info(discovered_from=["src/"])
        self.assertFalse(r["source_stale"], r)
        self.assertFalse(r["stale"], r)

    def test_bookkeeping_only_dirt_is_not_stale(self) -> None:
        # Dirty .weld/graph.json with a broad "./" discovered_from must
        # not flip stale -- bookkeeping is never source.
        (self.root / ".weld" / "graph.json").write_text(
            "{}\n", encoding="utf-8"
        )
        r = self._info(discovered_from=["./"])
        self.assertFalse(r["source_stale"], r)
        self.assertFalse(r["stale"], r)

    def test_auto_refresh_sidecar_does_not_flip_stale(self) -> None:
        # The reported failure: one auto-refresh writes its sidecar log, and
        # from then on a broad ``discovered_from`` (``['./']`` -- the default
        # ``wd init`` shape) reads weld's own log as user source drift, so
        # freshness answers ``source_stale`` forever. HEAD never moved, and
        # no user file changed, so nothing here is stale.
        from weld._auto_refresh import _record_sidecar_event
        _record_sidecar_event(
            self.root, files_changed=2, elapsed_ms=11, incremental=False,
        )
        # The writer is failure-isolated (ADR 0035), so assert it actually
        # produced a log -- otherwise this test would pass vacuously.
        self.assertTrue(
            sorted((self.root / ".weld").iterdir()),
            "auto-refresh writer produced no sidecar to test against",
        )
        r = self._info(discovered_from=["./"])
        self.assertFalse(r["source_stale"], r)
        self.assertFalse(r["stale"], r)

    def test_committed_drift_and_dirty_both_detected(self) -> None:
        # Commit one tracked change (sha_behind path) then add an
        # uncommitted edit on top -- still stale, no double-trip needed.
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        _commit_all(self.root, "committed change")
        (self.root / "src" / "a.py").write_text("x = 3\n", encoding="utf-8")
        r = self._info(discovered_from=["src/"])
        self.assertTrue(r["source_stale"], r)
        self.assertTrue(r["stale"], r)

    def test_clean_tree_stale_check_is_fast(self) -> None:
        # Acceptance #3: the stale check on a clean tree stays well under
        # 500ms. Two git subprocesses (rev-parse + status) dominate; this
        # is a generous ceiling that still catches an accidental
        # per-file-hash regression.
        start = time.monotonic()
        self._info(discovered_from=["src/"])
        elapsed_ms = (time.monotonic() - start) * 1000
        self.assertLess(
            elapsed_ms, 500, f"clean stale check took {elapsed_ms:.0f}ms"
        )


class AutoRefreshFiresOnDirtyTreeTest(unittest.TestCase):
    """A dirty tracked tree must drive ``auto_refresh_if_stale`` to refresh."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git_init(self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        _commit_all(self.root, "initial")
        self._sha0 = get_git_sha(self.root)
        # A discover.yaml is required for auto-refresh to engage.
        (self.root / ".weld" / "discover.yaml").write_text(
            "sources: []\n", encoding="utf-8"
        )

    def _write_graph(self) -> None:
        import json
        from weld.contract import SCHEMA_VERSION
        payload = {
            "meta": {
                "version": SCHEMA_VERSION,
                "updated_at": "2026-04-20T12:00:00+00:00",
                "git_sha": self._sha0,
                "discovered_from": ["src/"],
            },
            "nodes": {},
            "edges": [],
        }
        (self.root / ".weld" / "graph.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def test_dirty_tree_triggers_auto_refresh(self) -> None:
        import io
        from weld._auto_refresh import auto_refresh_if_stale
        self._write_graph()
        # Uncommitted edit to a tracked source file.
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        # Pass an explicit env (WELD_AUTO_REFRESH unset) so the test is
        # hermetic against the host's environment.
        env = {k: v for k, v in os.environ.items()
               if k != "WELD_AUTO_REFRESH"}
        result = auto_refresh_if_stale(
            self.root, env=env, stderr=io.StringIO(),
        )
        # A refresh dict (truthy) means the stale signal fired and the
        # refresh ran; None would mean we judged the graph fresh.
        self.assertIsNotNone(
            result, "dirty tracked tree should have triggered auto-refresh"
        )

    def test_clean_tree_does_not_trigger_auto_refresh(self) -> None:
        import io
        from weld._auto_refresh import auto_refresh_if_stale
        self._write_graph()
        env = {k: v for k, v in os.environ.items()
               if k != "WELD_AUTO_REFRESH"}
        result = auto_refresh_if_stale(
            self.root, env=env, stderr=io.StringIO(),
        )
        self.assertIsNone(
            result, "clean tree at recorded HEAD must not refresh"
        )


if __name__ == "__main__":
    unittest.main()
