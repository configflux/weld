"""Integration test: ``package_import_resolver`` against real ``Graph.load()``.

This is the mandatory acceptance test for the bug where the resolver's
``getattr(graph, 'nodes', None)`` access pattern returns ``None`` against
the production :class:`weld.graph.Graph` shape (the real Graph stores
nodes at ``_data['nodes']`` as a dict keyed by node id, with each value
shaped ``{type, label, props}``; ``name`` and ``imports_from`` live under
``props``, not at the top level).

The pre-existing ``_G`` test doubles in
:mod:`weld_package_import_resolver_test` and
:mod:`weld_package_import_resolver_csharp_test` fake a ``.nodes``
property returning a flat list of dicts with top-level fields. That
shape is fictional: it never matches what discovery actually writes, so
those tests pass while the real federated-discover path emits zero
edges.

This module pins the contract against on-disk ``graph.json`` files
round-tripped through :meth:`Graph.load`. If the resolver regresses to
the synthetic shape (or to any access path that does not also work on
the real Graph), this test fails first.

The acceptance bar from bd b1k8 is "at least one ``depends_on`` edge",
matching the production symptom we are fixing.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.contract import SCHEMA_VERSION
from weld.cross_repo.base import ResolverContext, run_resolvers
from weld.graph import Graph

# Registering the resolver requires importing the cross_repo package so
# its __init__'s side-effect imports run. Without this the strategy name
# is unknown to ``run_resolvers``.
import weld.cross_repo  # noqa: F401

SEP = "\x1f"


def _write_child_graph_file(root: Path, nodes: dict[str, dict]) -> None:
    """Write a v1 child ``graph.json`` under ``<root>/.weld/graph.json``.

    Mirrors the on-disk shape that ``Graph.save`` emits: ``meta`` carries
    ``version`` + ``schema_version``, ``nodes`` is a dict keyed by id,
    each value carries ``type``/``label``/``props``. This is the only
    fixture surface the test trusts -- the rest of the path runs through
    real :meth:`Graph.load`, real :class:`ResolverContext`, and real
    :func:`run_resolvers`.
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
        "nodes": nodes,
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_child(root: Path) -> tuple[Graph, bytes]:
    """Return ``(Graph, raw_bytes)`` for the child's on-disk graph.

    The bytes feed :meth:`ResolverContext.hash_bytes` so we exercise the
    same TOCTOU pathway the production federation path uses.
    """
    graph = Graph(root)
    graph.load()
    raw = (root / ".weld" / "graph.json").read_bytes()
    return graph, raw


def _ctx_from_real_graphs(
    children: dict[str, tuple[Graph, bytes]],
) -> ResolverContext:
    loaded = {n: g for n, (g, _) in children.items()}
    hashes = {
        n: ResolverContext.hash_bytes(r) for n, (_, r) in children.items()
    }
    return ResolverContext(
        workspace_root="/tmp/ws",
        cross_repo_strategies=["package_import_resolver"],
        children=loaded,
        child_hashes=hashes,
    )


class RealGraphLoadEmitsAtLeastOneEdgeTest(unittest.TestCase):
    """Real ``Graph.load()`` -> resolver -> at least one ``depends_on`` edge.

    This is the dispositive regression test for bd b1k8. The pre-fix
    resolver read ``getattr(graph, 'nodes', None)`` against the real
    :class:`Graph`, which returns ``None`` (no such attribute exists), so
    the iteration bailed out before any matching could happen. Two
    independent shape bugs followed: nodes are dict-keyed not list-shaped,
    and ``name``/``imports_from`` live under ``props``, not the top
    level. All three failure modes converge on the same observable: zero
    edges. We therefore assert "at least one edge", which is the same
    floor the production symptom violates.
    """

    def test_csharp_shaped_polyrepo_emits_at_least_one_depends_on_edge(self) -> None:
        # Build two on-disk child graphs that mirror what a real C#
        # tree-sitter discovery emits: file nodes (type='file') with
        # imports_from under props, and package nodes with name under
        # props. The shapes match what bd b1k8's Tier-1 corpus produces.
        consumer_nodes = {
            "file:ShareX.HelpersLib/Colors/GradientInfo": {
                "type": "file",
                "label": "GradientInfo",
                "props": {
                    "file": "ShareX.HelpersLib/Colors/GradientInfo.cs",
                    "imports_from": ["Newtonsoft.Json", "System"],
                    "language": "csharp",
                    "source_strategy": "tree_sitter",
                },
            },
        }
        producer_nodes = {
            "package:csharp:newtonsoft.json": {
                "type": "package",
                "label": "Newtonsoft.Json",
                "props": {
                    "name": "Newtonsoft.Json",
                    "language": "csharp",
                    "origin": "external",
                    "source_strategy": "tree_sitter",
                },
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sharex").mkdir()
            (root / "newtonsoft").mkdir()
            _write_child_graph_file(root / "sharex", consumer_nodes)
            _write_child_graph_file(root / "newtonsoft", producer_nodes)
            children = {
                "sharex": _load_child(root / "sharex"),
                "newtonsoft": _load_child(root / "newtonsoft"),
            }
            edges = run_resolvers(_ctx_from_real_graphs(children))

        # Acceptance bar from bd b1k8: at least one edge.
        self.assertGreaterEqual(
            len(edges),
            1,
            "package_import_resolver must emit >=1 depends_on edge for a "
            "matching consumer/producer pair loaded via Graph.load(). "
            "Zero edges means the resolver is reading the wrong shape "
            "and the bug is back.",
        )

    def test_real_graph_edge_uses_canonical_federation_id_format(self) -> None:
        # Once we have at least one edge, pin the canonical id shape so
        # the resolver does not regress on the namespacing contract.
        consumer_nodes = {
            "file:app/main": {
                "type": "file",
                "label": "main",
                "props": {
                    "imports_from": ["shared_utils"],
                    "source_strategy": "python_module",
                },
            },
        }
        producer_nodes = {
            "package:py:shared_utils": {
                "type": "package",
                "label": "shared_utils",
                "props": {"name": "shared_utils"},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "lib").mkdir()
            _write_child_graph_file(root / "app", consumer_nodes)
            _write_child_graph_file(root / "lib", producer_nodes)
            children = {
                "app": _load_child(root / "app"),
                "lib": _load_child(root / "lib"),
            }
            edges = run_resolvers(_ctx_from_real_graphs(children))

        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge.type, "depends_on")
        self.assertEqual(edge.from_id, f"app{SEP}file:app/main")
        self.assertEqual(edge.to_id, f"lib{SEP}package:py:shared_utils")
        self.assertEqual(edge.props["import_name"], "shared_utils")
        self.assertEqual(edge.props["source_child"], "app")

    def test_real_graph_intra_repo_match_yields_no_edge(self) -> None:
        # Self-edges must still be excluded even with the real Graph
        # shape: when consumer and producer live in the same child, no
        # cross-repo edge is emitted.
        nodes = {
            "file:Caller": {
                "type": "file",
                "label": "Caller",
                "props": {"imports_from": ["Foo.Bar"]},
            },
            "package:csharp:foo.bar": {
                "type": "package",
                "label": "Foo.Bar",
                "props": {"name": "Foo.Bar"},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "solo").mkdir()
            _write_child_graph_file(root / "solo", nodes)
            children = {"solo": _load_child(root / "solo")}
            edges = run_resolvers(_ctx_from_real_graphs(children))

        self.assertEqual(edges, [])


if __name__ == "__main__":
    unittest.main()
