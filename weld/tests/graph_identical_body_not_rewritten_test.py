"""A graph whose bytes did not change is not rewritten (bd emyk).

``graph.json`` is written by atomic rename, so *every* write lands a fresh
inode and a fresh ``st_mtime_ns`` -- including a write of byte-identical
content. That is not free. ``st_mtime_ns`` is the cheap half of the
inventory<->graph binding: ``discovery-state.json`` records
``published_graph = {sha256, size, mtime_ns}`` and
``_token_pins_file`` answers "is this still the body my inventory describes"
with one ``stat`` instead of hashing a multi-megabyte file. Rewriting
identical bytes moves the mtime, so the token stops pinning, so the token is
re-stamped -- and under ADR 0110, where ``discovery-state.json`` is a tracked
file, that surfaced as one line of git diff on every no-change ``wd discover``
(``"mtime_ns": <moves every run>``).

Carrying a stale ``mtime_ns`` forward is not the fix -- that would make the
steady-state read miss its fast path and pay a full digest on every read,
which is precisely what bd aqqa moved the freshness precheck off the graph
body to avoid. The only sound fix is the one pinned here: **do not rewrite an
unchanged graph.** Then the inode holds still, the token keeps pinning, the
sidecar mirror keeps matching, and a no-change run leaves the tracked
artifacts byte-identical.

The skip already existed, reachable only through the one caller that passed
``on_disk_bytes`` (bd 85tb.2, the incremental no-change refresh). Every other
writer -- ``Graph.save``, the ``wd discover`` CLI tail, ``wd warm``, the
federated writers -- went down an ``else`` branch that wrote unconditionally.
The decision now lives in the writer, once, so a caller cannot opt out of it
by not knowing about it.

What must NOT be skipped is pinned here too: the volatile ``graph-meta.json``
sidecar carries ``updated_at`` / ``git_sha`` and re-stamps on every run, and
its ``graph_mtime_ns`` mirror has to keep pointing at the graph the skip left
alone -- otherwise the read-path precheck falls back to a full parse and the
saving is spent twice over.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._discover_state_check import (
    _token_pins_file,
    published_graph_token,
)
from weld._graph_meta_sidecar import (
    read_staleness_meta,
    sidecar_path_for,
    write_graph_with_meta,
)


def _graph(extra_node: str | None = None) -> dict:
    """A minimal canonical graph, optionally carrying one more node."""
    nodes = {
        "file:a": {"type": "file", "label": "a", "props": {"file": "a.py"}},
    }
    if extra_node is not None:
        nodes[extra_node] = {
            "type": "file", "label": extra_node, "props": {"file": "b.py"},
        }
    return {
        "meta": {
            "version": 5,
            "discovered_from": ["a.py"],
            "updated_at": "2026-01-01T00:00:00Z",
        },
        "nodes": nodes,
        "edges": [],
    }


def _identity(path: Path) -> tuple[int, int, int]:
    """``(inode, size, mtime_ns)`` -- what a rewrite would move."""
    info = path.stat()
    return info.st_ino, info.st_size, info.st_mtime_ns


class IdenticalBodyIsNotRewrittenTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.weld = Path(self._tmp.name) / ".weld"
        self.weld.mkdir(parents=True)
        self.graph_path = self.weld / "graph.json"

    def test_rewriting_the_same_graph_holds_the_inode_still(self) -> None:
        """The defect in one assertion: same bytes in, same file on disk."""
        write_graph_with_meta(self.graph_path, _graph())
        first = _identity(self.graph_path)
        write_graph_with_meta(self.graph_path, _graph())
        self.assertEqual(
            _identity(self.graph_path), first,
            "an identical graph was rewritten: the atomic rename landed a new "
            "inode and mtime, which is what invalidates every cheap freshness "
            "check keyed on the stat pair",
        )

    def test_a_volatile_only_change_still_does_not_rewrite_the_body(self) -> None:
        """``updated_at`` moves every run and is *not* in ``graph.json``.

        It is split to the sidecar (ADR 0065), so a run that differs only in
        wall clock produces identical canonical bytes -- which is exactly the
        no-change discover this fix is about.
        """
        write_graph_with_meta(self.graph_path, _graph())
        first = _identity(self.graph_path)
        later = _graph()
        later["meta"]["updated_at"] = "2026-12-31T23:59:59Z"
        later["meta"]["git_sha"] = "deadbeef"
        write_graph_with_meta(self.graph_path, later)
        self.assertEqual(_identity(self.graph_path), first)

    def test_a_real_change_is_still_written(self) -> None:
        """The skip must never swallow a change; that is the whole risk."""
        write_graph_with_meta(self.graph_path, _graph())
        before = _identity(self.graph_path)
        write_graph_with_meta(self.graph_path, _graph(extra_node="file:b"))
        after = _identity(self.graph_path)
        self.assertNotEqual(after, before)
        body = json.loads(self.graph_path.read_text(encoding="utf-8"))
        self.assertIn("file:b", body["nodes"])

    def test_a_shrinking_change_is_still_written(self) -> None:
        """The size shortcut decides "different" as well as "same"."""
        write_graph_with_meta(self.graph_path, _graph(extra_node="file:b"))
        write_graph_with_meta(self.graph_path, _graph())
        body = json.loads(self.graph_path.read_text(encoding="utf-8"))
        self.assertNotIn("file:b", body["nodes"])

    def test_a_missing_graph_is_written_not_skipped(self) -> None:
        """Nothing on disk cannot match, so the first write always happens."""
        write_graph_with_meta(self.graph_path, _graph())
        self.assertTrue(self.graph_path.is_file())
        self.assertIn("file:a", json.loads(
            self.graph_path.read_text(encoding="utf-8"))["nodes"])

    def test_a_corrupted_body_is_repaired_rather_than_matched(self) -> None:
        """Same length, different bytes: the digest has to settle it."""
        write_graph_with_meta(self.graph_path, _graph())
        good = self.graph_path.read_text(encoding="utf-8")
        corrupt = good.replace("file:a", "file:X", 1)
        self.assertEqual(len(corrupt), len(good), "fixture must keep the size")
        self.graph_path.write_text(corrupt, encoding="utf-8")
        write_graph_with_meta(self.graph_path, _graph())
        self.assertEqual(self.graph_path.read_text(encoding="utf-8"), good)


class SkippingTheBodyKeepsTheClaimsTrueTest(unittest.TestCase):
    """The freshness contract the skip exists to preserve, and must not break."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.weld = Path(self._tmp.name) / ".weld"
        self.weld.mkdir(parents=True)
        self.graph_path = self.weld / "graph.json"

    def test_the_published_graph_token_still_pins_the_file(self) -> None:
        """The emyk symptom, at the layer it is actually observed.

        A token minted after one write must still pin the file after a second,
        identical write -- that is what stops ``discovery-state.json`` from
        re-stamping ``published_graph.mtime_ns`` on a no-change run.
        """
        write_graph_with_meta(self.graph_path, _graph())
        token = published_graph_token(self.graph_path)
        self.assertIsNotNone(token)
        write_graph_with_meta(self.graph_path, _graph())
        self.assertTrue(
            _token_pins_file(token, self.graph_path),
            "the token stopped pinning after an identical rewrite, so the "
            "inventory would re-stamp mtime_ns -- the bd emyk diff",
        )

    def test_a_real_change_stops_the_token_pinning(self) -> None:
        """The other direction: the binding must still notice a new body."""
        write_graph_with_meta(self.graph_path, _graph())
        token = published_graph_token(self.graph_path)
        write_graph_with_meta(self.graph_path, _graph(extra_node="file:b"))
        self.assertFalse(_token_pins_file(token, self.graph_path))

    def test_the_volatile_sidecar_is_still_refreshed(self) -> None:
        """Skipping the body must not skip the record that dates it."""
        write_graph_with_meta(self.graph_path, _graph())
        later = _graph()
        later["meta"]["updated_at"] = "2026-12-31T23:59:59Z"
        later["meta"]["git_sha"] = "deadbeef"
        write_graph_with_meta(self.graph_path, later)
        sidecar = json.loads(
            sidecar_path_for(self.graph_path).read_text(encoding="utf-8"))
        self.assertEqual(sidecar["updated_at"], "2026-12-31T23:59:59Z")
        self.assertEqual(sidecar["git_sha"], "deadbeef")

    def test_the_sidecar_mirror_still_pins_the_untouched_graph(self) -> None:
        """``read_staleness_meta`` is the read-path fast lane (bd aqqa).

        It rejects its own mirror the moment ``graph_mtime_ns`` stops matching
        ``graph.json``. Skipping the body leaves the *older* mtime on the
        file, so the sidecar written afterwards has to re-stat that untouched
        file rather than assume a fresh one -- if it did not, every read after
        a no-change discover would fall back to a full parse.
        """
        write_graph_with_meta(self.graph_path, _graph())
        later = _graph()
        later["meta"]["git_sha"] = "deadbeef"
        write_graph_with_meta(self.graph_path, later)
        mirror = read_staleness_meta(self.graph_path)
        self.assertIsNotNone(
            mirror,
            "the sidecar mirror stopped pinning graph.json after a skipped "
            "body write; the read-path precheck would parse the graph on "
            "every read",
        )
        self.assertEqual(mirror["git_sha"], "deadbeef")
        self.assertEqual(mirror["discovered_from"], ["a.py"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
