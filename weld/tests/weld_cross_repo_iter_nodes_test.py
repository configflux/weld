"""Unit tests for :func:`weld.cross_repo.base._iter_nodes`.

This module pins the contract of the shared helper that replaces the
duplicated ``getattr(graph, '_data', {}).get('nodes', {})`` pattern
previously inlined in :mod:`weld.cross_repo.package_import_resolver`
(as a module-local ``_iter_graph_nodes`` helper added by bd b1k8) and
:mod:`weld.cross_repo.grpc_service_binding` (line 72).

The helper has three jobs:

1. Degrade to an empty sequence when the input lacks ``_data`` (so
   resolvers that receive stubs or partial mocks do not raise on the
   defensive access path).
2. Degrade to an empty sequence when ``_data['nodes']`` is present but
   not a dict (a list-shaped or string-shaped value is a malformed
   child graph; resolvers should produce zero edges, not crash).
3. Round-trip with the real :class:`weld.graph.Graph` -- the production
   storage shape stores nodes at ``_data['nodes']`` as a dict keyed by
   node id, with each value carrying ``{type, label, props}``. The
   helper must surface that shape verbatim as ``(node_id, node_dict)``
   tuples so resolvers can read ``node['type']`` / ``node['props']``
   without further unpacking.

These three cases are the failure modes the helper exists to absorb.
Behavior beyond them (e.g. tuple-shaped values, non-string keys) is
left undefined intentionally so callers must continue to validate node
shape inside their own loop bodies.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.contract import SCHEMA_VERSION
from weld.cross_repo.base import _iter_nodes
from weld.graph import Graph


class _StubWithoutData:
    """Graph-like object that does not expose ``_data`` at all.

    Production code constructs :class:`weld.graph.Graph` instances that
    always carry ``_data``, but resolvers can be invoked against
    third-party stubs or partial mocks during integration testing. The
    helper must degrade gracefully rather than raise ``AttributeError``
    on this shape.
    """


class _StubWithMalformedNodes:
    """Graph-like object whose ``_data['nodes']`` is the wrong type.

    A list (instead of a dict) is the most plausible malformation: it
    matches the legacy v0 storage shape some pre-rename fixtures still
    expose, and it is also the most common mistake when authoring a
    hand-rolled stub. The helper degrades to ``[]`` so resolvers
    produce zero edges instead of crashing with a TypeError.
    """

    def __init__(self, nodes_value: object) -> None:
        self._data = {"nodes": nodes_value}


class IterNodesEmptyDegradationTests(unittest.TestCase):
    """The helper returns an empty sequence on all degenerate inputs."""

    def test_object_without_data_attribute_yields_empty(self) -> None:
        self.assertEqual(list(_iter_nodes(_StubWithoutData())), [])

    def test_data_missing_nodes_key_yields_empty(self) -> None:
        class _G:
            _data: dict = {}

        self.assertEqual(list(_iter_nodes(_G())), [])

    def test_data_nodes_as_list_yields_empty(self) -> None:
        # Production reads node values via ``node.get('type')``; a list
        # would silently iterate and crash. The helper short-circuits
        # the malformed shape to ``[]`` so the resolver loop body never
        # sees it.
        self.assertEqual(list(_iter_nodes(_StubWithMalformedNodes([]))), [])
        self.assertEqual(
            list(_iter_nodes(_StubWithMalformedNodes([{"id": "x"}]))),
            [],
        )

    def test_data_nodes_as_string_yields_empty(self) -> None:
        # Strings happen to be iterable but they are not a dict; we do
        # not want the helper to surface characters as node ids.
        self.assertEqual(
            list(_iter_nodes(_StubWithMalformedNodes("not-a-dict"))),
            [],
        )

    def test_data_nodes_as_none_yields_empty(self) -> None:
        self.assertEqual(list(_iter_nodes(_StubWithMalformedNodes(None))), [])


class IterNodesDictShapeTests(unittest.TestCase):
    """The dict-shaped path returns ``(node_id, node_dict)`` tuples."""

    def test_returns_all_id_node_pairs(self) -> None:
        class _G:
            _data = {
                "nodes": {
                    "n1": {"type": "package", "label": "pkg", "props": {}},
                    "n2": {"type": "file", "label": "f", "props": {"x": 1}},
                }
            }

        pairs = dict(_iter_nodes(_G()))
        self.assertEqual(
            pairs,
            {
                "n1": {"type": "package", "label": "pkg", "props": {}},
                "n2": {"type": "file", "label": "f", "props": {"x": 1}},
            },
        )

    def test_empty_nodes_dict_yields_empty(self) -> None:
        class _G:
            _data: dict = {"nodes": {}}

        self.assertEqual(list(_iter_nodes(_G())), [])


class IterNodesRealGraphTests(unittest.TestCase):
    """Round-trip against the production :class:`weld.graph.Graph`."""

    def test_round_trip_via_graph_load(self) -> None:
        # Build an on-disk graph.json shaped the way Graph.save emits
        # (mirrors the b1k8 acceptance fixture: nodes keyed by id with
        # ``type``/``label``/``props`` per node), then load it through
        # the real Graph and feed that Graph to _iter_nodes. This is
        # the contract check that catches any regression to a shape
        # that diverges from the on-disk format.
        nodes = {
            "file:app/main": {
                "type": "file",
                "label": "main",
                "props": {"imports_from": ["lib"]},
            },
            "package:py:lib": {
                "type": "package",
                "label": "lib",
                "props": {"name": "lib"},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            (root / ".weld" / "graph.json").write_text(
                json.dumps(
                    {
                        "meta": {
                            "version": SCHEMA_VERSION,
                            "schema_version": 1,
                        },
                        "nodes": nodes,
                        "edges": [],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            graph = Graph(root)
            graph.load()
            pairs = dict(_iter_nodes(graph))

        # Every authored node id round-trips, and the value is the
        # full dict including ``props`` (which resolvers depend on).
        self.assertEqual(set(pairs), {"file:app/main", "package:py:lib"})
        self.assertEqual(
            pairs["file:app/main"]["props"]["imports_from"], ["lib"]
        )
        self.assertEqual(pairs["package:py:lib"]["props"]["name"], "lib")


if __name__ == "__main__":
    unittest.main()
