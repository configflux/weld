"""Incremental == full when a stub's last anchor is a closure edge (bd 5038-rwi34).

Sixth member of the placeholder-purge equivalence family, after bd pkz2s
(external package), bd g7rs (membership), bd oao53 (unresolved sentinel),
bd n4nvt (resolved cross-glob stub) and bd 5038-q4t3d (outbound ``decorates``
anchor). The first four fixed a placeholder the purge could not *see*; q4t3d
fixed one it read backwards. This one fixes a placeholder it saw, read
correctly, and was told about by an edge that only existed because the
placeholder did.

**What the round measures.** ``python_callgraph``, walking ``beta/use.py``'s
``from alpha import fn_alpha``, mints the never-walked resolved-target stub
``symbol:py:alpha:fn_alpha``. That stub carries ``props.module == "alpha"``,
and no ``file:`` node claims that module name -- the canonical Python trio
leaves ``__init__.py`` unanchored, so ``alpha/__init__.py`` has no node at all
-- so ``graph_closure._module_index`` binds ``alpha`` to the stub and
``_link_imports`` hands the *clean* file ``beta/sub/use.py`` a
``graph_closure``-authored ``depends_on`` onto it. Delete ``beta/use.py`` and
the strategy-authored ``calls`` edge goes with it, but that closure edge
survives, names the stub, and kept it anchored -- after which ``close_graph``
re-derived the very same edge off the very same index entry. Stub and edge
held each other up; a full discover of the post-delete tree mints neither and
resolves the import externally to ``package:python:alpha``.

**Not the re-export retarget**, despite the shape reading like one.
:mod:`weld._graph_closure_reexport` finds a facade by looking its module name
up in the *path* index, and ``alpha/__init__.py`` has no file node to find, so
that walk never fires here at all -- :class:`FixtureShapeTest` asserts it
outright rather than leaving it assumed. The mirror round where the retarget
*is* load-bearing (facade deleted, definition surviving) is
``incremental_reexport_equivalence_test`` and stays where it is.

**Why the orphan assertion.** Node/edge equality alone would also pass on the
defect once ADR 0074's fourth amendment (bd znzu,
:mod:`weld._discover_orphan_edges`) had widened the dirty set and repaired it,
so the first case asserts ``orphaned_producer_files() == set()`` as well --
the same reason ``incremental_decorator_anchor_equivalence_test`` does, and
for the same stakes.

The unit half over the predicate itself is
``discovery_state_closure_anchor_test``; both read the one cast in
:mod:`weld.tests._closure_anchor_fixture`.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld._discover_orphan_edges import orphaned_producer_files
from weld.discover import _discover_single_repo
from weld.tests._closure_anchor_fixture import (
    CLEAN_CONSUMER,
    PACKAGE_ID,
    SOLE_IMPORTER,
    SOLE_IMPORTER_SYMBOL,
    STUB_ID,
    commit,
    edge_set,
    git_init,
    node_ids,
    strip_meta,
    write,
)


def _discover_intact(prefix: str, *, second_importer: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix=prefix) as td:
        root = Path(td)
        git_init(root)
        write(root, second_importer=second_importer)
        commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


def _discover_after_delete(prefix: str, *, second_importer: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix=prefix) as td:
        root = Path(td)
        git_init(root)
        write(root, second_importer=second_importer)
        (root / SOLE_IMPORTER).unlink()
        commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class FixtureShapeTest(unittest.TestCase):
    """The fixture is only evidence if it really carries the measured shape.

    Every claim the module docstring makes about *why* the stub survived is
    asserted here, so a future change that quietly moves one of them fails
    loudly instead of leaving the equivalence cases vacuous.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = _discover_intact("rwi34-shape-")

    def test_the_facade_has_no_file_node_so_the_retarget_cannot_fire(self) -> None:
        """The re-export walk finds a facade in the path index or not at all.

        ``python_module`` leaves ``__init__.py`` unanchored, so there is no
        node for the facade and :mod:`weld._graph_closure_reexport` never runs
        on this tree -- which is what makes this a placeholder-anchor round
        rather than a second copy of the retarget family.
        """
        self.assertNotIn("file:alpha/__init__", node_ids(self.graph))
        self.assertIn(
            (SOLE_IMPORTER_SYMBOL, "calls", STUB_ID), edge_set(self.graph),
            "the call must still be ON the stub; a retargeted call would mean "
            "this fixture exercises the re-export walk instead",
        )

    def test_the_clean_consumer_depends_on_the_stub_via_the_closure(self) -> None:
        """The anchor at issue: a closure edge from a file that never named it.

        ``beta/sub/use.py`` imports ``alpha`` and says nothing about
        ``fn_alpha``; the edge lands on the stub only because the module index
        had nowhere else to bind the name.
        """
        anchoring = [
            e for e in self.graph["edges"]
            if e["to"] == STUB_ID and e["from"] == CLEAN_CONSUMER
        ]
        self.assertEqual(len(anchoring), 1)
        self.assertEqual(anchoring[0]["type"], "depends_on")
        props = anchoring[0]["props"]
        self.assertEqual(props["source_strategy"], "graph_closure")
        self.assertEqual(props["normalized_import"], "alpha")

    def test_a_full_discover_claims_no_orphan_placeholder(self) -> None:
        """The producer's own output, judged by the purge's own predicate.

        A full discover runs no purge at all, so a placeholder named here
        would be the predicate being wrong rather than a purge being late
        (bd 5038-ekohj's invariant, asserted at its source).
        """
        self.assertEqual(
            emptied_placeholder_node_ids(self.graph["nodes"], self.graph["edges"]),
            set(),
        )


