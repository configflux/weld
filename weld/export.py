"""Graph visualization export: Mermaid, DOT, and D2 serializers.

Each format is a pure function that takes graph data (nodes dict and edges
list) and returns a string. The ``export()`` dispatcher loads the graph,
optionally extracts a subgraph, and delegates to the requested serializer.

This module has no external dependencies beyond the weld runtime.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from weld.graph import Graph


# ---------------------------------------------------------------------------
# ID sanitization
# ---------------------------------------------------------------------------


def _safe_id(node_id: str) -> str:
    """Convert a node ID to a diagram-safe identifier.

    Replaces colons, slashes, dashes, and dots with underscores so the ID
    is valid in Mermaid, DOT, and D2.
    """
    return (
        node_id.replace(":", "_")
        .replace("/", "_")
        .replace("-", "_")
        .replace(".", "_")
    )


# ---------------------------------------------------------------------------
# Subgraph extraction
# ---------------------------------------------------------------------------


def extract_subgraph(
    graph: Graph,
    node_id: str,
    depth: int = 1,
) -> tuple[dict[str, dict], list[dict]]:
    """Extract a subgraph around *node_id* up to *depth* hops.

    Returns ``(nodes, edges)`` where *nodes* is a dict mapping node IDs to
    their data and *edges* is a list of edge dicts connecting only the
    included nodes.

    If *node_id* does not exist in the graph, returns empty collections.
    """
    data = graph.dump()
    all_nodes: dict[str, dict] = data.get("nodes", {})
    all_edges: list[dict] = data.get("edges", [])

    if node_id not in all_nodes:
        return {}, []

    # BFS to collect node IDs within depth
    visited: set[str] = {node_id}
    frontier: deque[tuple[str, int]] = deque([(node_id, 0)])

    # Build adjacency (undirected) for BFS
    adj: dict[str, list[str]] = {}
    for e in all_edges:
        adj.setdefault(e["from"], []).append(e["to"])
        adj.setdefault(e["to"], []).append(e["from"])

    while frontier:
        current, d = frontier.popleft()
        if d >= depth:
            continue
        for neighbor in adj.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append((neighbor, d + 1))

    # Collect nodes and edges
    sub_nodes = {nid: all_nodes[nid] for nid in visited if nid in all_nodes}
    sub_edges = [
        e for e in all_edges if e["from"] in visited and e["to"] in visited
    ]

    return sub_nodes, sub_edges


# ---------------------------------------------------------------------------
# Mermaid serializer
# ---------------------------------------------------------------------------


def to_mermaid(
    graph: Graph,
    *,
    nodes: dict[str, dict] | None = None,
    edges: list[dict] | None = None,
) -> str:
    """Serialize graph data to a Mermaid flowchart string.

    If *nodes* and *edges* are provided, uses those (subgraph mode).
    Otherwise serializes the full graph.
    """
    if nodes is None or edges is None:
        data = graph.dump()
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])

    lines: list[str] = ["flowchart LR"]

    # Node definitions
    for node_id, node_data in sorted(nodes.items()):
        sid = _safe_id(node_id)
        label = node_data.get("label", node_id)
        ntype = node_data.get("type", "")
        display = f"{label} ({ntype})" if ntype else label
        # Use square brackets for all nodes
        lines.append(f"    {sid}[\"{display}\"]")

    # Edge definitions
    for edge in edges:
        src = _safe_id(edge["from"])
        dst = _safe_id(edge["to"])
        etype = edge.get("type", "")
        if etype:
            lines.append(f"    {src} -->|{etype}| {dst}")
        else:
            lines.append(f"    {src} --> {dst}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# DOT serializer
# ---------------------------------------------------------------------------


def to_dot(
    graph: Graph,
    *,
    nodes: dict[str, dict] | None = None,
    edges: list[dict] | None = None,
) -> str:
    """Serialize graph data to Graphviz DOT format."""
    if nodes is None or edges is None:
        data = graph.dump()
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])

    lines: list[str] = ["digraph weld {"]
    lines.append("    rankdir=LR;")

    # Node definitions
    for node_id, node_data in sorted(nodes.items()):
        sid = _safe_id(node_id)
        label = node_data.get("label", node_id)
        ntype = node_data.get("type", "")
        display = f"{label}\\n({ntype})" if ntype else label
        lines.append(f'    {sid} [label="{display}"];')

    # Edge definitions
    for edge in edges:
        src = _safe_id(edge["from"])
        dst = _safe_id(edge["to"])
        etype = edge.get("type", "")
        if etype:
            lines.append(f'    {src} -> {dst} [label="{etype}"];')
        else:
            lines.append(f"    {src} -> {dst};")

    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# D2 serializer
# ---------------------------------------------------------------------------


def to_d2(
    graph: Graph,
    *,
    nodes: dict[str, dict] | None = None,
    edges: list[dict] | None = None,
) -> str:
    """Serialize graph data to D2 diagram format."""
    if nodes is None or edges is None:
        data = graph.dump()
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])

    lines: list[str] = []

    # Node definitions
    for node_id, node_data in sorted(nodes.items()):
        sid = _safe_id(node_id)
        label = node_data.get("label", node_id)
        ntype = node_data.get("type", "")
        display = f"{label} ({ntype})" if ntype else label
        lines.append(f"{sid}: {display}")

    # Edge definitions
    for edge in edges:
        src = _safe_id(edge["from"])
        dst = _safe_id(edge["to"])
        etype = edge.get("type", "")
        if etype:
            lines.append(f"{src} -> {dst}: {etype}")
        else:
            lines.append(f"{src} -> {dst}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Format registry and dispatcher
# ---------------------------------------------------------------------------

_FORMATS: dict[str, Any] = {
    "mermaid": to_mermaid,
    "dot": to_dot,
    "d2": to_d2,
}


def export(
    fmt: str,
    *,
    node_id: str | None = None,
    depth: int = 1,
    root: str | Path = ".",
) -> str:
    """Export the graph (or a subgraph) to the requested format.

    Parameters
    ----------
    fmt : str
        Output format: ``mermaid``, ``dot``, or ``d2``.
    node_id : str, optional
        Center node for subgraph extraction. If ``None``, exports the
        full graph.
    depth : int
        BFS depth for subgraph extraction (default 1). Ignored when
        *node_id* is ``None``.
    root : str or Path
        Project root containing ``.weld/graph.json``.

    Returns
    -------
    str
        The serialized diagram string.

    Raises
    ------
    ValueError
        If *fmt* is not a recognized format.
    """
    if fmt not in _FORMATS:
        raise ValueError(
            f"unknown export format: {fmt!r} (available: {', '.join(sorted(_FORMATS))})"
        )

    g = Graph(Path(root))
    g.load()

    serializer = _FORMATS[fmt]

    if node_id is not None:
        nodes, edges = extract_subgraph(g, node_id, depth=depth)
        return serializer(g, nodes=nodes, edges=edges)

    return serializer(g)
