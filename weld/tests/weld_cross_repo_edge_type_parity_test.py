"""Producer parity for the cross-repo ``depends_on`` edge type (bd ``5038-4v6fm``).

Three shipped resolvers produce the same fact -- "repo A depends on repo B" --
and until this test they disagreed about how to spell it on the wire::

    weld/cross_repo/package_graph.py            cross_repo:depends_on
    weld/cross_repo/compose_topology.py         depends_on
    weld/cross_repo/package_import_resolver.py  depends_on

``weld/_federation_validate.is_well_formed_cross_repo_edge_type`` checks for the
``cross_repo:`` prefix, so the namespaced form is the convention and the other
two were the outliers. The divergence survived a green suite because plain
``depends_on`` is a legitimate *intra-repo* type in ``VALID_EDGE_TYPES``: the
un-namespaced edges validated as ordinary edges and nothing compared the
producers to each other.

**This file is the C3a exemplar** -- ``docs/testing-hygiene.md`` §
`Cross-surface parity`_, `ADR 0139 <../../docs/adrs/0139-test-quality-mechanisms.md>`_
mechanism 3. One fact, several producers, so the interesting question stopped
being "is the type right?" and became "do the producers still agree?". The
section constrains the shape because the wrong shape passes: asserting the
literal ``cross_repo:depends_on`` in three places is mechanism 1 -- a hand-built
payload wearing a parity test's name -- and stays green while all three drift
together. Both admissible shapes are used here, and neither spells the expected
value:

* **Symmetric** -- :class:`ProducerEdgeTypeParityTest` feeds ONE real on-disk
  workspace to all three real producers and asserts their emitted type sets are
  equal *to each other*. ``cross_repo:depends_on`` appears nowhere in that
  assertion, so the section's check holds: delete the expected value and the
  test still fails when the producers disagree, because there is no expected
  value to delete.
* **Oracle** -- :class:`CrossRepoEdgeTypeConventionTest` takes the convention's
  own checker as the arbiter and holds *every registered resolver* to it. This
  is the shape ``ProducerParityTest``
  (``weld/tests/graph_invariants_cannot_answer_markers_test.py:197``) uses, and
  it is registry-driven on purpose: a sixth resolver is covered without editing
  this file. :meth:`CrossRepoEdgeTypeConventionTest
  .test_the_convention_guard_catches_a_sixth_resolver_that_drifts` red-proofs
  that claim by registering a drifting resolver and requiring the guard to name
  it.

Why not extend ``weld_polyrepo_integration_test.py``: that file already asserts
``e.type.startswith("cross_repo:")`` (line 214), but over four *test-local fake*
resolvers that hard-code the namespaced type. It exercises no production
resolver, which is precisely why the real ones drifted underneath it.

The fixture is one workspace, not three, because parity is only meaningful over
a shared input -- three per-producer fixtures would let the producers disagree
about what they were even describing.

.. _Cross-surface parity: ../../docs/testing-hygiene.md
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld._federation_validate import is_well_formed_cross_repo_edge_type
from weld.cross_repo import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    get_resolver,
    register_resolver,
    resolver_names,
)
from weld.graph import Graph

#: The three resolvers that produce the cross-repo ``depends_on`` fact. Named
#: rather than derived: the point of the symmetric case is that these specific
#: producers describe one relationship, and a derived list would silently shrink
#: to one member (which compares equal to itself) if a resolver stopped
#: emitting.
_DEPENDS_ON_PRODUCERS: tuple[str, ...] = (
    "compose_topology",
    "package_graph",
    "package_import_resolver",
)

#: Child names. They are the service names, the image names, the
#: ``workspaces.yaml`` child names and the manifest-declared package producer
#: all at once, which is what lets one workspace drive all three resolvers.
_CONSUMER = "svc-api"
_PRODUCER = "svc-schema"

#: The package ``svc-schema`` produces and ``svc-api`` both declares (manifest,
#: read by ``package_graph``) and imports (graph node, read by
#: ``package_import_resolver``).
_PACKAGE = "order-schema"

_COMPOSE = f"""\
services:
  {_CONSUMER}:
    image: {_CONSUMER}
    depends_on:
      - {_PRODUCER}
  {_PRODUCER}:
    image: {_PRODUCER}
"""

_WORKSPACES_YAML = f"""\
version: 1
children:
  - name: {_CONSUMER}
    path: {_CONSUMER}
  - name: {_PRODUCER}
    path: {_PRODUCER}
