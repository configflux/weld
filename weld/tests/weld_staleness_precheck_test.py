"""Read-path staleness precheck via the graph-meta sidecar (bd aqqa).

The precheck must feed :func:`weld._staleness.compute_stale_info` the exact
``git_sha`` / ``discovered_from`` it needs **without** parsing the multi-MB
``graph.json`` when the sidecar mirror is provably current, and must fall back
to the authoritative full parse -- yielding byte-identical decisions -- the
instant the mirror is missing, legacy, or stale.

Covers:

* ``write_graph_with_meta`` mirrors ``discovered_from`` + a ``(size, mtime_ns)``
  stat pin into the sidecar, matching the on-disk graph.json;
* ``read_staleness_meta`` hit / stat-guard fallback / legacy fallback / absent;
* ``read_meta_for_staleness`` fast path skips the graph parse;
* determinism: ``compute_stale_info`` is identical fast vs. full parse across a
  fresh, a committed-drift, and a dirty-worktree git state.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._graph_meta_sidecar import (
    merge_sidecar_meta,
    read_meta_for_staleness,
    read_staleness_meta,
    sidecar_path_for,
    write_graph_with_meta,
)
from weld._staleness import compute_stale_info


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=30, check=True,
        env={**os.environ, "LC_ALL": "C"},
    )


def _git_init(root: Path) -> None:
    _run(["git", "init", "--quiet"], root)
    _run(["git", "config", "user.email", "t@t.com"], root)
    _run(["git", "config", "user.name", "T"], root)
    _run(["git", "config", "commit.gpgsign", "false"], root)


def _git_sha(root: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    return out.stdout.strip()


def _graph(git_sha: str | None, discovered_from: list[str]) -> dict:
    meta: dict = {
        "version": 5,
        "updated_at": "2026-07-07T00:00:00+00:00",
        "schema_version": 1,
        "discovered_from": discovered_from,
    }
    if git_sha is not None:
        meta["git_sha"] = git_sha
    return {
        "meta": meta,
        "nodes": {"n:1": {"type": "file", "label": "L", "props": {}}},
        "edges": [],
    }


def _big_graph(git_sha: str, discovered_from: list[str]) -> dict:
    """A graph whose graph.json body is well over the fast-path guard size."""
    g = _graph(git_sha, discovered_from)
    g["nodes"] = {
        f"n:{i}": {"type": "file", "label": f"label-{i}", "props": {"i": i}}
        for i in range(400)
    }
    return g


class WriteMirrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.weld = Path(self._tmp.name) / ".weld"
        self.weld.mkdir(parents=True)
        self.graph_path = self.weld / "graph.json"

    def test_sidecar_mirrors_discovered_from_and_pins_stat(self) -> None:
        write_graph_with_meta(self.graph_path, _graph("abc123", ["src/"]))
        side = json.loads(sidecar_path_for(self.graph_path).read_text())
        st = self.graph_path.stat()
        self.assertEqual(side["discovered_from"], ["src/"])
        self.assertEqual(side["graph_size"], st.st_size)
        self.assertEqual(side["graph_mtime_ns"], st.st_mtime_ns)
        self.assertEqual(side["git_sha"], "abc123")
        # discovered_from stays authoritative in graph.json too.
        on_disk = json.loads(self.graph_path.read_text())
        self.assertEqual(on_disk["meta"]["discovered_from"], ["src/"])

    def test_read_staleness_meta_hit(self) -> None:
        write_graph_with_meta(self.graph_path, _graph("abc123", ["src/"]))
        self.assertEqual(
            read_staleness_meta(self.graph_path),
            {"git_sha": "abc123", "discovered_from": ["src/"]},
        )

    def test_stat_guard_rejects_rewritten_graph(self) -> None:
        write_graph_with_meta(self.graph_path, _graph("abc123", ["src/"]))
        # Rewrite graph.json under the sidecar's feet (simulates git checkout
        # of a committed graph): the stat pin no longer matches -> fall back.
        self.graph_path.write_text(
            self.graph_path.read_text() + "\n", encoding="utf-8"
        )
        self.assertIsNone(read_staleness_meta(self.graph_path))

    def test_legacy_sidecar_without_mirror_falls_back(self) -> None:
        # An older / wd-warm sidecar: volatile keys only, no mirror.
        self.graph_path.write_text(json.dumps(_graph(None, ["src/"])), "utf-8")
        sidecar_path_for(self.graph_path).write_text(
            json.dumps({"version": 1, "git_sha": "abc123"}), "utf-8"
        )
        self.assertIsNone(read_staleness_meta(self.graph_path))

    def test_absent_sidecar_falls_back(self) -> None:
        self.graph_path.write_text(json.dumps(_graph(None, ["src/"])), "utf-8")
        self.assertIsNone(read_staleness_meta(self.graph_path))

    def test_read_meta_for_staleness_fast_path_skips_graph_parse(self) -> None:
        write_graph_with_meta(self.graph_path, _big_graph("abc123", ["src/"]))
        self.assertGreater(self.graph_path.stat().st_size, 5_000)
        real_loads = json.loads

        def guard(s, *a, **k):  # type: ignore[no-untyped-def]
            # The tiny sidecar parse is fine; a full graph.json parse is not.
            if isinstance(s, (str, bytes)) and len(s) > 5_000:
                raise AssertionError("full graph parsed on fast path")
            return real_loads(s, *a, **k)

        json.loads = guard  # type: ignore[assignment]
        try:
            meta = read_meta_for_staleness(self.graph_path)
        finally:
            json.loads = real_loads  # type: ignore[assignment]
        self.assertEqual(meta, {"git_sha": "abc123", "discovered_from": ["src/"]})


class DeterminismTest(unittest.TestCase):
    """compute_stale_info must be identical fast (sidecar) vs. full parse."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git_init(self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        _run(["git", "add", "-A"], self.root)
        _run(["git", "commit", "-m", "seed", "--quiet"], self.root)
        self.weld = self.root / ".weld"
        self.weld.mkdir()
        self.graph_path = self.weld / "graph.json"

    def _write(self, git_sha: str | None) -> None:
        write_graph_with_meta(self.graph_path, _graph(git_sha, ["src/"]))

    def _assert_identical(self) -> dict:
        fast = read_meta_for_staleness(self.graph_path)
        data = json.loads(self.graph_path.read_text())
        full = merge_sidecar_meta(data.get("meta", {}), self.graph_path)
        info_fast = compute_stale_info(self.graph_path, fast)  # type: ignore[arg-type]
        info_full = compute_stale_info(self.graph_path, full)
        self.assertEqual(info_fast, info_full)
        return info_fast

    def test_identical_when_fresh(self) -> None:
        self._write(_git_sha(self.root))
        info = self._assert_identical()
        self.assertFalse(info["source_stale"])

    def test_identical_when_committed_drift(self) -> None:
        old = _git_sha(self.root)
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        _run(["git", "add", "-A"], self.root)
        _run(["git", "commit", "-m", "drift", "--quiet"], self.root)
        self._write(old)  # graph recorded at the pre-drift commit
        info = self._assert_identical()
        self.assertTrue(info["source_stale"])

    def test_identical_when_dirty_worktree(self) -> None:
        self._write(_git_sha(self.root))
        # Uncommitted edit to a tracked source file -> dirty-worktree stale.
        (self.root / "src" / "a.py").write_text("x = 3\n", encoding="utf-8")
        info = self._assert_identical()
        self.assertTrue(info["source_stale"])


if __name__ == "__main__":
    unittest.main()
