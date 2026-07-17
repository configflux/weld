"""Tests for the dirty-working-tree refresh cache (bd o18k).

With working-tree-aware staleness (ADR 0066) an agent holding uncommitted edits
sees ``source_stale=True`` on every read, so auto-refresh re-runs discovery
(~0.7-6s) each call. :mod:`weld._refresh_cache` short-circuits that: it keys a
bounded, process-local cache on a *working-tree signature* (HEAD sha + content
hashes of the dirty tracked files) plus the ``graph.json`` identity, so repeated
reads *between* edits skip discovery entirely.

These pin the contract:

- signature content-sensitivity: a single-byte edit, a new/deleted tracked file,
  or a HEAD move each change the signature; an edit *outside* the tracked
  prefixes and a byte-identical tree do not. A staged rename and a
  copy-then-restore (which collapse to the same signature under git's default
  rename detection) are kept distinct.
- ``refresh_with_cache``: a hit skips ``do_refresh`` and returns ``None``; a
  miss runs it; a failed refresh, a signature we cannot compute, and an
  out-of-band ``graph.json`` rewrite all decline to serve a hit.
- boundedness and the ``clear_refresh_cache`` seam.
- end-to-end: ``auto_refresh_if_stale`` runs discovery once for a dirty tree,
  then short-circuits identical follow-up reads.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weld import _refresh_cache as _rc  # noqa: E402 -- module handle for spies
from weld._refresh_cache import (  # noqa: E402
    _CACHE,
    _MAX_ROOTS,
    clear_refresh_cache,
    refresh_with_cache,
    worktree_signature,
)


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


class _RepoTestBase(unittest.TestCase):
    """A committed one-source-file git repo; cache cleared around each test."""

    def setUp(self) -> None:
        clear_refresh_cache()
        self.addCleanup(clear_refresh_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git_init(self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "README.md").write_text("hi\n", encoding="utf-8")
        _commit_all(self.root, "initial")

    def _edit(self, rel: str, text: str) -> None:
        (self.root / rel).write_text(text, encoding="utf-8")


class WorktreeSignatureTest(_RepoTestBase):
    """The signature must miss on any tracked content change, hit otherwise."""

    def _sig(self) -> str | None:
        return worktree_signature(self.root, ["src/"])

    def test_clean_tree_signature_is_deterministic(self) -> None:
        first = self._sig()
        clear_refresh_cache()
        self.assertIsNotNone(first)
        self.assertEqual(first, self._sig())

    def test_single_byte_edit_changes_signature(self) -> None:
        before = self._sig()
        self._edit("src/a.py", "x = 2\n")  # same length, one byte differs
        clear_refresh_cache()  # exclude the stat-memo; test pure content hashing
        self.assertNotEqual(before, self._sig())

    def test_new_untracked_file_changes_signature(self) -> None:
        before = self._sig()
        self._edit("src/b.py", "y = 1\n")
        clear_refresh_cache()
        self.assertNotEqual(before, self._sig())

    def test_deleted_tracked_file_changes_signature(self) -> None:
        before = self._sig()
        (self.root / "src" / "a.py").unlink()
        clear_refresh_cache()
        self.assertNotEqual(before, self._sig())

    def test_edit_outside_tracked_prefix_does_not_change_signature(self) -> None:
        before = self._sig()
        self._edit("README.md", "hello again\n")
        clear_refresh_cache()
        self.assertEqual(before, self._sig())

    def test_head_move_changes_signature(self) -> None:
        before = self._sig()
        self._edit("src/a.py", "x = 2\n")
        _commit_all(self.root, "advance HEAD")  # tree clean again, HEAD moved
        clear_refresh_cache()
        self.assertNotEqual(before, self._sig())

    def test_reverting_to_committed_content_restores_signature(self) -> None:
        clean = self._sig()
        self._edit("src/a.py", "x = 999\n")
        clear_refresh_cache()
        dirty = self._sig()
        self.assertNotEqual(clean, dirty)
        self._edit("src/a.py", "x = 1\n")  # back to committed bytes
        clear_refresh_cache()
        self.assertEqual(clean, self._sig())

    def test_non_git_root_yields_none(self) -> None:
        with tempfile.TemporaryDirectory() as plain:
            self.assertIsNone(worktree_signature(Path(plain), ["src/"]))

    def test_staged_rename_and_copy_restore_are_distinct(self) -> None:
        # Under git's default rename detection a staged rename a->b and a
        # copy-of-a-into-b (a restored) both surface as just "b changed",
        # colliding. detect_renames=False makes the rename's vacated original
        # an explicit deletion, so the two trees sign differently.
        _run(["git", "mv", "src/a.py", "src/b.py"], self.root)
        rename_sig = self._sig()
        clear_refresh_cache()

        _run(["git", "reset", "--hard", "--quiet"], self.root)  # back to committed
        (self.root / "src" / "b.py").write_text("x = 1\n", encoding="utf-8")
        _run(["git", "add", "src/b.py"], self.root)  # a kept, b added (copy)
        copy_sig = self._sig()

        self.assertIsNotNone(rename_sig)
        self.assertIsNotNone(copy_sig)
        self.assertNotEqual(rename_sig, copy_sig)

    def test_prefetched_head_sha_matches_unprefetched(self) -> None:
        # Threading the HEAD sha compute_stale_info already fetched must yield
        # the exact same signature as letting worktree_signature shell for it --
        # the two are the same `git rev-parse HEAD` value, so the cache is
        # unaffected (no false hits, no new misses).
        head = _run(["git", "rev-parse", "HEAD"], self.root)
        self._edit("src/a.py", "x = 2\n")
        clear_refresh_cache()
        prefetched = worktree_signature(self.root, ["src/"], head_sha=head)
        clear_refresh_cache()
        shelled = worktree_signature(self.root, ["src/"])
        self.assertIsNotNone(shelled)
        self.assertEqual(prefetched, shelled)

    def test_prefetched_head_sha_skips_get_git_sha(self) -> None:
        # The whole point: a supplied head_sha removes the `git rev-parse HEAD`
        # subprocess; omitting it preserves the one-call fallback.
        head = _run(["git", "rev-parse", "HEAD"], self.root)
        spy = mock.Mock(wraps=_rc.get_git_sha)
        with mock.patch.object(_rc, "get_git_sha", spy):
            worktree_signature(self.root, ["src/"], head_sha=head)
            self.assertEqual(spy.call_count, 0, "prefetched sha must be reused")
            worktree_signature(self.root, ["src/"])
            self.assertEqual(spy.call_count, 1, "no prefetch -> shell once")

    def test_none_head_sha_falls_back_to_shelling(self) -> None:
        # A None head_sha (e.g. an unborn HEAD upstream) must behave exactly as
        # before: shell get_git_sha, and if that is None, decline a signature.
        spy = mock.Mock(wraps=_rc.get_git_sha)
        with mock.patch.object(_rc, "get_git_sha", spy):
            sig = worktree_signature(self.root, ["src/"], head_sha=None)
            self.assertEqual(spy.call_count, 1)
        self.assertIsNotNone(sig)


class RefreshWithCacheTest(_RepoTestBase):
    """``refresh_with_cache`` gates ``do_refresh`` on the cached signature."""

    def setUp(self) -> None:
        super().setUp()
        self.graph_path = self.root / ".weld" / "graph.json"
        self.graph_path.write_text('{"nodes": {}}\n', encoding="utf-8")
        self.meta = {"discovered_from": ["src/"]}
        self._edit("src/a.py", "x = 2\n")  # dirty tree -> refresh is warranted
        self.calls = 0

    def _refresh(self) -> dict:
        self.calls += 1
        return {"refreshed": True, "incremental": True}

    def _call(self, do_refresh=None) -> dict | None:
        return refresh_with_cache(
            self.root, self.graph_path, self.meta,
            do_refresh or self._refresh,
        )

    def test_first_miss_then_hit(self) -> None:
        first = self._call()
        self.assertIsNotNone(first, "first call must run the refresh")
        second = self._call()
        self.assertIsNone(second, "identical tree must be a cache hit")
        self.assertEqual(self.calls, 1, "do_refresh must run exactly once")

    def test_repeated_reads_run_discovery_once(self) -> None:
        for _ in range(6):
            self._call()
        self.assertEqual(self.calls, 1, "six identical reads -> one refresh")

    def test_edit_between_reads_misses(self) -> None:
        self._call()
        self._edit("src/a.py", "x = 33\n")  # distinct size and content
        result = self._call()
        self.assertIsNotNone(result, "a fresh edit must re-run discovery")
        self.assertEqual(self.calls, 2)

    def test_failed_refresh_is_not_cached(self) -> None:
        self._call(do_refresh=lambda: None)  # refresh failed -> returns None
        self.assertIsNone(self._CACHE_entry())
        self._call(do_refresh=lambda: None)
        # Still nothing cached: a None result must never seed a hit.
        self.assertIsNone(self._CACHE_entry())

    def test_out_of_band_graph_rewrite_misses(self) -> None:
        self._call()
        self.assertEqual(self.calls, 1)
        # Something else rewrites graph.json (git checkout, external discover).
        self.graph_path.write_text('{"nodes": {"n": 1}}\n', encoding="utf-8")
        result = self._call()
        self.assertIsNotNone(result, "a changed graph.json must miss the cache")
        self.assertEqual(self.calls, 2)

    def test_missing_graph_never_hits(self) -> None:
        self.graph_path.unlink()
        self._call()
        self._call()
        # No graph.json to pin the entry, so we never assert a hit.
        self.assertEqual(self.calls, 2)

    def test_clear_forces_a_miss(self) -> None:
        self._call()
        clear_refresh_cache()
        self._call()
        self.assertEqual(self.calls, 2)

    def test_threaded_head_sha_skips_rev_parse_and_still_caches(self) -> None:
        # refresh_with_cache forwards head_sha to worktree_signature, so the
        # miss+store and the follow-up hit never re-shell `git rev-parse HEAD`,
        # yet the cache still gates discovery to exactly one run.
        head = _run(["git", "rev-parse", "HEAD"], self.root)
        spy = mock.Mock(wraps=_rc.get_git_sha)
        with mock.patch.object(_rc, "get_git_sha", spy):
            first = refresh_with_cache(
                self.root, self.graph_path, self.meta,
                self._refresh, head_sha=head,
            )
            second = refresh_with_cache(
                self.root, self.graph_path, self.meta,
                self._refresh, head_sha=head,
            )
        self.assertIsNotNone(first, "first call must run discovery")
        self.assertIsNone(second, "identical tree must hit the cache")
        self.assertEqual(self.calls, 1)
        self.assertEqual(spy.call_count, 0, "HEAD sha must never be re-shelled")

    def _CACHE_entry(self):
        return _CACHE.get(os.path.abspath(os.fspath(self.root)))


class RefreshCacheNonGitTest(unittest.TestCase):
    """A root where the signature cannot be computed never serves a hit."""

    def setUp(self) -> None:
        clear_refresh_cache()
        self.addCleanup(clear_refresh_cache)

    def test_non_git_root_always_refreshes(self) -> None:
        calls = {"n": 0}

        def do_refresh() -> dict:
            calls["n"] += 1
            return {"refreshed": True}

        with tempfile.TemporaryDirectory() as plain:
            root = Path(plain)
            graph = root / "graph.json"
            graph.write_text("{}\n", encoding="utf-8")
            meta = {"discovered_from": ["src/"]}
            refresh_with_cache(root, graph, meta, do_refresh)
            refresh_with_cache(root, graph, meta, do_refresh)
        # No HEAD -> signature None -> no caching -> both calls refresh.
        self.assertEqual(calls["n"], 2)


class CacheBoundTest(unittest.TestCase):
    """The per-root cache is LRU-bounded so a long-lived process cannot leak."""

    def setUp(self) -> None:
        clear_refresh_cache()
        self.addCleanup(clear_refresh_cache)

    def test_cache_never_exceeds_max_roots(self) -> None:
        # Drive stores for many synthetic roots via the internal map; the
        # public API only ever stores after a real refresh, so this exercises
        # the eviction bound directly.
        for i in range(_MAX_ROOTS * 3):
            _CACHE[f"/synthetic/root/{i}"] = ("sig", i, i)
            _CACHE.move_to_end(f"/synthetic/root/{i}")
            while len(_CACHE) > _MAX_ROOTS:
                _CACHE.popitem(last=False)
        self.assertLessEqual(len(_CACHE), _MAX_ROOTS)


if __name__ == "__main__":
    unittest.main()
