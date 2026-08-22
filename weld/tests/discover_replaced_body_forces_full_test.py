"""A graph body replaced under a vouching inventory (bd wq9i).

The sibling failure to ``discover_unproven_state_forces_full_test``, reached
from the opposite end. There the *writing run* was at fault: it resolved files
without publishing a graph, leaving an inventory ahead of its own body. Here
the writing run is blameless -- it published the graph it describes, and the
inventory still matches the tree exactly -- and the body a reader loads is
nonetheless not the one that inventory was built beside, because something
replaced it afterwards.

ADR 0101's second amendment recorded "this run published a graph" as a
boolean, which states the writer's half and nothing about the body now on
disk. Both situations therefore looked identical to it, and this one was
accepted: the auto-detect found a state, a parsing graph and a satisfied flag,
went incremental, found nothing dirty, and served the foreign body stamped
with our own basis.

ADR 0096 gate 5 reaches this with no hand-editing at all. Its copy
deliberately keeps a state file already present at the destination, so a
worktree that has discovered once and then lost only its ``graph.json`` gets a
sibling's body landed beside a state that vouches for its own. The end-to-end
git reproduction is ``weld_worktree_seed_replaced_body_test``; what is pinned
here is the layer that has to refuse -- the incremental basis decision.

The fixture rewrites its source **in place**, as its sibling does and for the
same reason: a file whose content drifted still carries nodes, just the wrong
ones, so it is invisible to the hash diff (the inventory already matches the
tree) and to the ADR 0008 per-file repair (which finds files carrying *zero*
nodes) alike.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weld import discover as discover_mod
from weld.discover import _discover_single_repo
from weld.discovery_state import build_file_hashes, load_state
from weld.tests._unproven_state_lib import (
    MOD as _MOD,
    NEW as _NEW,
    build_fixture as _build_fixture,
    exports as _exports,
    on_disk as _on_disk,
    stamp_a_later_mtime as _stamp_a_later_mtime,
    vouches as _vouches,
)


class ReplacedGraphBodyTests(unittest.TestCase):
    """A body swapped in under a vouching inventory (bd wq9i).

    The other producer of the same divergence, and the one the boolean could
    not see. Here the writing run *did* publish a graph and the inventory
    *does* match the tree -- both halves the earlier encoding could state --
    and the body a reader loads is still not the one the inventory describes,
    because something replaced it afterwards. ADR 0096 gate 5 reaches this
    without any hand-editing: its copy deliberately keeps a state file already
    present at the destination, so a worktree that lost only its ``graph.json``
    has a sibling's body landed beside a state that vouches for its own.

    Reproduced here at the module boundary -- the auto-detect is what has to
    refuse -- with the end-to-end git version in
    ``weld_worktree_seed_replaced_body_test``.
    """

    def _root_with_a_foreign_body(self) -> Path:
        """A root whose state published one graph and now holds another."""
        td = tempfile.TemporaryDirectory(prefix="replaced-body-")
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        _build_fixture(root)
        _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertTrue(
            _vouches(load_state(root), root),
            "fixture invariant: this run published the graph it describes",
        )

        # The foreign body: the same tree discovered at different content, so
        # it is a real graph of a real repository -- just not of this one.
        donor = Path(td.name) / "donor"
        donor.mkdir()
        _build_fixture(donor)
        (donor / _MOD).write_text(_NEW, encoding="utf-8")
        _discover_single_repo(donor, incremental=False, write_graph=True)
        (root / ".weld" / "graph.json").write_bytes(
            (donor / ".weld" / "graph.json").read_bytes(),
        )

        self.assertEqual(
            _exports(_on_disk(root)), ["replacement"],
            "fixture invariant: the body on disk describes the donor",
        )
        self.assertEqual(
            load_state(root).files[_MOD], build_file_hashes(root, [_MOD])[_MOD],
            "fixture invariant: the inventory still matches this tree, so the "
            "hash diff reports nothing -- this is what hid the foreign body",
        )
        return root

    def test_a_replaced_body_is_not_a_delta_basis(self) -> None:
        """The served graph must describe this tree, not the landed one.

        Without the identity check the auto-detect sees a state, a parsing
        graph and a published flag, goes incremental, finds nothing dirty, and
        returns the donor's body stamped as ours.
        """
        root = self._root_with_a_foreign_body()

        graph = _discover_single_repo(root, incremental=None, write_graph=True)

        self.assertEqual(
            _exports(graph), ["helper"],
            "discovery served content this tree does not hold: an inventory "
            "may only be the delta basis for the graph it actually published",
        )
        self.assertEqual(
            _exports(_on_disk(root)), ["helper"],
            "the re-derived graph must also land, or the next read re-serves "
            "the foreign body",
        )

    def test_the_repair_converges_after_one_pass(self) -> None:
        """One full pass, then the fast path returns.

        The same asymmetry the second amendment relies on: refusing a basis is
        safe only because the refusal settles. The repairing run publishes
        graph and inventory together, so the identity matches again.
        """
        root = self._root_with_a_foreign_body()

        _discover_single_repo(root, incremental=None, write_graph=True)

        self.assertTrue(
            _vouches(load_state(root), root),
            "the repairing run must leave the inventory naming the body it "
            "just published",
        )

        with mock.patch.object(
            discover_mod, "_run_source", wraps=discover_mod._run_source,
        ) as spy:
            _discover_single_repo(root, incremental=None, write_graph=True)

        self.assertEqual(
            spy.call_count, 0,
            "a root that keeps re-deriving in full on every read has traded a "
            "silent wrong answer for a permanent cold start",
        )

    def test_an_identical_body_at_a_new_inode_still_vouches(self) -> None:
        """The price check: identity is content, not the file it arrived in.

        Gate 5 lands a sibling's graph bytes verbatim, so the seeded inventory
        describes the landed body exactly -- at a fresh inode, hence a fresh
        mtime. Refusing that would make every worktree seed pay the cold full
        discover the seed exists to avoid.
        """
        with tempfile.TemporaryDirectory(prefix="same-bytes-") as td:
            root = Path(td)
            _build_fixture(root)
            _discover_single_repo(root, incremental=False, write_graph=True)

            graph_path = root / ".weld" / "graph.json"
            body = graph_path.read_bytes()
            graph_path.unlink()
            graph_path.write_bytes(body)
            _stamp_a_later_mtime(graph_path)

            self.assertTrue(
                _vouches(load_state(root), root),
                "the same bytes at a new inode are the same graph",
            )

            with mock.patch.object(
                discover_mod, "_run_source", wraps=discover_mod._run_source,
            ) as spy:
                _discover_single_repo(root, incremental=None, write_graph=True)

            self.assertEqual(
                spy.call_count, 0,
                "a re-landed identical body must keep the incremental basis",
            )


if __name__ == "__main__":
    unittest.main()
