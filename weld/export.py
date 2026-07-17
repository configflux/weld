"""Graph visualization export: Mermaid, DOT, and D2 serializers.

Each format is a pure function that takes graph data (nodes dict and edges
list) and returns a string. The ``export()`` dispatcher loads the graph,
optionally extracts a subgraph, and delegates to the requested serializer.

Per ADR 0053 the dispatcher also supports the multi-file ``wiki`` format,
which writes a directory tree of markdown wikilinks rather than returning a
string. Multi-file exporters implement the :class:`MultiFileExporter` protocol
and are invoked via the dedicated ``output`` argument; string formats are
unaffected.

This module has no external dependencies beyond the weld runtime.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from weld import _export_mermaid as _mermaid
from weld.graph import Graph


# ---------------------------------------------------------------------------
# Multi-file exporter protocol (ADR 0053)
# ---------------------------------------------------------------------------


@runtime_checkable
class MultiFileExporter(Protocol):
    """Contract for export formats that write a directory tree.

    Multi-file exporters do not return a string. They take an *output*
    directory and write any number of files beneath it. Implementations
    must be deterministic: re-running against an unchanged graph should
    produce byte-identical output (ADR 0053).
    """

    def write(self, output: Path) -> None:
        """Render the full export to *output*.

        Implementations create the directory if it does not exist and
        are responsible for any per-file atomicity guarantees.
        """
        ...


# ---------------------------------------------------------------------------
# ID sanitization
# ---------------------------------------------------------------------------


def _safe_id(node_id: str) -> str:
    """Convert a node ID to a diagram-safe identifier.

    Any character outside ``[A-Za-z0-9_]`` (colons, slashes, dashes, dots,
    spaces, and control bytes such as the cross-repo unit separator) is
    mapped to ``_`` so the result is a valid identifier in Mermaid, DOT,
    and D2 -- including federated ``repo<US>local-id`` node ids.
    """
    return "".join(
        c if c == "_" or (c.isascii() and c.isalnum()) else "_" for c in node_id
    )


def _collision_safe_ids(
    nodes: dict[str, dict], edges: list[dict]
) -> Callable[[str], str]:
    """Return an *injective* id sanitizer over this render's id universe.

    :func:`_safe_id` maps every illegal character to ``_``, so distinct source
    ids (``a:b`` vs ``a-b``) can collapse to the same identifier and silently
    merge. This wraps it with :func:`weld._export_mermaid._disambiguate`, which
    appends a short deterministic hash suffix *only* on real collisions -- every
    non-colliding id keeps its bare :func:`_safe_id` form, so output for graphs
    without collisions is unchanged.
    """
    ids: set[str] = set(nodes)
    for edge in edges:
        ids.update((edge.get("from"), edge.get("to")))
    ids.discard(None)
    id_map = _mermaid._disambiguate((nid, _safe_id(nid)) for nid in ids)
    return lambda nid: id_map.get(nid, _safe_id(nid))


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
    max_nodes: int | None = _mermaid.DEFAULT_MAX_NODES,
) -> str:
    """Serialize graph data to a clustered, styled Mermaid flowchart string.

    If *nodes* and *edges* are provided, uses those (subgraph mode);
    otherwise serializes the full graph. Nodes are grouped into
    ``subgraph`` blocks by file/module/package, styled per type via
    ``classDef``, given human-readable (escaped) labels, and truncation is
    annotated explicitly past *max_nodes*. See :mod:`weld._export_mermaid`.
    """
    if nodes is None or edges is None:
        data = graph.dump()
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])

    return _mermaid.render(nodes, edges, safe_id=_safe_id, max_nodes=max_nodes)


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

    safe_id = _collision_safe_ids(nodes, edges)
    lines: list[str] = ["digraph weld {"]
    lines.append("    rankdir=LR;")

    # Node definitions
    for node_id, node_data in sorted(nodes.items()):
        sid = safe_id(node_id)
        label = node_data.get("label", node_id)
        ntype = node_data.get("type", "")
        display = f"{label}\\n({ntype})" if ntype else label
        lines.append(f'    {sid} [label="{display}"];')

    # Edge definitions
    for edge in edges:
        src = safe_id(edge["from"])
        dst = safe_id(edge["to"])
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

    safe_id = _collision_safe_ids(nodes, edges)
    lines: list[str] = []

    # Node definitions
    for node_id, node_data in sorted(nodes.items()):
        sid = safe_id(node_id)
        label = node_data.get("label", node_id)
        ntype = node_data.get("type", "")
        display = f"{label} ({ntype})" if ntype else label
        lines.append(f"{sid}: {display}")

    # Edge definitions
    for edge in edges:
        src = safe_id(edge["from"])
        dst = safe_id(edge["to"])
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
#: Multi-file format names (ADR 0053). Each entry maps to a factory that
#: takes a loaded :class:`Graph` and returns a :class:`MultiFileExporter`.
_MULTI_FILE_FORMATS: dict[str, Any] = {
    "wiki": None,  # populated lazily to avoid import cycles
}


def _wiki_exporter_factory(graph: Graph) -> "MultiFileExporter":
    from weld._wiki_export import WikiExporter

    return WikiExporter(graph)


_MULTI_FILE_FORMATS["wiki"] = _wiki_exporter_factory


def export(
    fmt: str,
    *,
    node_id: str | None = None,
    depth: int = 1,
    root: str | Path = ".",
    output: str | Path | None = None,
) -> str:
    """Export the graph (or a subgraph) to the requested format.

    Parameters
    ----------
    fmt : str
        Output format. String formats: ``mermaid``, ``dot``, ``d2``.
        Multi-file formats: ``wiki`` (writes a directory tree, see
        ADR 0053).
    node_id : str, optional
        Center node for subgraph extraction. Ignored for multi-file
        formats, which always export the full graph.
    depth : int
        BFS depth for subgraph extraction (default 1). Ignored when
        *node_id* is ``None``.
    root : str or Path
        Project root containing ``.weld/graph.json``.
    output : str or Path, optional
        Target directory for multi-file formats. Required for ``wiki``.

    Returns
    -------
    str
        The serialized diagram string. For multi-file formats this is
        the empty string; the artefacts live under *output*.

    Raises
    ------
    ValueError
        If *fmt* is not a recognized format, or if a multi-file format
        is requested without *output*.
    """
    if fmt in _MULTI_FILE_FORMATS:
        if output is None:
            raise ValueError(
                f"format {fmt!r} requires --output=<dir>; multi-file exporters "
                "write to a directory rather than stdout."
            )
        g = Graph(Path(root))
        g.load()
        exporter = _MULTI_FILE_FORMATS[fmt](g)
        exporter.write(Path(output))
        return ""

    if fmt not in _FORMATS:
        raise ValueError(
            f"unknown export format: {fmt!r} (available: "
            f"{', '.join(sorted(list(_FORMATS) + list(_MULTI_FILE_FORMATS)))})"
        )

    g = Graph(Path(root))
    g.load()

    serializer = _FORMATS[fmt]

    if node_id is not None:
        # ADR 0041 alias-aware: rewrite legacy id to canonical first.
        node_id = getattr(g, "_alias_index", {}).get(node_id, node_id) \
            if node_id not in g._data.get("nodes", {}) else node_id
        nodes, edges = extract_subgraph(g, node_id, depth=depth)
        return serializer(g, nodes=nodes, edges=edges)

    return serializer(g)
