"""Tests for the source-file staleness model (ADR 0017).

Covers `Graph.stale()` semantics:
- primary signal `source_stale` is driven by file content diffs between
  `meta.git_sha` and HEAD, intersected with `meta.discovered_from`.
- secondary signal `sha_behind` reflects pointer drift.
- enrichment-only commits (add-node --merge) stamp git_sha implicitly
  via `Graph.save(touch_git_sha=True)` so stale stays False.
- non-git repos keep the legacy answer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._git import drift_is_graph_only, get_git_sha  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402


def _run(cmd: list[str], cwd: Path) -> str:
    """Run a shell command and return stdout (strip)."""
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


def _write_graph(
    root: Path, *, git_sha: str | None, discovered_from: list[str],
    nodes: dict | None = None, edges: list | None = None,
) -> None:
    """Write a .weld/graph.json with a known meta."""
    meta: dict = {
        "version": SCHEMA_VERSION,
        "updated_at": "2026-04-20T12:00:00+00:00",
        "discovered_from": discovered_from,
    }
    if git_sha is not None:
        meta["git_sha"] = git_sha
    payload = {
        "meta": meta,
        "nodes": nodes or {},
        "edges": edges or [],
    }
    (root / ".weld" / "graph.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )


class GraphStaleSourceModelTest(unittest.TestCase):
    """Primary signal is file-diff based, not SHA-based."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _git_init(self.root)
        # Seed a single tracked source file under a discovered-from dir.
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "README.md").write_text("hi\n", encoding="utf-8")
        _commit_all(self.root, "initial")
        self._sha0 = get_git_sha(self.root)
        assert self._sha0 is not None

    def tearDown(self) -> None:
        # Graceful cleanup: subprocess.run might leave files around.
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _stale(self) -> dict:
        g = Graph(self.root)
        g.load()
        return g.stale()

    def test_fresh_graph_reports_not_stale(self) -> None:
        _write_graph(self.root, git_sha=self._sha0, discovered_from=["src/"])
        r = self._stale()
        self.assertFalse(r["stale"])
        self.assertFalse(r["source_stale"])
        self.assertFalse(r["sha_behind"])
        self.assertEqual(r["graph_sha"], self._sha0)
        self.assertEqual(r["current_sha"], self._sha0)
        self.assertEqual(r["commits_behind"], 0)

    def test_enrichment_only_commit_keeps_stale_false(self) -> None:
        """add-node --merge stamps git_sha (touch_git_sha=True), so after
        the agent commits the enrichment, `stale()` reports False for
        both source_stale and sha_behind."""
        _write_graph(self.root, git_sha=self._sha0, discovered_from=["src/"])
        # Simulate the mutating CLI path: load, mutate, save with touch.
        g = Graph(self.root)
        g.load()
        g.add_node("entity:Alpha", "entity", "Alpha", {"description": "x"})
        g.save(touch_git_sha=True)
        # Commit the enrichment -- no source file changed.
        _commit_all(self.root, "enrich")
        sha1 = get_git_sha(self.root)
        assert sha1 is not None
        # After commit HEAD advanced; stale must still be False because
        # the save touched git_sha to HEAD before the commit, and HEAD
        # moved forward to sha1. graph.meta.git_sha is still sha0 on
        # disk because git_sha was stamped *before* the commit. So we
        # re-load and check.
        g2 = Graph(self.root)
        g2.load()
        r = g2.stale()
        # source_stale: no source file changed between graph_sha and HEAD
        self.assertFalse(
            r["source_stale"],
            f"source_stale should be False when no src/ files changed; got {r}",
        )
        self.assertFalse(r["stale"], f"expected stale=False, got {r}")

    def test_source_change_without_discover_reports_stale(self) -> None:
        _write_graph(self.root, git_sha=self._sha0, discovered_from=["src/"])
        # Touch a tracked source file and commit -- no discover run.
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        _commit_all(self.root, "src change")
        r = self._stale()
        self.assertTrue(r["source_stale"])
        self.assertTrue(r["stale"])
        # sha_behind is also True because HEAD moved.
        self.assertTrue(r["sha_behind"])

    def test_untracked_path_change_is_not_stale(self) -> None:
        """Changes outside `discovered_from` do not make the graph stale."""
        _write_graph(self.root, git_sha=self._sha0, discovered_from=["src/"])
        # README.md is NOT in src/ so it is not tracked by discover.
        (self.root / "README.md").write_text("hi again\n", encoding="utf-8")
        _commit_all(self.root, "readme change")
        r = self._stale()
        self.assertFalse(r["source_stale"])
        # but sha_behind should be True -- HEAD advanced
        self.assertTrue(r["sha_behind"])
        # 'stale' alias tracks source_stale
        self.assertFalse(r["stale"])

    def test_non_git_repo_keeps_legacy_shape(self) -> None:
        """Non-git roots continue to return stale=False with a reason."""
        non_git = Path(tempfile.mkdtemp())
        try:
            (non_git / ".weld").mkdir()
            _write_graph(non_git, git_sha=None, discovered_from=[])
            g = Graph(non_git)
            g.load()
            r = g.stale()
            self.assertFalse(r["stale"])
            self.assertFalse(r["source_stale"])
            self.assertFalse(r["sha_behind"])
            self.assertIn("reason", r)
        finally:
            import shutil
            shutil.rmtree(non_git, ignore_errors=True)

    def test_missing_graph_sha_falls_back_to_mtime(self) -> None:
        """When meta.git_sha is absent, the primary signal falls back to
        mtime-based detection. We cannot guarantee freshness so we treat
        it as potentially stale."""
        _write_graph(self.root, git_sha=None, discovered_from=["src/"])
        r = self._stale()
        # No git_sha recorded -> source_stale is True (we cannot prove
        # fidelity). sha_behind is False because there's no pointer.
        self.assertTrue(r["source_stale"])
        self.assertFalse(r["sha_behind"])
        self.assertIsNone(r["graph_sha"])