cross_repo_strategies: [{", ".join(_DEPENDS_ON_PRODUCERS)}]
"""

_CONSUMER_PYPROJECT = f"""\
[project]
name = "{_CONSUMER}"
dependencies = ["{_PACKAGE}"]
"""

_PRODUCER_PYPROJECT = f"""\
[project]
name = "{_PACKAGE}"
"""

#: Consumer-side graph: a ``file`` node carrying ``imports_from`` under
#: ``props`` -- the shape ``weld/strategies/python_module.py`` and the shared
#: tree-sitter C# strategy both emit.
_CONSUMER_NODES: dict[str, dict] = {
    "file:app.py": {
        "type": "file",
        "label": "app",
        "props": {"file": "app.py", "imports_from": [_PACKAGE]},
    },
}

#: Producer-side graph: a ``package`` node whose ``props.name`` is the package
#: the consumer imports.
_PRODUCER_NODES: dict[str, dict] = {
    f"package:{_PACKAGE}": {
        "type": "package",
        "label": _PACKAGE,
        "props": {"name": _PACKAGE},
    },
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_child_graph(child_root: Path, nodes: dict[str, dict]) -> None:
    _write(
        child_root / ".weld" / "graph.json",
        json.dumps(
            {"meta": {"version": "1", "schema_version": 1},
             "nodes": nodes, "edges": []},
            indent=2,
            sort_keys=True,
        ) + "\n",
    )


def _build_workspace(root: Path) -> None:
    """Write the one workspace all producers read.

    Everything here is a file a real polyrepo root would hold: the compose file
    ``compose_topology`` parses, the ``workspaces.yaml`` and manifests
    ``package_graph`` scans off disk, and the child ``graph.json`` files
    ``package_import_resolver`` reads through a real :class:`Graph`.
    """
    _write(root / "docker-compose.yml", _COMPOSE)
    _write(root / ".weld" / "workspaces.yaml", _WORKSPACES_YAML)

    _write(root / _CONSUMER / "pyproject.toml", _CONSUMER_PYPROJECT)
    _write_child_graph(root / _CONSUMER, _CONSUMER_NODES)

    _write(root / _PRODUCER / "pyproject.toml", _PRODUCER_PYPROJECT)
    _write_child_graph(root / _PRODUCER, _PRODUCER_NODES)


def _context(root: Path, strategies: list[str]) -> ResolverContext:
    """Build a context over real, loaded child graphs.

    Real :class:`Graph` objects rather than stubs: ``package_import_resolver``
    reads nodes through ``_iter_nodes``, and a stub that happened to expose a
    different attribute would let this test pass against a resolver that finds
    nothing in production (the bd ``b1k8`` failure mode).
    """
    children: dict[str, Graph] = {}
    hashes: dict[str, str] = {}
    for name in (_CONSUMER, _PRODUCER):
        child_root = root / name
        graph = Graph(child_root)
        graph.load()
        children[name] = graph
        hashes[name] = ResolverContext.hash_bytes(
            (child_root / ".weld" / "graph.json").read_bytes()
        )
    return ResolverContext(
        workspace_root=str(root),
        cross_repo_strategies=strategies,
        children=children,
        child_hashes=hashes,
    )


def _edges_by_resolver(root: Path, names: tuple[str, ...]) -> dict[str, list[CrossRepoEdge]]:
    """Run each named resolver against the one workspace at *root*.

    Resolvers are invoked directly rather than through ``run_resolvers`` so a
    resolver that raises surfaces here as an error instead of being swallowed
    into an empty list -- ``run_resolvers`` catches per-resolver exceptions by
    design, which would turn a crash into a vacuously-passing parity check.
    """
    context = _context(root, list(names))
    return {name: get_resolver(name)().resolve(context) for name in names}


def _convention_violations(root: Path) -> list[tuple[str, str]]:
    """Return ``(resolver, edge_type)`` for every edge failing the checker.

    The arbiter is
    :func:`weld._federation_validate.is_well_formed_cross_repo_edge_type` --
    the same predicate ``validate_edge`` consults to decide whether a
    federation graph may carry the type at all. Nothing in this function
    spells a concrete edge type, so it reports drift in any direction: a
    resolver that drops the prefix, and equally one that invents a third
    spelling with no prefix at all.
    """
    names = tuple(resolver_names())
    violations: list[tuple[str, str]] = []
    for name, edges in sorted(_edges_by_resolver(root, names).items()):
        for edge in edges:
            if not is_well_formed_cross_repo_edge_type(edge.type):
                violations.append((name, edge.type))
    return violations


class _WorkspaceFixture(unittest.TestCase):
    """Shared one-workspace fixture for both parity shapes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_workspace(self.root)


