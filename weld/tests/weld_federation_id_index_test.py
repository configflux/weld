"""The federated endpoint classifier and its builder (ADR 0137 ss3).

Two halves. The first is the classification itself over a hand-built index:
which of ``ok`` / ``dangling`` / ``unverifiable`` each endpoint shape earns,
including the two rulings the ADR had to make explicitly -- an unregistered
child prefix is dangling (nothing claims that name, so there is nothing to be
unsure about), and ``repo:<name>`` for a registered-but-absent child is
dangling too (the root mints ``repo:`` nodes for present children only, so its
absence is a fact rather than a gap).

The second is the builder over a real workspace on disk: every *registered*
child gets an entry, and the three unreadable states map to ``None`` with the
state recorded, because a validator that cannot tell "no such node" from
"cannot say" is back to guessing -- from both child read paths, JSON and the
ADR 0058 sqlite sidecar.
"""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._federation_ids import federation_id_index, federation_id_index_for_root
from weld._federation_validate import (
    ENDPOINT_DANGLING,
    ENDPOINT_OK,
    ENDPOINT_UNVERIFIABLE,
    FederationIdIndex,
    UNKNOWN_CHILD_STATE,
)
from weld._sqlite_reader import SqliteBackedGraph
from weld.federation import FederatedGraph
from weld.tests._federation_id_fixtures import (
    CORRUPT,
    MISSING,
    UNINITIALIZED,
    write_child,
    write_workspace_root,
)
from weld.workspace import UNIT_SEPARATOR as SEP


def _index() -> FederationIdIndex:
    """Root with one repo node; one readable child, one that cannot be read."""
    return FederationIdIndex(
        root_ids=frozenset({"repo:alpha"}),
        child_ids={"alpha": frozenset({"n1"}), "beta": None},
        child_states={"beta": MISSING},
    )


class ClassifyEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.index = _index()

    def test_root_node_id_is_ok(self) -> None:
        self.assertEqual(self.index.classify_endpoint("repo:alpha"), ENDPOINT_OK)

    def test_child_local_id_present_in_that_child_is_ok(self) -> None:
        self.assertEqual(
            self.index.classify_endpoint(f"alpha{SEP}n1"), ENDPOINT_OK
        )

    def test_child_local_id_absent_from_that_child_is_dangling(self) -> None:
        self.assertEqual(
            self.index.classify_endpoint(f"alpha{SEP}nope"), ENDPOINT_DANGLING
        )

    def test_unregistered_child_prefix_is_dangling_not_unverifiable(self) -> None:
        # ADR 0137 ss3: nothing in the workspace claims the name "gamma", so
        # there is no child whose absence could make this undecidable.
        self.assertEqual(
            self.index.classify_endpoint(f"gamma{SEP}n1"), ENDPOINT_DANGLING
        )

    def test_repo_id_for_a_registered_absent_child_is_dangling(self) -> None:
        # The root graph is always readable and mints repo: nodes for present
        # children only, so "repo:beta is not there" is a fact, not a gap --
        # even though beta itself is registered and missing.
        self.assertEqual(
            self.index.classify_endpoint("repo:beta"), ENDPOINT_DANGLING
        )

    def test_child_local_id_in_an_unreadable_child_is_unverifiable(self) -> None:
        self.assertEqual(
            self.index.classify_endpoint(f"beta{SEP}n1"), ENDPOINT_UNVERIFIABLE
        )

    def test_hybrid_child_prefixed_repo_id_is_dangling(self) -> None:
        # The shape N1 reports: a root-minted id namespaced into a child. It
        # belongs to neither id space, and no reader can resolve it.
        self.assertEqual(
            self.index.classify_endpoint(f"alpha{SEP}repo:alpha"),
            ENDPOINT_DANGLING,
        )

    def test_malformed_federation_ids_are_dangling(self) -> None:
        for value in (SEP, f"a{SEP}", f"{SEP}b", f"a{SEP}b{SEP}c"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.index.classify_endpoint(value), ENDPOINT_DANGLING
                )

    def test_non_string_endpoints_are_dangling(self) -> None:
        for value in (None, 42, ["alpha", "n1"]):
            with self.subTest(value=value):
                self.assertEqual(
                    self.index.classify_endpoint(value), ENDPOINT_DANGLING
                )

    def test_child_state_falls_back_when_unrecorded(self) -> None:
        index = FederationIdIndex(
            root_ids=frozenset(), child_ids={"beta": None},
        )
        self.assertEqual(index.child_state("beta"), UNKNOWN_CHILD_STATE)

    def test_endpoint_child_reads_the_prefix(self) -> None:
        self.assertEqual(self.index.endpoint_child(f"alpha{SEP}n1"), "alpha")
        self.assertIsNone(self.index.endpoint_child("repo:alpha"))

    def test_the_index_holds_ids_only(self) -> None:
        # ADR 0137 ss3: "The index is id-only: it reads node ids and never
        # materialises child edges." A field for anything else is how that
        # stops being true.
        self.assertEqual(
            {f.name for f in fields(FederationIdIndex)},
            {"root_ids", "child_ids", "child_states"},
        )


