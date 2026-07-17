"""Breadth-first shortest-path traversal for :class:`weld.federation.FederatedGraph`.

Split out of ``weld/federation.py`` to keep that module within the 400-line
cap. ``path_federated`` takes the federation object, mirroring the
``weld._federation_query.query_federated(federation, ...)`` and
``weld._federation_descent.descent_edges_for(federation, ...)`` seams: the
federation stays the single owner of child loading, node decoration, and root
edges, and this module only orchestrates the BFS over its ``adjacent_nodes``
fan-out.
"""

from __future__ import annotations

from collections import deque

from weld._federation_descent import descent_edges_for as _descent
from weld._sqlite_reader import SqliteBackedGraph
from weld.federation_child_loader import child_edges_for as _child_edges_for
from weld.federation_support import edge_key, prefix_node_id, split_prefixed_id
from weld.graph import Graph

__all__ = ["adjacent_nodes", "path_federated"]


def adjacent_nodes(federation, node_id: str) -> list[tuple[str, dict]]:
    """Return deterministic ``(neighbor_id, decorated_edge)`` pairs for *node_id*.

    Combines root cross edges and synthetic root->child descent edges with the
    child-internal edges reachable through a prefixed child id. Neighbors whose
    node is absent are skipped so the path never traverses a dangling edge. The
    result is keyed by ``other_id|edge_key`` and returned in sorted key order
    for a stable, iteration-order-independent BFS.
    """
    adjacent: dict[str, tuple[str, dict]] = {}

    for edge in federation._root_edges_for(node_id) + _descent(federation, node_id):
        other_id = edge["to"] if edge["from"] == node_id else edge["from"]
        if federation.get_node(other_id) is None:
            continue
        decorated = federation._decorate_edge(edge)
        adjacent.setdefault(
            f"{other_id}|{edge_key(decorated)}",
            (other_id, decorated),
        )

    parts = split_prefixed_id(node_id)
    if parts is None:
        return [adjacent[key] for key in sorted(adjacent)]

    child_name, local_id = parts
    child = federation._load_child(child_name)
    if not isinstance(child, (Graph, SqliteBackedGraph)):
        return [adjacent[key] for key in sorted(adjacent)]

    for edge in _child_edges_for(child, local_id):
        if edge["from"] == local_id:
            other_local = edge["to"]
        elif edge["to"] == local_id:
            other_local = edge["from"]
        else:
            continue
        other_id = prefix_node_id(child_name, other_local)
        if federation.get_node(other_id) is None:
            continue
        prefixed = federation._prefix_edge(child_name, edge)
        adjacent.setdefault(
            f"{other_id}|{edge_key(prefixed)}",
            (other_id, prefixed),
        )

    return [adjacent[key] for key in sorted(adjacent)]


def path_federated(federation, from_id: str, to_id: str) -> dict:
    """Return the shortest path across child graphs and root cross edges."""
    start = federation._canonicalize_node_id(from_id)
    goal = federation._canonicalize_node_id(to_id)
    if federation.get_node(start) is None or federation.get_node(goal) is None:
        return {"path": None, "reason": "node not found"}

    queue: deque[str] = deque([start])
    visited = {start}
    prev: dict[str, tuple[str, dict]] = {}

    while queue:
        current = queue.popleft()
        if current == goal:
            break
        for neighbor_id, edge in adjacent_nodes(federation, current):
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            prev[neighbor_id] = (current, edge)
            queue.append(neighbor_id)
    else:
        return {"path": None, "reason": "no path found"}

    path_ids: list[str] = [goal]
    edges: list[dict] = []
    current = goal
    while current != start:
        parent, edge = prev[current]
        path_ids.append(parent)
        edges.append(edge)
        current = parent
    path_ids.reverse()
    edges.reverse()
    nodes = [federation.get_node(node_id) for node_id in path_ids]
    return {
        "path": [node for node in nodes if node is not None],
        "edges": edges,
    }