class GraphStaleBackcompatKeysTest(unittest.TestCase):
    """Existing keys (`graph_sha`, `current_sha`, `commits_behind`, `stale`)
    remain in the response dict for every non-edge-case path."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _git_init(self.root)
        (self.root / "README.md").write_text("hi\n", encoding="utf-8")
        _commit_all(self.root, "initial")
        self._sha0 = get_git_sha(self.root)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_dict_keys_present(self) -> None:
        _write_graph(self.root, git_sha=self._sha0, discovered_from=[])
        g = Graph(self.root)
        g.load()
        r = g.stale()
        for key in (
            "stale", "source_stale", "sha_behind",
            "graph_sha", "current_sha", "commits_behind",
        ):
            self.assertIn(key, r, f"missing key {key} in stale() output")


class GraphOnlyCommitDriftTest(unittest.TestCase):
    """Graph-only commits must not leave the graph in a stale-after-commit
    loop (bd-p1a.6).

    Workflow:
      1. Run discovery -> meta.git_sha stamped to HEAD_A.
      2. Optionally `wd touch` to re-stamp -> still HEAD_A on disk.
      3. Commit just .weld/graph.json -> HEAD advances to HEAD_B while
         meta.git_sha on disk still points at HEAD_A.
      4. `wd stale` must report source_stale=False AND sha_behind=False
         so that `wd prime` emits no graph next-step action. Otherwise
         the advisory nudges the user to `wd touch` again, which stamps
         the new HEAD, which then requires another commit, which bumps
         HEAD again -- an infinite touch/commit/touch loop.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _git_init(self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        _commit_all(self.root, "initial")
        self._sha0 = get_git_sha(self.root)
        assert self._sha0 is not None

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _stale(self) -> dict:
        g = Graph(self.root)
        g.load()
        return g.stale()

    def _commit_graph_only(self, msg: str) -> None:
        _run(["git", "add", ".weld/graph.json"], self.root)
        _run(["git", "commit", "-m", msg, "--quiet"], self.root)

    def test_graph_only_commit_does_not_mark_sha_behind(self) -> None:
        """Committing ONLY .weld/graph.json must collapse the SHA drift.

        Without the fix, after the commit:
          - source_stale=False (src/ unchanged) [already OK]
          - sha_behind=True    [this triggers the [advisory] / touch loop]

        With the fix, sha_behind must also be False because the only
        commits between graph_sha and HEAD touched nothing but the
        graph JSON -- the graph is effectively fresh.
        """
        _write_graph(self.root, git_sha=self._sha0, discovered_from=["src/"])
        # Commit the graph file. HEAD advances; graph_sha stays at sha0.
        self._commit_graph_only("commit graph")
        r = self._stale()
        self.assertFalse(r["source_stale"], f"source_stale must be False: {r}")
        self.assertFalse(r["stale"], f"stale must be False: {r}")
        self.assertFalse(
            r["sha_behind"],
            f"graph-only drift must not set sha_behind: {r}",
        )

    def test_graph_only_commit_preserves_backcompat_keys(self) -> None:
        _write_graph(self.root, git_sha=self._sha0, discovered_from=["src/"])
        self._commit_graph_only("commit graph")
        r = self._stale()
        for key in (
            "stale", "source_stale", "sha_behind",
            "graph_sha", "current_sha", "commits_behind",
        ):
            self.assertIn(key, r, f"missing key {key}")
        # graph_sha is still the original recorded value; HEAD moved.
        self.assertEqual(r["graph_sha"], self._sha0)
        self.assertNotEqual(r["current_sha"], self._sha0)

    def test_graph_only_then_source_change_is_stale(self) -> None:
        """A graph-only commit followed by a real source change must still
        flip source_stale (and sha_behind) to True -- the graph-only
        pass-through must not mask real drift."""
        _write_graph(self.root, git_sha=self._sha0, discovered_from=["src/"])
        self._commit_graph_only("commit graph")
        # Now change a tracked source and commit that too.
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        _commit_all(self.root, "src change")
        r = self._stale()
        self.assertTrue(
            r["source_stale"],
            f"source change must still be detected across graph-only drift: {r}",
        )
        self.assertTrue(r["sha_behind"])

    def test_graph_only_mixed_with_other_commit_keeps_sha_behind(self) -> None:
        """If ANY commit in the drift touched something other than the
        graph file, SHA drift must still be reported (the existing
        [advisory] path still applies)."""
        _write_graph(self.root, git_sha=self._sha0, discovered_from=["src/"])
        self._commit_graph_only("commit graph")
        # README is untracked by discover so source_stale stays False,
        # but sha_behind must remain True because a non-graph commit
        # happened.
        (self.root / "README.md").write_text("hi\n", encoding="utf-8")
        _commit_all(self.root, "readme")
        r = self._stale()
        self.assertFalse(r["source_stale"])
        self.assertTrue(
            r["sha_behind"],
            f"non-graph-only drift must still set sha_behind: {r}",
        )


class DriftIsGraphOnlyTest(unittest.TestCase):
    """Direct unit coverage for ``drift_is_graph_only``.

    The customer-reported 5-step repro in bd-...yb89 exercises this
    helper end-to-end via ``wd prime``; this file pins the helper
    against the cross product of the bookkeeping paths so a future
    regression in the path set is caught locally.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git_init(self.root)
        # Seed: one source file committed at sha0.
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        _run(["git", "add", "app.py"], self.root)
        _run(["git", "commit", "--quiet", "-m", "seed"], self.root)
        self._sha0 = get_git_sha(self.root)

    def _commit(self, paths: list[str], message: str) -> None:
        for rel in paths:
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n", encoding="utf-8")
            _run(["git", "add", rel], self.root)
        _run(["git", "commit", "--quiet", "-m", message], self.root)

    def test_graph_only_commit_is_graph_only(self) -> None:
        self._commit([".weld/graph.json"], "graph")
        self.assertTrue(drift_is_graph_only(self.root, self._sha0))

    def test_state_only_commit_is_graph_only(self) -> None:
        self._commit([".weld/discovery-state.json"], "state")
        self.assertTrue(drift_is_graph_only(self.root, self._sha0))

    def test_both_bookkeeping_paths_is_graph_only(self) -> None:
        self._commit(
            [".weld/graph.json", ".weld/discovery-state.json"],
            "graph and state",
        )
        self.assertTrue(drift_is_graph_only(self.root, self._sha0))

    def test_source_change_is_not_graph_only(self) -> None:
        self._commit(["app.py"], "edit source")
        self.assertFalse(drift_is_graph_only(self.root, self._sha0))

    def test_mixed_change_is_not_graph_only(self) -> None:
        self._commit(
            [".weld/graph.json", "app.py"], "graph plus source",
        )
        self.assertFalse(drift_is_graph_only(self.root, self._sha0))

    def test_empty_diff_is_not_graph_only(self) -> None:
        # No commit between sha0 and HEAD.
        self.assertFalse(drift_is_graph_only(self.root, self._sha0))


if __name__ == "__main__":
    unittest.main()
