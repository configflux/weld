"""Gate 5 must not inherit a source's graph/inventory divergence (bd nwyq).

:mod:`weld._worktree_seed_mode_a` proves the graph, sidecar and state it
copies came from **one generation** of the source, by re-stating every watched
file after the copy. That proof is about simultaneity, and it holds. What it
cannot establish is whether that one generation was itself coherent: a
checkout whose last discovery run resolved files without publishing a graph
holds an inventory ahead of its own ``graph.json``, and copying both at the
same instant reproduces the divergence rather than detecting it.

The reconcile was then asked to settle it, and could not. It re-derives with
``incremental=None``, and until bd nwyq the auto-detect trusted any state file
that existed: the borrowed hashes already matched the worktree's tree, so no
file was dirty, the seeded body survived untouched, and the pass stamped it
with *our* HEAD and branch. The read that followed named a basis it did not
have -- ``reconciled to <branch>@<HEAD>`` over content from an older commit --
which is the single outcome ADR 0096 gate 5 exists to prevent.

The fix belongs in the auto-detect, not here: an inventory that cannot vouch
for the graph beside it is not a delta basis, wherever it came from. These
tests pin the consequence at the surface a developer actually meets, and the
second one pins the price -- a healthy seed must still reconcile incrementally,
because paying a cold full discover on every fresh worktree would cost more
than the bug did.
"""

from __future__ import annotations

import unittest
from unittest import mock

from weld import discover as discover_mod
from weld.tests._mode_a_fixture import (
    ALPHA_NODE,
    ModeAFixture,
    commit_all,
    node_exports,
    read,
)

#: What ``alpha.py`` exports after the in-place rewrite. An edit rather than an
#: added file on purpose: a new file would be caught by the ADR 0008 per-file
#: repair, so only a content drift reproduces the silent case.
_REWRITTEN = "rewritten"


class UnprovenSourceSeedTests(ModeAFixture):
    """A worktree answers for its own HEAD even from an incoherent source."""

    def _desync_the_source(self) -> None:
        """Advance the origin's inventory past the graph it still serves.

        The production producer, not a hand-written file: rewrite the tracked
        source, commit, then run discovery in the library shape
        (``write_graph=False``). That records the new content hashes while
        ``.weld/graph.json`` stays at the old body, and marks the state
        unproven -- the shape ADR 0101 names and the shape the checkout in bd
        nwyq was found in.
        """
        (self.origin / "alpha.py").write_text(
            f"def {_REWRITTEN}():\n    return 2\n", encoding="utf-8",
        )
        commit_all(self.origin, "rewrite alpha in place")
        discover_mod._discover_single_repo(
            self.origin, incremental=None, write_graph=False,
        )

        self.assertEqual(
            node_exports(self.origin, ALPHA_NODE), ["alpha"],
            "fixture invariant: the source must still serve the pre-rewrite "
            "body, which is what makes it an unusable seed",
        )

    def test_a_worktree_seeded_from_an_incoherent_source_answers_for_head(
        self,
    ) -> None:
        """The first read must describe the worktree's own commit.

        Without the fix the reconcile finds no dirty file -- the borrowed
        hashes already match this tree -- keeps the seeded body, and stamps it
        as ours.
        """
        self._desync_the_source()
        worktree = self.worktree()

        read(worktree)

        self.assertEqual(
            node_exports(worktree, ALPHA_NODE), [_REWRITTEN],
            "the seeded graph was reconciled in name only: it still describes "
            "content the source had already replaced, while the sidecar "
            "claims this worktree's HEAD",
        )

    def test_the_reconcile_still_claims_only_what_it_delivered(self) -> None:
        """The stderr notice and the served body must agree.

        The notice is the user's only signal that a read wrote ``.weld/``, so
        a truthful one is load-bearing: naming a branch and sha the body does
        not describe is worse than staying quiet.
        """
        self._desync_the_source()
        worktree = self.worktree()

        printed = read(worktree)

        self.assertIn(
            "seeded worktree graph", printed,
            "fixture invariant: this read must be the one that seeds",
        )
        self.assertIn(
            "reconciled to", printed,
            "a seed that reconciled successfully still says so",
        )
        self.assertEqual(
            node_exports(worktree, ALPHA_NODE), [_REWRITTEN],
            "the graph must earn the basis the notice announced",
        )

    def test_a_coherent_source_still_seeds_without_re_running_strategies(
        self,
    ) -> None:
        """The price check: a healthy seed keeps its incremental reconcile.

        The origin here published graph and inventory together, and the fresh
        worktree holds identical content, so the reconcile has nothing to
        re-extract. If this ever starts invoking strategies, gate 5 has
        degraded into the cold full discover it was built to avoid.
        """
        worktree = self.worktree()

        with mock.patch.object(
            discover_mod, "_run_source", wraps=discover_mod._run_source,
        ) as spy:
            read(worktree)

        self.assertEqual(
            spy.call_count, 0,
            "seeding a worktree from a coherent source must reconcile "
            "incrementally: re-running every strategy would spend the whole "
            "saving gate 5 exists for",
        )
        self.assertEqual(
            node_exports(worktree, ALPHA_NODE), ["alpha"],
            "the incremental reconcile must still describe this worktree",
        )


if __name__ == "__main__":
    unittest.main()
