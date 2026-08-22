"""Gate 5 under concurrency and corruption (ADR 0096 §2).

A seed reads another checkout's ``.weld/`` while that checkout is free to
be doing anything at all -- a ``wd discover`` may land a whole new
generation of files between our read of ``graph.json`` and our copy of
the state beside it. The state carries no binding back to the graph it
belongs to, so a mismatched pair is a *silent* wrong answer later:
content hashes from one revision mark files unchanged in a graph built
from another, and the next incremental discover skips exactly the files
it needed to re-extract.

So the rules asserted here are the safety ones, not the fast ones. Prove
graph and state came from the same generation of the source, or take the
full pass. Never land a second graph over one somebody else just landed.
Never let a held lock, a wedged source, or a file that is not a graph
turn a read into a failure.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest import mock

from weld._graph_write_lock import graph_write_lock
from weld._worktree_seed import ensure_seeded
from weld.tests._mode_a_fixture import (
    ALPHA_NODE,
    SIDECAR,
    ModeAFixture,
    graph_nodes,
    read,
    stale_info,
    weld_listing,
)

_MODE_A = "weld._worktree_seed_mode_a"


class MidCopyRewriteTests(ModeAFixture):
    """The source moved between our two reads -- prove it, or drop the state."""

    def _rewriting_copy(self, *, generations: int, disturb: str = "graph.json"):
        """Real ``copy_state_files``, then bump *disturb*'s mtime at the source.

        Standing in for a ``wd discover`` finishing in the source checkout
        while we copy. *generations* bounds how many attempts are
        disturbed, so a test can choose between "the retry recovers" and
        "every attempt races"; *disturb* chooses which half of the
        source's paired write lands in the window.
        """
        from weld._worktree_seed_copy import copy_state_files

        state = {"calls": 0}
        target = self.origin / ".weld" / disturb

        def wrapper(source, root):
            copied = copy_state_files(source, root)
            state["calls"] += 1
            if state["calls"] <= generations:
                info = target.stat()
                os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
            return copied

        return wrapper, state

    def test_a_single_racing_generation_is_absorbed_by_the_retry(self) -> None:
        worktree = self.worktree()
        wrapper, calls = self._rewriting_copy(generations=1)

        with mock.patch(f"{_MODE_A}.copy_state_files", wrapper):
            result = ensure_seeded(worktree)

        self.assertEqual(calls["calls"], 2, "the copy must be retried exactly once")
        self.assertIn("discovery-state.json", result["seeded_state"])
        self.assertIn(ALPHA_NODE, graph_nodes(worktree))

    def test_a_persistently_racing_source_yields_a_graph_without_state(self) -> None:
        """Losing the state costs a full reconcile; keeping it would be wrong."""
        worktree = self.worktree()
        wrapper, calls = self._rewriting_copy(generations=99)

        with mock.patch(f"{_MODE_A}.copy_state_files", wrapper):
            result = ensure_seeded(worktree)

        self.assertEqual(calls["calls"], 2, "one retry, then accept the full pass")
        self.assertEqual(result["seeded_state"], [])
        self.assertIsNone(
            result["git_sha"],
            "a basis not proven to describe the landed bytes is not recorded",
        )
        self.assertIn(ALPHA_NODE, graph_nodes(worktree), "the graph still lands")

    def test_a_sidecar_rewritten_mid_copy_is_never_stamped(self) -> None:
        """The basis is half of a paired write, and is watched like the rest.

        ``write_graph_with_meta`` lands ``graph.json`` and its sidecar
        separately, so a copy starting between them would pair one
        generation's graph with the next generation's ``git_sha``. Should
        that sha happen to match our own HEAD, the seed would read as
        fresh while describing content this worktree does not have -- the
        exact failure ADR 0096 exists to prevent.
        """
        worktree = self.worktree()
        wrapper, _calls = self._rewriting_copy(generations=99, disturb=SIDECAR)

        with mock.patch(f"{_MODE_A}.copy_state_files", wrapper):
            result = ensure_seeded(worktree)

        self.assertIsNone(result["git_sha"])
        self.assertIn(ALPHA_NODE, graph_nodes(worktree), "the graph still lands")

    def test_dropping_state_never_removes_files_we_did_not_copy(self) -> None:
        """Only our own copies are withdrawn; the checkout's own files stay."""
        worktree = self.worktree()
        mine = worktree / ".weld" / "file-index.json"
        mine.write_text('{"version": 1, "files": {}}\n', encoding="utf-8")
        wrapper, _calls = self._rewriting_copy(generations=99)

        with mock.patch(f"{_MODE_A}.copy_state_files", wrapper):
            ensure_seeded(worktree)

        self.assertTrue(mine.is_file(), "a pre-existing state file must survive")