class BuildFromWorkspaceTest(unittest.TestCase):
    """The builder over a real root: every registered child gets an entry."""

    def _workspace(self, tmp: str) -> Path:
        root = Path(tmp)
        write_child(root, "alpha", node_ids=("n1", "n2"))
        write_child(root, "gone", state=MISSING)
        write_child(root, "fresh", state=UNINITIALIZED)
        write_child(root, "broken", state=CORRUPT)
        write_workspace_root(
            root,
            registered=("alpha", "broken", "fresh", "gone"),
            repo_nodes=("alpha",),
        )
        return root

    def test_present_child_contributes_its_node_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp)
            with FederatedGraph(root, eager_index=False) as fg:
                index = federation_id_index(fg)
            self.assertEqual(index.child_ids["alpha"], frozenset({"n1", "n2"}))
            self.assertEqual(index.root_ids, frozenset({"repo:alpha"}))

    def test_every_unreadable_state_maps_to_none_with_its_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp)
            with FederatedGraph(root, eager_index=False) as fg:
                index = federation_id_index(fg)
            for name, state in (
                ("gone", MISSING),
                ("fresh", UNINITIALIZED),
                ("broken", CORRUPT),
            ):
                with self.subTest(child=name):
                    self.assertIsNone(index.child_ids[name])
                    self.assertEqual(index.child_state(name), state)
                    self.assertEqual(
                        index.classify_endpoint(f"{name}{SEP}n1"),
                        ENDPOINT_UNVERIFIABLE,
                    )

    def test_registered_children_are_all_keyed_even_when_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp)
            with FederatedGraph(root, eager_index=False) as fg:
                index = federation_id_index(fg)
            self.assertEqual(
                sorted(index.child_ids), ["alpha", "broken", "fresh", "gone"]
            )


class SqliteBackedChildTest(unittest.TestCase):
    """A child served from its sidecar reads the same ids (ADR 0058).

    The loader hands back a ``SqliteBackedGraph`` rather than a ``Graph``
    whenever a child's ``graph.db`` is fresh, and the two are read through
    different calls. Getting the sqlite one wrong would not fail loudly -- it
    would report every endpoint in a perfectly healthy child as dangling.
    """

    def test_ids_come_back_from_the_sidecar(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha", node_ids=("n1", "n2"), sidecar=True)
            write_workspace_root(
                root, registered=("alpha",), repo_nodes=("alpha",)
            )
            with FederatedGraph(root, eager_index=False) as fg:
                self.assertIsInstance(fg._load_child("alpha"), SqliteBackedGraph)
                index = federation_id_index(fg)
        self.assertEqual(index.child_ids["alpha"], frozenset({"n1", "n2"}))
        self.assertEqual(index.classify_endpoint(f"alpha{SEP}n2"), ENDPOINT_OK)
        self.assertEqual(
            index.classify_endpoint(f"alpha{SEP}n3"), ENDPOINT_DANGLING
        )


class IndexForRootTest(unittest.TestCase):
    def test_single_repo_root_has_no_index(self) -> None:
        # No workspaces.yaml -> no child id space -> nothing to resolve into,
        # and the caller keeps the shape check that is correct there.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            self.assertIsNone(federation_id_index_for_root(root))

    def test_workspace_root_builds_an_index(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha")
            write_workspace_root(
                root, registered=("alpha",), repo_nodes=("alpha",)
            )
            index = federation_id_index_for_root(root)
            self.assertIsNotNone(index)
            self.assertEqual(index.classify_endpoint(f"alpha{SEP}n1"), ENDPOINT_OK)


if __name__ == "__main__":
    unittest.main()
