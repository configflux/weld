"""End-to-end tests for the dirty-tree refresh cache (bd o18k, bd q3le).

Split from ``refresh_cache_test.py`` to keep each module under the 400-line cap.
These drive the cache through the real
:func:`weld._auto_refresh.auto_refresh_if_stale` entry point (only ``_do_refresh``
is stubbed), including that a cache-hit read reuses the HEAD sha
``compute_stale_info`` already resolved rather than re-shelling ``git rev-parse``.

The tiny git-scaffolding helpers below are duplicated from the unit-test module
on purpose: they are trivial and shared through Bazel ``//weld:runtime`` only, not
a cross-test-module import (which Bazel does not wire).
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weld import _refresh_cache as _rc  # noqa: E402 -- module handle for spies
from weld._refresh_cache import clear_refresh_cache  # noqa: E402


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


class AutoRefreshCacheIntegrationTest(unittest.TestCase):
    """End-to-end: auto_refresh_if_stale discovers once, then short-circuits."""

    def setUp(self) -> None:
        clear_refresh_cache()
        self.addCleanup(clear_refresh_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git_init(self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        _commit_all(self.root, "initial")
        self._sha0 = _run(["git", "rev-parse", "HEAD"], self.root)
        (self.root / ".weld" / "discover.yaml").write_text(
            "sources: []\n", encoding="utf-8"
        )
        self._write_graph()

    def _write_graph(self) -> None:
        import json
        from weld.contract import SCHEMA_VERSION
        payload = {
            "meta": {
                "version": SCHEMA_VERSION,
                "updated_at": "2026-07-08T00:00:00+00:00",
                "git_sha": self._sha0,
                "discovered_from": ["src/"],
            },
            "nodes": {},
            "edges": [],
        }
        (self.root / ".weld" / "graph.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def _auto_refresh(self):
        from weld._auto_refresh import auto_refresh_if_stale
        env = {k: v for k, v in os.environ.items() if k != "WELD_AUTO_REFRESH"}
        return auto_refresh_if_stale(self.root, env=env, stderr=io.StringIO())

    def test_dirty_tree_discovers_once_then_hits(self) -> None:
        # A stub _do_refresh stands in for real discovery so the test is
        # hermetic; it is the exact call the cache must elide on a repeat read.
        calls = {"n": 0}

        def fake_do_refresh(root, *, safe, json_output, stderr):
            calls["n"] += 1
            return {"refreshed": True, "incremental": True,
                    "elapsed_ms": 0, "files_changed": 1}

        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        with mock.patch("weld._auto_refresh._do_refresh", fake_do_refresh):
            first = self._auto_refresh()
            second = self._auto_refresh()
            third = self._auto_refresh()
        self.assertIsNotNone(first, "dirty tree must trigger the first refresh")
        self.assertIsNone(second, "unchanged dirty tree must be a cache hit")
        self.assertIsNone(third, "and stay a hit")
        self.assertEqual(calls["n"], 1, "discovery must run exactly once")

    def test_new_edit_after_hit_re_triggers_discovery(self) -> None:
        calls = {"n": 0}

        def fake_do_refresh(root, *, safe, json_output, stderr):
            calls["n"] += 1
            return {"refreshed": True, "incremental": True,
                    "elapsed_ms": 0, "files_changed": 1}

        with mock.patch("weld._auto_refresh._do_refresh", fake_do_refresh):
            (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
            self._auto_refresh()
            self._auto_refresh()  # hit
            (self.root / "src" / "a.py").write_text("x = 33\n", encoding="utf-8")
            self._auto_refresh()  # new content -> miss
        self.assertEqual(calls["n"], 2)

    def test_hit_path_reuses_compute_stale_info_head_sha(self) -> None:
        # End-to-end proof of the optimization: across the miss and the hit the
        # refresh-cache layer never shells `git rev-parse HEAD` because it
        # reuses the HEAD sha compute_stale_info already fetched. Before this
        # change the count was 2 (once per auto_refresh call).
        calls = {"n": 0}

        def fake_do_refresh(root, *, safe, json_output, stderr):
            calls["n"] += 1
            return {"refreshed": True, "incremental": True,
                    "elapsed_ms": 0, "files_changed": 1}

        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        spy = mock.Mock(wraps=_rc.get_git_sha)
        with mock.patch.object(_rc, "get_git_sha", spy), \
                mock.patch("weld._auto_refresh._do_refresh", fake_do_refresh):
            first = self._auto_refresh()
            second = self._auto_refresh()
        self.assertIsNotNone(first, "dirty tree triggers the first refresh")
        self.assertIsNone(second, "unchanged dirty tree is a cache hit")
        self.assertEqual(calls["n"], 1, "discovery runs exactly once")
        self.assertEqual(
            spy.call_count, 0,
            "refresh cache reused the prefetched HEAD sha on miss and hit",
        )


if __name__ == "__main__":
    unittest.main()