class ConcurrentSeederTests(ModeAFixture):
    """Two processes, one graph: the lock plus a re-check settles it."""

    def test_a_graph_that_appeared_while_we_waited_is_not_clobbered(self) -> None:
        """The double-check inside the lock is what makes the lock useful.

        The patched lock stands in for the process that won it: by the
        time we are inside, its graph is on disk and its reconcile may
        already be running. Landing a second copy over that is the one
        thing the re-check exists to prevent.
        """
        worktree = self.worktree()
        winner = b'{"nodes": {"file:winner": {"id": "file:winner"}}, "edges": []}'

        @contextmanager
        def lock_then_lose(root, **kwargs):
            (worktree / ".weld" / "graph.json").write_bytes(winner)
            yield

        with mock.patch(f"{_MODE_A}.graph_write_lock", lock_then_lose):
            self.assertIsNone(ensure_seeded(worktree))

        self.assertEqual(
            (worktree / ".weld" / "graph.json").read_bytes(),
            winner,
            "the winner's graph must be left exactly as it was",
        )

    def test_a_held_lock_declines_instead_of_raising(self) -> None:
        """A read must never fail because seeding could not get the lock."""
        worktree = self.worktree()
        before = weld_listing(worktree)

        with graph_write_lock(worktree):
            with mock.patch.dict(os.environ, {"WELD_GRAPH_LOCK_TIMEOUT": "0"}):
                self.assertIsNone(ensure_seeded(worktree))

        self.assertFalse((worktree / ".weld" / "graph.json").exists())
        self.assertEqual(weld_listing(worktree) - {"graph.write.lock"}, before)


class UnusableSourceTests(ModeAFixture):
    """Bytes from another checkout are data, and are checked like data."""

    def test_a_source_that_is_not_a_graph_is_refused(self) -> None:
        (self.origin / ".weld" / "graph.json").write_text(
            '{"not": "a graph"}', encoding="utf-8",
        )
        worktree = self.worktree()

        self.assertIsNone(ensure_seeded(worktree))
        self.assertFalse((worktree / ".weld" / "graph.json").exists())

    def test_a_truncated_source_is_refused(self) -> None:
        graph = self.origin / ".weld" / "graph.json"
        graph.write_bytes(graph.read_bytes()[:40])
        worktree = self.worktree()

        self.assertIsNone(ensure_seeded(worktree))
        self.assertFalse((worktree / ".weld" / "graph.json").exists())


class FailedReconcileTests(ModeAFixture):
    """A seed whose reconcile failed must not claim to be fresh.

    The stamped basis is the *source's* HEAD. If the source was discovered
    with uncommitted edits, that sha describes content our clean worktree
    does not have -- and at a shared HEAD it reads as perfectly fresh.
    The reconcile is what normally replaces it; when the reconcile fails,
    withdrawing the stamp leaves the graph basis-less, hence stale, hence
    refreshed on the next read.
    """

    def test_a_failed_reconcile_withdraws_the_borrowed_basis(self) -> None:
        worktree = self.worktree()

        with mock.patch(
            "weld.discover._discover_single_repo", side_effect=RuntimeError("boom"),
        ):
            result = ensure_seeded(worktree)

        self.assertFalse(result["reconciled"])
        self.assertIn(ALPHA_NODE, graph_nodes(worktree), "the graph still lands")
        self.assertFalse(
            (worktree / ".weld" / SIDECAR).exists(),
            "a basis we cannot vouch for must not be left on record",
        )

    def test_the_next_read_recovers_a_failed_reconcile(self) -> None:
        worktree = self.worktree()
        with mock.patch(
            "weld.discover._discover_single_repo", side_effect=RuntimeError("boom"),
        ):
            ensure_seeded(worktree)

        self.assertTrue(stale_info(worktree)["stale"])
        read(worktree)
        self.assertFalse(stale_info(worktree)["stale"])

    def test_a_source_without_a_basis_stamps_nothing(self) -> None:
        """No sidecar candidate qualifies, so nothing is seeded at all."""
        (self.origin / ".weld" / SIDECAR).unlink()
        worktree = self.worktree()

        self.assertIsNone(ensure_seeded(worktree))
        self.assertFalse((worktree / ".weld" / "graph.json").exists())


if __name__ == "__main__":
    unittest.main()
