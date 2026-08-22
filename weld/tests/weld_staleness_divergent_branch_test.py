"""A seeded basis that HEAD cannot reach must settle, not oscillate.

Gate 5 stamps a graph with the *source* checkout's commit, and branches
move underneath that: a rebase rewrites the commits, a force-push and a
later prune can remove the recorded object from the repository outright.
``commits_behind`` answers ``-1`` for the unreachable case, which
:func:`weld._staleness.compute_stale_info` already reads as stale -- so
the seeded graph refreshes.

What these tests pin is the *shape* of that recovery, which is the part a
regression would break quietly: exactly one refresh, after which the
sidecar carries this worktree's own HEAD and the graph reports clean. A
basis that stayed unreachable would re-discover on every read -- the
touch/commit/touch loop ADR 0017 was written to avoid -- and a basis that
was trusted too far would serve another branch's content as fresh.

The same rule covers an ordinary in-place ``git checkout``: nothing seeds
then, but the graph is suddenly describing the wrong branch, and the next
read has to notice.
"""

from __future__ import annotations

import unittest
from unittest import mock

from weld._graph_meta_sidecar import SIDECAR_VERSION, sidecar_path_for
from weld._worktree_seed import ensure_seeded
from weld.tests._mode_a_fixture import (
    ALPHA_NODE,
    BETA_NODE,
    ModeAFixture,
    add_branch_file,
    git,
    graph_nodes,
    node_exports,
    read,
    sidecar,
    stale_info,
    wrapped_discover,
)

#: A commit that is not in this repository at all -- the state a
#: force-push plus ``git gc --prune`` leaves the seeded sidecar in.
_PRUNED_SHA = "0" * 40


class UnreachableBasisTests(ModeAFixture):
    """The recorded commit is gone; recovery must take exactly one refresh."""

    def _stamp(self, root, git_sha: str) -> None:
        """Rewrite the sidecar's basis, leaving the graph itself alone."""
        import json

        path = sidecar_path_for(root / ".weld" / "graph.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["git_sha"] = git_sha
        payload.pop("graph_mtime_ns", None)  # drop the mirror; force the real read
        payload.pop("graph_size", None)
        payload["version"] = SIDECAR_VERSION
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def test_an_unreachable_basis_reports_stale(self) -> None:
        worktree = self.worktree()
        ensure_seeded(worktree)
        self._stamp(worktree, _PRUNED_SHA)

        info = stale_info(worktree)

        self.assertEqual(info["commits_behind"], -1)
        self.assertTrue(info["stale"])

    def test_one_read_recovers_and_the_next_does_nothing(self) -> None:
        worktree = self.worktree()
        ensure_seeded(worktree)
        self._stamp(worktree, _PRUNED_SHA)

        with mock.patch(
            "weld.discover._discover_single_repo", wraps=wrapped_discover(),
        ) as discovered:
            read(worktree)
            self.assertEqual(discovered.call_count, 1, "one refresh, not a loop")

            self.assertFalse(stale_info(worktree)["stale"])
            self.assertEqual(
                sidecar(worktree)["git_sha"], git(worktree, "rev-parse", "HEAD"),
            )

            read(worktree)
            self.assertEqual(discovered.call_count, 1, "steady state is quiet")


class DivergentHistoryTests(ModeAFixture):
    """A rewritten branch is reachable history -- and still settles.

    Both halves of ADR 0017's bargain apply to a seeded graph exactly as
    they do to a discovered one: history that moved without moving any
    *content* is not a reason to re-discover, and content that moved is.
    """

    def test_a_content_free_rewrite_does_not_churn(self) -> None:
        worktree = self.worktree()
        add_branch_file(worktree)
        read(worktree)

        # A new commit object for the identical tree: the recorded basis
        # is no longer an ancestor of HEAD, but nothing it describes has
        # changed.
        git(worktree, "commit", "-q", "--amend", "-m", "rewritten", "--no-edit")

        with mock.patch(
            "weld.discover._discover_single_repo", wraps=wrapped_discover(),
        ) as discovered:
            read(worktree)

        self.assertEqual(discovered.call_count, 0, "no content moved, no rediscover")
        self.assertFalse(stale_info(worktree)["stale"])
        self.assertIn(BETA_NODE, graph_nodes(worktree))

    def test_a_rewrite_that_moves_content_converges_in_one_refresh(self) -> None:
        worktree = self.worktree()
        add_branch_file(worktree)
        read(worktree)
        self.assertEqual(node_exports(worktree, BETA_NODE), ["beta"])

        (worktree / "beta.py").write_text(
            "def beta():\n    return 2\n\n\ndef beta_two():\n    return 3\n",
            encoding="utf-8",
        )
        git(worktree, "add", "-A")
        git(worktree, "commit", "-q", "--amend", "--no-edit")

        with mock.patch(
            "weld.discover._discover_single_repo", wraps=wrapped_discover(),
        ) as discovered:
            read(worktree)
            self.assertEqual(discovered.call_count, 1)

            self.assertEqual(
                node_exports(worktree, BETA_NODE), ["beta", "beta_two"],
            )
            self.assertFalse(stale_info(worktree)["stale"])
            self.assertEqual(
                sidecar(worktree)["git_sha"], git(worktree, "rev-parse", "HEAD"),
            )

            read(worktree)
            self.assertEqual(discovered.call_count, 1, "steady state is quiet")


class InPlaceBranchSwitchTests(ModeAFixture):
    """Switching branches inside one worktree must change the answer."""

    def test_the_next_read_follows_the_checkout(self) -> None:
        worktree = self.worktree()
        add_branch_file(worktree)
        read(worktree)
        self.assertIn(BETA_NODE, graph_nodes(worktree))

        git(worktree, "checkout", "-q", "-b", "sidetrack", "HEAD~1")

        read(worktree)

        nodes = graph_nodes(worktree)
        self.assertNotIn(BETA_NODE, nodes, "the other branch's file is gone")
        self.assertIn(ALPHA_NODE, nodes)
        self.assertEqual(sidecar(worktree)["git_branch"], "sidetrack")
        self.assertFalse(stale_info(worktree)["stale"])


if __name__ == "__main__":
    unittest.main()