class SoleImporterDeletedEquivalenceTest(unittest.TestCase):
    """Delete the only file that ever named the stub. Seed 369's round."""

    def test_incremental_matches_full_without_needing_the_repair_pass(
        self,
    ) -> None:
        observed: list[set[str]] = []
        original = orphaned_producer_files

        def watched(nodes, edges):
            result = original(nodes, edges)
            observed.append(set(result))
            return result

        import weld._discover_incremental_merge as merge_module

        merge_module.orphaned_producer_files = watched
        try:
            with tempfile.TemporaryDirectory(prefix="rwi34-inc-") as td:
                root = Path(td)
                git_init(root)
                write(root)
                commit(root)
                baseline = _discover_single_repo(
                    root, incremental=False, write_graph=True,
                )
                self.assertIn(STUB_ID, node_ids(baseline))
                (root / SOLE_IMPORTER).unlink()
                commit(root)
                incremental = _discover_single_repo(
                    root, incremental=True, write_graph=True,
                )
        finally:
            merge_module.orphaned_producer_files = original

        full = _discover_after_delete("rwi34-full-")

        inc_nodes, full_nodes = node_ids(incremental), node_ids(full)
        self.assertEqual(
            inc_nodes, full_nodes,
            f"incremental node set diverged from full (full-only="
            f"{sorted(full_nodes - inc_nodes)}, inc-only="
            f"{sorted(inc_nodes - full_nodes)})",
        )
        self.assertEqual(edge_set(incremental), edge_set(full))
        self.assertEqual(strip_meta(incremental), strip_meta(full))
        self.assertTrue(observed, "the incremental path never ran the merge")
        self.assertEqual(
            observed, [set()],
            "the purge dangled an edge it then had to repair: the round "
            "converged only because ADR 0074's widen-and-retry re-parsed a "
            f"clean file, not because {STUB_ID} was resolved correctly",
        )

    def test_the_stranded_stub_goes_and_the_import_lands_on_the_package(
        self,
    ) -> None:
        """Both paths agree, and they agree on what a full run produces.

        Equality alone is satisfied by both paths keeping the stub, which is
        the failure this member exists to catch -- so the end state is named
        outright rather than left to the comparison.
        """
        full = _discover_after_delete("rwi34-shape-after-")
        self.assertNotIn(STUB_ID, node_ids(full))
        self.assertIn((CLEAN_CONSUMER, "depends_on", PACKAGE_ID), edge_set(full))


class SecondImporterSurvivesTest(unittest.TestCase):
    """No over-purge: a surviving strategy-authored edge still anchors.

    The whole change is that a ``graph_closure`` edge stops counting as an
    anchor. Had it removed the anchor test altogether, this round would purge
    a stub a full discover of the same tree still mints -- so the fixture
    keeps a second minter in the same glob and deletes only the first.
    """

    def test_one_of_two_importers_deleted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwi34-two-inc-") as td:
            root = Path(td)
            git_init(root)
            write(root, second_importer=True)
            commit(root)
            baseline = _discover_single_repo(
                root, incremental=False, write_graph=True,
            )
            self.assertIn(STUB_ID, node_ids(baseline))
            (root / SOLE_IMPORTER).unlink()
            commit(root)
            incremental = _discover_single_repo(
                root, incremental=True, write_graph=True,
            )

        full = _discover_after_delete("rwi34-two-full-", second_importer=True)

        inc_nodes = node_ids(incremental)
        self.assertEqual(inc_nodes, node_ids(full))
        self.assertEqual(edge_set(incremental), edge_set(full))
        self.assertIn(STUB_ID, inc_nodes)


if __name__ == "__main__":
    unittest.main()
