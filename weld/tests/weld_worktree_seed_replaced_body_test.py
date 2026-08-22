"""Gate 5 must not land a foreign body under a vouching inventory (bd wq9i).

The sibling case to ``weld_worktree_seed_unproven_source_test``, reached from
the opposite end. There the *source* was incoherent and the worktree inherited
its divergence. Here every checkout is perfectly coherent, and the divergence
is manufactured by the copy itself.

:func:`weld._worktree_seed_copy.copy_state_files` copies only the state files
**absent** at the destination -- a file already there belongs to that checkout
and is authoritative for it. Correct on its own terms, and it means a worktree
that has discovered once and then lost only its ``graph.json`` keeps a state
that names its own graph while gate 5 lands the *source's* body beside it. The
inventory is proven, matches this tree exactly, and describes a graph nobody
has any more.

Until bd wq9i the reconcile could not see that. ``graph_published`` recorded
that a graph had been published, not which one, so the auto-detect found a
state, a parsing graph and a satisfied flag, went incremental, found nothing
dirty -- the hashes do match this tree -- and kept the foreign body, stamping
it with our HEAD.

An in-place content edit is again the only fixture that reproduces it. A file
that exists on one side and not the other carries *zero* nodes in the wrong
graph, which the ADR 0008 per-file repair already finds and re-extracts; a
file whose content merely drifted carries nodes, just the wrong ones, and is
invisible to every signal but identity.
"""

from __future__ import annotations

import unittest
from unittest import mock

from weld import discover as discover_mod
from weld.tests._mode_a_fixture import (
    ALPHA_NODE,
    ModeAFixture,
    commit_all,
    discover,
    node_exports,
    read,
)

#: What ``alpha.py`` exports in the worktree after its own rewrite. The origin
#: keeps exporting ``alpha``, so the two checkouts' graphs disagree about one
#: file that exists, at the same path, in both.
_MINE = "mine"


class ReplacedBodySeedTests(ModeAFixture):
    """A worktree that lost only its graph must not answer from a sibling's."""

    def _worktree_that_lost_its_graph(self):
        """A discovered worktree whose ``graph.json`` alone has gone missing.

        Built entirely by production paths: branch, edit, commit, discover,
        then remove the one file. The state left behind is a *healthy* one --
        written by a run that did publish the graph it describes -- which is
        what makes this case invisible to the earlier encoding.
        """
        worktree = self.worktree()
        (worktree / "alpha.py").write_text(
            f"def {_MINE}():\n    return 3\n", encoding="utf-8",
        )
        commit_all(worktree, "rewrite alpha on this branch")
        discover(worktree)

        self.assertEqual(
            node_exports(worktree, ALPHA_NODE), [_MINE],
            "fixture invariant: the worktree discovered its own content",
        )
        self.assertEqual(
            node_exports(self.origin, ALPHA_NODE), ["alpha"],
            "fixture invariant: the origin still describes the older content, "
            "so its graph is a foreign body for this worktree",
        )

        (worktree / ".weld" / "graph.json").unlink()
        self.assertTrue(
            (worktree / ".weld" / "discovery-state.json").is_file(),
            "fixture invariant: only the graph is gone -- the inventory that "
            "vouched for it survives, which is the whole setup",
        )
        return worktree

    def test_a_seeded_worktree_answers_for_its_own_content(self) -> None:
        """The read must describe this branch, not the checkout it copied from.

        Without the identity check the retained inventory is accepted as a
        delta basis for the landed body, nothing reads as dirty, and the
        origin's graph survives the reconcile wearing our HEAD.
        """
        worktree = self._worktree_that_lost_its_graph()

        read(worktree)

        self.assertEqual(
            node_exports(worktree, ALPHA_NODE), [_MINE],
            "the seed served another checkout's content: an inventory may "
            "only be the delta basis for the graph it actually published, and "
            "the body landed beside it was never that graph",
        )

    def test_the_repaired_worktree_settles(self) -> None:
        """One repairing pass, then the ordinary fast path.

        Refusing a basis is only safe because the refusal converges: the pass
        that re-derives also publishes graph and inventory together, so the
        identity matches again and the next read does no strategy work.
        """
        worktree = self._worktree_that_lost_its_graph()
        read(worktree)

        with mock.patch.object(
            discover_mod, "_run_source", wraps=discover_mod._run_source,
        ) as spy:
            read(worktree)

        self.assertEqual(
            spy.call_count, 0,
            "a worktree that keeps re-deriving in full on every read has "
            "traded a silent wrong answer for a permanent cold start",
        )
        self.assertEqual(node_exports(worktree, ALPHA_NODE), [_MINE])

    def test_the_seed_notice_still_tells_the_truth(self) -> None:
        """The stderr line is the only signal that a read wrote ``.weld/``.

        It names the branch the answer now belongs to, so the body has to
        actually belong to it -- a truthful notice over a foreign graph is
        worse than no notice at all.
        """
        worktree = self._worktree_that_lost_its_graph()

        printed = read(worktree)

        self.assertIn(
            "seeded worktree graph", printed,
            "fixture invariant: this read must be the one that seeds",
        )
        self.assertIn("reconciled to", printed)
        self.assertEqual(
            node_exports(worktree, ALPHA_NODE), [_MINE],
            "the graph must earn the basis the notice announced",
        )


if __name__ == "__main__":
    unittest.main()
