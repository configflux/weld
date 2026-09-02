"""Gap G8: npm was absent from the cross-repo producer/consumer registry.

The manifest scan read ``pyproject.toml``, ``go.mod``, ``.csproj`` and
``.proto``. ``package.json`` contributed neither a produced name nor a
consumed one, so a Node polyrepo -- a workspace root, a library repo that
publishes a package, an app repo that depends on it -- got zero
``package_graph`` edges no matter how the resolver was configured. That is the
npm instance of field-eval finding M4, and its fix lands on the per-ecosystem
producer registry ADR 0141 D2 decided (ADR 0142 D5, bd lrnx1.8).

The probe landed **red on purpose** and its marker was flipped by the fix, not
by this file (ADR 0142 D7); it is green from bd lrnx1.8 onward with the
assertion it was written with. It is grammar-independent by construction: the
whole claim is read out of two ``package.json`` files off disk, so it stays in
the fast loop whatever happens to the tree-sitter layer.

Beside it is a pass-today assurance probe on the federation itself. It is what
stops G8's probe from being red for the wrong reason: a root that never
registered its children, or child graphs that failed to build, would produce
exactly the same empty edge set as a missing ecosystem, and the probe could
not tell the two apart on its own.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._federation_endpoints import endpoint_child_name
from weld.tests._graph_invariants import assert_edges_resolve, graph_edges
from weld.tests._node_eval_corpus import (
    POLYREPO_CHILDREN,
    STOREFRONT_CHILD,
    UI_KIT_CHILD,
    UI_KIT_PACKAGE,
)
from weld.tests._node_eval_e2e_harness import NodeEvalWorkspace, nodes_of_type

#: The bd issue that owns G8's fix -- an issue-id suffix, the full ledger ids
#: being tracker-internal. The entry stays after the marker is flipped: it is
#: the ledger of who fixed G8, and the inventory guard reads it to check this
#: module against ADR 0142's own owner table.
_BD_FIXES = {"G8": "lrnx1.8"}

#: The single join this workspace's manifests declare, and nothing else:
#: ``(from child, to child, package name)``. An equality, not a membership
#: test -- a fabricated second edge is as wrong as a missing first one.
_GROUND_TRUTH_JOINS = {(STOREFRONT_CHILD, UI_KIT_CHILD, UI_KIT_PACKAGE)}

_WS: NodeEvalWorkspace | None = None
_TMP: tempfile.TemporaryDirectory | None = None


def setUpModule() -> None:
    global _WS, _TMP
    _TMP = tempfile.TemporaryDirectory()
    _WS = NodeEvalWorkspace.polyrepo(Path(_TMP.name))
    _WS.bootstrap_federated()


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def workspace() -> NodeEvalWorkspace:
    assert _WS is not None, "setUpModule did not run"
    return _WS


class PackageGraphProbes(unittest.TestCase):
    ws: NodeEvalWorkspace

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()

    def _joins(self) -> set[tuple[str, str, str]]:
        """``{(from child, to child, package)}`` for every cross-repo edge.

        Endpoints are read child-name-first through the helper that knows both
        id shapes (ADR 0137 s1): the question is *which repos got joined*, and
        reading it through an id convention would make the probe fail on a
        spelling rather than on the join it exists to pin.
        """
        return {
            (
                str(endpoint_child_name(str(edge.get("from")))),
                str(endpoint_child_name(str(edge.get("to")))),
                str((edge.get("props") or {}).get("package")),
            )
            for edge in graph_edges(self.ws.graph())
        }

    def test_g8_npm_manifests_join_the_package_graph(self) -> None:
        """A declared npm dependency on a sibling repo's package is an edge.

        Recall against ground truth: the edge set the manifests on disk
        declare, asserted as an equality. "The edges that formed resolve" is
        true of a graph missing every npm producer, and was -- which is how
        the same shape of finding reached us from the field with .NET.

        Marker flipped by ``_BD_FIXES["G8"]``: ``package.json`` joined the
        manifest registry (``weld.cross_repo._manifest_readers``), publishing
        under its ``name`` and consuming its runtime ``dependencies``. The
        assertion is unchanged -- what changed is the resolver underneath it.
        """
        root = self.ws.graph()
        assert_edges_resolve(
            root, {name: self.ws.graph(rel) for name, rel in POLYREPO_CHILDREN}
        )
        self.assertEqual(
            self._joins(), _GROUND_TRUTH_JOINS,
            "the package_graph resolver's edge set is not the one the "
            "package.json manifests declare",
        )

    # -- pass-today assurance ---------------------------------------------

    def test_the_federation_root_registers_both_children(self) -> None:
        """The root federates two answerable children with the resolver wired.

        Green today, and it is what makes the probe above readable: an empty
        cross-repo edge set means "npm is not in the registry" only if the
        workspace was actually federated. Without this, a root that lost its
        children would reproduce G8's symptom exactly and the corpus would
        report a fixed gap as still broken -- or, worse, the reverse.
        """
        config = (self.ws.root / ".weld" / "workspaces.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "package_graph", config,
            f"the resolver G8 is about is not wired:\n{config}",
        )

        repos = {
            str((node.get("props") or {}).get("path"))
            for node in nodes_of_type(self.ws.graph(), "repo").values()
        }
        self.assertEqual(
            repos, {rel for _name, rel in POLYREPO_CHILDREN},
            "the root graph does not hold one repo node per registered child",
        )
        for _name, rel in POLYREPO_CHILDREN:
            self.assertTrue(
                (self.ws.root / rel / ".weld" / "graph.json").is_file(),
                f"{rel} has no graph of its own to be joined from",
            )


if __name__ == "__main__":
    unittest.main()
