"""Incremental == full for an outbound-anchored decorator stub (bd 5038-q4t3d).

Fifth member of the placeholder-purge equivalence family, after bd pkz2s
(external package), bd g7rs (membership), bd oao53 (unresolved sentinel) and
bd n4nvt (resolved cross-glob stub). Those four each fixed a placeholder the
purge could not *see*; this one fixes a placeholder the purge saw and read
backwards. ADR 0122 emits ``decorates`` decorator -> decorated, so a symbol
referenced only as a decorator is anchored by its OUTBOUND edges, and both
zero-inbound rules -- which accumulated ``edge["to"]`` alone -- returned it as
emptied.

**Multi-glob, deliberately.** A two-file single-glob tree does not reproduce
it. ``graph_closure._module_index`` binds a module name to whichever node id
sorts first among the nodes claiming it, and with ``dataclass`` the only
``dataclasses`` member in the tree, ``import dataclasses`` lands its
``depends_on`` on the decorator stub itself and anchors it inbound by
accident. The fixture below puts a *call* to ``dataclasses.asdict`` in a
second glob: ``asdict`` sorts first, takes the import edge, and leaves
``symbol:py:dataclasses:dataclass`` anchored by its ``decorates`` edge alone
-- which is exactly the shape this repo's own graph carries (measured:
``in=0, out=118``, all ``decorates``).

**What the round proves.** Deleting an unrelated file is enough: the purge
runs, sees no inbound edge on the decorator stub, and removes a live node.
The end state converges anyway, because the retained ``decorates`` edge then
dangles and ADR 0074's fourth amendment (bd znzu,
:mod:`weld._discover_orphan_edges`) widens the dirty set with the clean
decorated file and redoes the whole merge. So node/edge equality alone would
pass on the defect -- which is why the first case here asserts on
:func:`weld._discover_orphan_edges.orphaned_producer_files` as well. Being
right and being repaired after being wrong are different states, and only one
of them stays right when the repair pass is not there: the same predicate
also deletes nodes inside
:func:`weld.strategies.cpp_resolver.resolve_includes_pass`, mid-pass, on a
**full** discover, where no widen-and-retry exists at all.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld._discover_orphan_edges import orphaned_producer_files
from weld.discover import _discover_single_repo

#: The resolved cross-glob stub (bd n4nvt's shape) reached only as a decorator.
STUB_ID = "symbol:py:dataclasses:dataclass"

#: The unresolved sentinel (bd oao53's shape) reached only as a decorator.
SENTINEL_ID = "symbol:unresolved:property"

_DECORATED = "pkg_a/decorated.py"
_CALLER = "pkg_b/caller.py"
_SPARE = "pkg_b/spare.py"


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _write_fixture(root: Path) -> None:
    """Two globs: one decorates, the other calls a sibling stdlib member."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        '  - glob: "pkg_a/**/*.py"\n'
        "    type: symbol\n"
        "    strategy: python_callgraph\n"
        '  - glob: "pkg_b/**/*.py"\n'
        "    type: symbol\n"
        "    strategy: python_callgraph\n",
        encoding="utf-8",
    )
    (root / "pkg_a").mkdir(exist_ok=True)
    (root / _DECORATED).write_text(
        "from dataclasses import dataclass\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class Rec:\n"
        "    value: int\n"
        "\n"
        "    @property\n"
        "    def doubled(self):\n"
        "        return self.value * 2\n",
        encoding="utf-8",
    )
    (root / "pkg_b").mkdir(exist_ok=True)
    (root / _CALLER).write_text(
        "from dataclasses import asdict\n"
        "\n"
        "\n"
        "def dump(rec):\n"
        "    return asdict(rec)\n",
        encoding="utf-8",
    )
    (root / _SPARE).write_text("def spare():\n    return 0\n", encoding="utf-8")


def _node_ids(graph: dict) -> set[str]:
    return set(graph.get("nodes", {}))


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["type"], e["to"]) for e in graph.get("edges", [])}


def _strip_meta(graph: dict) -> dict:
    """Drop volatile keys, plus ``discovered_from`` -- its ORDER (not set)
    legitimately differs between the two construction paths (bd 8084).
    """
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


class FixtureShapeTest(unittest.TestCase):
    """The fixture is only evidence if the stub really is outbound-only.

    ``_module_index``'s first-writer-wins tie-break is what decides that, and
    it is not this issue's contract to hold -- so it is asserted rather than
    assumed. If a future change gives the decorator stub an inbound edge, this
    fails loudly instead of leaving the cases below quietly vacuous.
    """

    def test_the_decorator_targets_are_anchored_outbound_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="q4t3d-shape-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            graph = _discover_single_repo(root, incremental=False, write_graph=True)

        for placeholder in (STUB_ID, SENTINEL_ID):
            self.assertIn(placeholder, _node_ids(graph))
            directions = {
                (e["from"] == placeholder, e["type"])
                for e in graph["edges"]
                if placeholder in (e["from"], e["to"])
            }
            self.assertEqual(
                directions, {(True, "decorates")},
                f"{placeholder} must be named by decorates edges and nothing "
                "else, or this fixture does not exercise the reversed direction",
            )

    def test_a_full_discover_claims_no_orphan_placeholder(self) -> None:
        """The invariant :func:`weld.tests._graph_invariants.assert_no_orphan_stubs`
        derives from (bd 5038-ekohj), asserted on the producer's own output --
        a full discover runs no purge at all, so a placeholder named here is
        the predicate being wrong rather than a purge being late.
        """
        with tempfile.TemporaryDirectory(prefix="q4t3d-orphan-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            graph = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(
            emptied_placeholder_node_ids(graph["nodes"], graph["edges"]), set(),
        )