class ProducerEdgeTypeParityTest(_WorkspaceFixture):
    """Symmetric shape: the producers are compared to each other, not to a literal."""

    def test_every_producer_emits_at_least_one_edge(self) -> None:
        """Vacuity guard -- empty sets compare equal, and prove nothing.

        Without this, a resolver that silently stopped matching would make the
        parity assertion below trivially true, and the file would go on
        reporting agreement about a fact nobody produced.
        """
        for name, edges in sorted(_edges_by_resolver(self.root, _DEPENDS_ON_PRODUCERS).items()):
            self.assertGreaterEqual(
                len(edges), 1,
                f"{name} produced no edges against the shared workspace, so the "
                "parity assertions below compare empty sets",
            )

    def test_the_producers_agree_on_one_edge_type(self) -> None:
        """The required parity assertion (bd ``5038-4v6fm``, R9).

        Three real producers, one real workspace, their emitted type sets
        compared against **each other**. The expected value is spelled nowhere
        in this method: there is nothing to edit to make a disagreement pass.
        """
        by_resolver = _edges_by_resolver(self.root, _DEPENDS_ON_PRODUCERS)
        types = {name: {e.type for e in edges} for name, edges in by_resolver.items()}

        distinct = {frozenset(v) for v in types.values()}
        self.assertEqual(
            len(distinct), 1,
            "producers of the cross-repo depends_on fact disagree on its edge "
            f"type: {types}",
        )

    def test_the_agreed_type_is_the_one_the_contract_admits(self) -> None:
        """Ties the agreed spelling to the reader that gates it.

        ``validate_edge`` admits a ``cross_repo:*`` type only under federation
        (``meta.schema_version == 2``); agreeing on a spelling the contract
        rejects would be parity on a broken wire format. The type under test is
        read back out of a producer rather than written here.
        """
        by_resolver = _edges_by_resolver(self.root, _DEPENDS_ON_PRODUCERS)
        agreed = {e.type for edges in by_resolver.values() for e in edges}
        self.assertEqual(len(agreed), 1, agreed)
        edge_type = agreed.pop()

        self.assertTrue(
            is_well_formed_cross_repo_edge_type(edge_type),
            f"the producers agree on {edge_type!r}, which the federation "
            "contract does not recognise as a cross-repo edge type",
        )


class CrossRepoEdgeTypeConventionTest(_WorkspaceFixture):
    """Oracle shape: every registered resolver held to the convention's checker.

    Registry-driven so the guard extends to resolvers that do not exist yet --
    the "a sixth resolver cannot reintroduce the split" half of bd
    ``5038-4v6fm``. Resolvers this fixture does not drive contribute no edges
    and so cannot fail here; the vacuity guard in
    :class:`ProducerEdgeTypeParityTest` is what keeps the three that matter
    honest.
    """

    def test_no_registered_resolver_violates_the_convention(self) -> None:
        self.assertEqual(
            _convention_violations(self.root), [],
            "a registered resolver emitted an edge type without the "
            "cross_repo: prefix the federation contract requires",
        )

    def test_the_convention_guard_catches_a_sixth_resolver_that_drifts(self) -> None:
        """Red-proof: the guard above fails when a new resolver drifts.

        Registered inside the test and torn down after, so the claim "a sixth
        resolver is covered without editing this file" is executed rather than
        asserted in a comment. The drifting spelling is deliberately the
        un-namespaced form the two outliers used before this change, which
        makes this the standing regression proof against reintroducing it.
        """
        drifted = "depends_on"

        @register_resolver("test_drifting_resolver")
        class _Drifting(CrossRepoResolver):
            name = "test_drifting_resolver"

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                return [CrossRepoEdge(
                    from_id=f"repo:{_CONSUMER}",
                    to_id=f"repo:{_PRODUCER}",
                    type=drifted,
                    props={},
                )]

        self.addCleanup(self._unregister, "test_drifting_resolver")

        self.assertIn(
            ("test_drifting_resolver", drifted),
            _convention_violations(self.root),
            "the convention guard did not notice a newly registered resolver "
            "emitting an un-namespaced edge type",
        )

    @staticmethod
    def _unregister(name: str) -> None:
        from weld.cross_repo import base as _base

        _base._REGISTRY.pop(name, None)


if __name__ == "__main__":
    unittest.main()
