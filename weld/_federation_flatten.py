"""Read-time flatten of a :class:`~weld.federation.FederatedGraph` (ADR 0089).

The whole-graph read tools -- ``trace`` (undirected interaction BFS), ``impact``
(reverse-dependency BFS), and ``communities`` -- are pure functions over a
graph's ``dump()`` / ``get_node()`` / ``query()``. At a federated root
``FederatedGraph.dump()`` deliberately returns only the root meta-graph (repo
nodes + persisted cross-repo edges) and never reads child graphs, so those tools
miss every child-internal edge -- the "cross-child reverse adjacency" gap.

:func:`flatten_federation` closes it by unioning the root meta-graph with every
present child's nodes and edges (child ids federation-prefixed, exactly as
``context`` / ``path`` / ``callers`` already prefix them) into one in-memory
:class:`~weld.graph.Graph`. That flattened graph is then handed to the existing,
unchanged engines. Nothing is persisted -- the root ``graph.json`` stays
byte-identical (ADR 0081) and ``FederatedGraph.dump()`` is untouched.

Determinism (ADR 0012): children are unioned in sorted name order and the
flattened edge list is sorted by a canonical ``(from, to, type, props)`` key, so
a given on-disk workspace yields a byte-identical flattened graph -- hence a
byte-identical trace/impact envelope.
"""

from __future__ import annotations

import json

from weld._sqlite_reader import SqliteBackedGraph
from weld.federation_support import prefix_node_id
from weld.graph import Graph

__all__ = ["flatten_federation"]


def _canonical_edge_key(edge: dict) -> tuple[str, str, str, str]:
    """Return a deterministic total-order key for *edge* (ADR 0012).

    Mirrors :func:`weld.impact_core._edge_key`: endpoints, type, then a
    sort-keyed JSON of ``props`` so two edges differing only in props order the
    same across runs.
    """
    props = json.dumps(edge.get("props", {}), sort_keys=True, ensure_ascii=True)
    return (
        str(edge.get("from", "")),
        str(edge.get("to", "")),
        str(edge.get("type", "")),
        props,
    )


def flatten_federation(fg, *, build_index: bool = True) -> Graph:
    """Return one in-memory :class:`Graph` = root meta-graph + every child.

    Child node ids are federation-prefixed (``<child>\\x1f<local>``) so the
    flattened graph shares the id space that ``FederatedGraph`` already exposes;
    the root's cross-repo edges (which already reference prefixed child ids) then
    resolve to the real child nodes added here. The flattened edge list is sorted
    canonically for determinism.

    ``build_index`` controls whether the inverted / alias index is built.
    ``trace`` needs it (term-seed resolution runs ``graph.query``); ``impact``
    and ``communities`` never query, so they pass ``build_index=False`` to skip
    the BM25 build over the union.
    """
    root_data = fg._root_graph.dump()
    nodes: dict[str, dict] = dict(root_data.get("nodes", {}))
    edges: list[dict] = list(root_data.get("edges", []))

    for name in sorted(fg._children):
        child = fg._load_child(name)
        if not isinstance(child, (Graph, SqliteBackedGraph)):
            # missing / uninitialized / corrupt children descend to nothing,
            # exactly as descent / callers fan-out already skip them.
            continue
        child_data = child.dump()
        for local_id, node in child_data.get("nodes", {}).items():
            entry = {key: value for key, value in node.items() if key != "id"}
            nodes[prefix_node_id(name, local_id)] = entry
        for edge in child_data.get("edges", []):
            edges.append(
                {
                    **edge,
                    "from": prefix_node_id(name, edge["from"]),
                    "to": prefix_node_id(name, edge["to"]),
                }
            )

    edges.sort(key=_canonical_edge_key)

    flat = Graph(fg._root)
    flat._data = {
        "meta": dict(root_data.get("meta", {})),
        "nodes": nodes,
        "edges": edges,
    }
    if build_index:
        flat._build_inverted_index()
    return flat