class UnrelatedDeletionEquivalenceTest(unittest.TestCase):
    """An incremental round that touches neither decorator file."""

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
            with tempfile.TemporaryDirectory(prefix="q4t3d-inc-") as td:
                root = Path(td)
                _git(root)
                _write_fixture(root)
                _commit(root)
                baseline = _discover_single_repo(
                    root, incremental=False, write_graph=True,
                )
                self.assertIn(STUB_ID, _node_ids(baseline))
                (root / _SPARE).unlink()
                _commit(root)
                incremental = _discover_single_repo(
                    root, incremental=True, write_graph=True,
                )
        finally:
            merge_module.orphaned_producer_files = original

        with tempfile.TemporaryDirectory(prefix="q4t3d-full-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            (root / _SPARE).unlink()
            _commit(root)
            full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_nodes, full_nodes = _node_ids(incremental), _node_ids(full)
        self.assertEqual(
            inc_nodes, full_nodes,
            f"incremental node set diverged from full (full-only="
            f"{sorted(full_nodes - inc_nodes)}, inc-only="
            f"{sorted(inc_nodes - full_nodes)})",
        )
        self.assertEqual(_edge_set(incremental), _edge_set(full))
        self.assertEqual(_strip_meta(incremental), _strip_meta(full))
        self.assertIn(STUB_ID, inc_nodes)
        self.assertIn(SENTINEL_ID, inc_nodes)
        self.assertTrue(observed, "the incremental path never ran the merge")
        self.assertEqual(
            observed, [set()],
            "the purge dangled an edge it then had to repair: the round "
            "converged only because ADR 0074's widen-and-retry re-parsed a "
            f"clean file, not because {STUB_ID} was left alone",
        )


class DecoratorFileDeletedEquivalenceTest(unittest.TestCase):
    """The round that SHOULD purge, so the widened anchor is not vacuous.

    Deleting the sole decorating file drops the ``decorates`` edges by
    provenance (ADR 0074), which leaves both placeholders genuinely unanchored
    -- and a fresh full discover of the same post-delete tree mints neither.
    bd oao53's and bd n4nvt's original contracts, still enforced.
    """

    def test_placeholders_go_when_their_last_decorates_edge_does(self) -> None:
        with tempfile.TemporaryDirectory(prefix="q4t3d-del-inc-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            baseline = _discover_single_repo(
                root, incremental=False, write_graph=True,
            )
            self.assertIn(SENTINEL_ID, _node_ids(baseline))
            (root / _DECORATED).unlink()
            _commit(root)
            incremental = _discover_single_repo(
                root, incremental=True, write_graph=True,
            )

        with tempfile.TemporaryDirectory(prefix="q4t3d-del-full-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            (root / _DECORATED).unlink()
            _commit(root)
            full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_nodes = _node_ids(incremental)
        self.assertEqual(inc_nodes, _node_ids(full))
        self.assertEqual(_edge_set(incremental), _edge_set(full))
        self.assertNotIn(STUB_ID, inc_nodes)
        self.assertNotIn(SENTINEL_ID, inc_nodes)


class SecondDecoratorSurvivesTest(unittest.TestCase):
    """No over-purge: two files decorate with the same stdlib name, and
    deleting one must leave the placeholder alive on the other's edge --
    matching what a full run over the same partially-emptied tree emits.
    """

    def test_one_of_two_decorating_files_deleted(self) -> None:
        second = "pkg_b/also_decorated.py"

        def _write(root: Path) -> None:
            _write_fixture(root)
            (root / second).write_text(
                "from dataclasses import dataclass\n"
                "\n"
                "\n"
                "@dataclass\n"
                "class Other:\n"
                "    name: str\n",
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory(prefix="q4t3d-two-inc-") as td:
            root = Path(td)
            _git(root)
            _write(root)
            _commit(root)
            baseline = _discover_single_repo(
                root, incremental=False, write_graph=True,
            )
            self.assertIn(STUB_ID, _node_ids(baseline))
            (root / _DECORATED).unlink()
            _commit(root)
            incremental = _discover_single_repo(
                root, incremental=True, write_graph=True,
            )

        with tempfile.TemporaryDirectory(prefix="q4t3d-two-full-") as td:
            root = Path(td)
            _git(root)
            _write(root)
            (root / _DECORATED).unlink()
            _commit(root)
            full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_nodes = _node_ids(incremental)
        self.assertEqual(inc_nodes, _node_ids(full))
        self.assertEqual(_edge_set(incremental), _edge_set(full))
        self.assertIn(STUB_ID, inc_nodes)


if __name__ == "__main__":
    unittest.main()
