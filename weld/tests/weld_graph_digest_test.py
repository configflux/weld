"""Unit tests for the shared graph sha256 memo (bd aqqa).

Pin the two guarantees the read path relies on:

* a single 16 MB ``graph.json`` is streamed through sha256 **once** per cold
  read even though :mod:`weld._query_sidecar` (envelope check) and
  :mod:`weld._mcp_read` (cache key) both ask for it;
* the memo self-invalidates on any write, so it never serves a digest for
  stale bytes -- the freshness contract both callers depend on.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

import weld._graph_digest as gd
from weld._graph_digest import clear_digest_memo, file_sha256


class GraphDigestMemoTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_digest_memo()
        self.addCleanup(clear_digest_memo)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "graph.json"
        self.path.write_bytes(b'{"nodes": {}, "edges": []}\n')

    def test_digest_matches_hashlib(self) -> None:
        expected = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(file_sha256(self.path), expected)

    def test_second_call_is_memoized(self) -> None:
        calls = {"n": 0}
        real = gd._stream_sha256

        def counting(p: Path) -> str | None:
            calls["n"] += 1
            return real(p)

        gd._stream_sha256 = counting  # type: ignore[assignment]
        try:
            first = file_sha256(self.path)
            second = file_sha256(self.path)
        finally:
            gd._stream_sha256 = real  # type: ignore[assignment]
        self.assertEqual(first, second)
        self.assertEqual(calls["n"], 1)

    def test_delegating_callers_share_one_hash(self) -> None:
        """``_hash_graph_bytes`` and ``_file_sha`` reuse the same memo entry."""
        from weld._mcp_read import _file_sha
        from weld._query_sidecar import _hash_graph_bytes

        calls = {"n": 0}
        real = gd._stream_sha256

        def counting(p: Path) -> str | None:
            calls["n"] += 1
            return real(p)

        gd._stream_sha256 = counting  # type: ignore[assignment]
        try:
            a = file_sha256(self.path)
            b = _hash_graph_bytes(self.path)
            c = _file_sha(self.path)
        finally:
            gd._stream_sha256 = real  # type: ignore[assignment]
        self.assertTrue(a == b == c and a is not None)
        self.assertEqual(calls["n"], 1)

    def test_memo_invalidates_on_rewrite(self) -> None:
        first = file_sha256(self.path)
        # Rewrite with different content -> different size/mtime -> fresh hash.
        self.path.write_bytes(b'{"nodes": {"n:1": {}}, "edges": []}\n')
        second = file_sha256(self.path)
        self.assertNotEqual(first, second)
        self.assertEqual(second, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_memo_invalidates_on_mtime_bump(self) -> None:
        """A same-size, same-content ``os.utime`` bump busts the memo.

        Directly covers the ``st_mtime_ns`` arm of the ``(mtime_ns, size)`` key:
        size and content are held constant, so the only thing that can drive a
        recomputation is the mtime change. Guards against a regression that
        dropped mtime from the key (which would then serve the stale entry). The
        digest is unchanged (same bytes), so the recompute is asserted via the
        stream-hash call count rather than a value difference.
        """
        calls = {"n": 0}
        real = gd._stream_sha256

        def counting(p: Path) -> str | None:
            calls["n"] += 1
            return real(p)

        gd._stream_sha256 = counting  # type: ignore[assignment]
        try:
            first = file_sha256(self.path)
            before = self.path.stat()
            # Bump mtime_ns a full second forward without touching size/content.
            os.utime(
                self.path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000)
            )
            after = self.path.stat()
            self.assertNotEqual(after.st_mtime_ns, before.st_mtime_ns)  # precondition
            self.assertEqual(after.st_size, before.st_size)  # size held constant
            second = file_sha256(self.path)
        finally:
            gd._stream_sha256 = real  # type: ignore[assignment]
        # Same bytes -> identical digest, but the memo MUST have recomputed
        # (count 2): the mtime arm -- not size -- drove the invalidation.
        self.assertEqual(first, second)
        self.assertEqual(calls["n"], 2)

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(file_sha256(Path(self._tmp.name) / "absent.json"))


if __name__ == "__main__":
    unittest.main()
