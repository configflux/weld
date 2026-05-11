"""Markdown renderers for the wiki / agent-readable export (ADR 0053).

Pure functions consumed by :mod:`weld._wiki_export`. Each renderer takes
the already-normalized inputs (graph slice plus the deterministic
``id_map``) and returns the page body as a string. Renderers do not
touch the filesystem; they are testable in isolation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# ---------------------------------------------------------------------------
# Node page
# ---------------------------------------------------------------------------


def render_node_page(
    *,
    node_id: str,
    node: Mapping[str, Any],
    outgoing: list[Mapping[str, Any]],
    incoming: list[Mapping[str, Any]],
    id_map: Mapping[str, str],
    community: str | None,
) -> str:
    """Render the markdown page for a single node.

    The page carries a YAML-style frontmatter block (``id``, ``type``,
    ``origin``, ``confidence``), a heading, the description (if any),
    outgoing and incoming edges with inline confidence labels, and the
    source path (when known).
    """
    props = _safe_props(node)
    fm = _render_frontmatter(node_id=node_id, node=node, props=props)
    heading = f"# {node.get('label') or node_id}\n"
    desc = _render_description(props)
    out_block = _render_edges(
        edges=outgoing, header="**Outgoing edges:**", direction="out",
    )
    in_block = _render_edges(
        edges=incoming, header="**Incoming edges:**", direction="in",
    )
    source = _render_source(props)
    comm = _render_community(community)
    parts = [fm, heading]
    if desc:
        parts.append(desc)
    parts.extend([out_block, in_block])
    if source:
        parts.append(source)
    if comm:
        parts.append(comm)
    return "\n".join(parts).rstrip() + "\n"


def _render_frontmatter(
    *, node_id: str, node: Mapping[str, Any], props: Mapping[str, Any],
) -> str:
    ntype = str(node.get("type", "unknown"))
    origin = _origin_for(node, props)
    # ``confidence`` on a node is derived from the inputs that produced
    # its edges. The ADR contract is that the field is present; we use
    # a conservative heuristic: ``definite`` when every incoming/outgoing
    # edge claim is definite, otherwise the lowest confidence we see.
    # For the frontmatter we surface the per-node default ``definite``
    # for project nodes and ``inferred`` for everything else, then let
    # the inline edge labels carry the per-edge truth.
    confidence = _frontmatter_confidence(origin)
    lines = [
        "---",
        f"id: {node_id}",
        f"type: {ntype}",
        f"origin: {origin}",
        f"confidence: {confidence}",
        "---",
        "",
    ]
    return "\n".join(lines)


def _origin_for(node: Mapping[str, Any], props: Mapping[str, Any]) -> str:
    explicit = props.get("origin")
    if isinstance(explicit, str) and explicit:
        return explicit
    # Fallback to ADR-0042 classifier when origin is unset.
    from weld._graph_origin import classify_node

    try:
        return classify_node(dict(node))
    except Exception:
        return "project"


def _frontmatter_confidence(origin: str) -> str:
    """Conservative default node-level confidence for frontmatter.

    Per ADR 0050 the canonical confidence signal lives on edges; this
    field is a per-node summary that callers can refine as they ingest
    the wiki output. ``project`` and ``stdlib`` nodes default to
    ``definite``; everything else defaults to ``inferred``.
    """
    if origin in ("project", "stdlib"):
        return "definite"
    return "inferred"


def _render_description(props: Mapping[str, Any]) -> str:
    desc = props.get("description")
    if not isinstance(desc, str) or not desc.strip():
        return ""
    return f"**Description:** {desc.strip()}\n"


def _outgoing_sort_key(edge: Mapping[str, Any]) -> tuple[str, str]:
    return (str(edge.get("type", "")), str(edge.get("to", "")))


def _incoming_sort_key(edge: Mapping[str, Any]) -> tuple[str, str]:
    return (str(edge.get("type", "")), str(edge.get("from", "")))


def _render_edges(
    *,
    edges: list[Mapping[str, Any]],
    header: str,
    direction: str,
) -> str:
    """Render outgoing or incoming edges sorted deterministically.

    Outgoing edges sort by ``(type, to)``; incoming by ``(type, from)``.
    Each line carries the confidence and source-strategy inline so an
    agent reading the markdown has the trust signal at hand.
    """
    if not edges:
        return f"{header} _(none)_\n"
    if direction == "out":
        ordered = sorted(edges, key=_outgoing_sort_key)
        rendered = [_format_outgoing_edge(e) for e in ordered]
    else:
        ordered = sorted(edges, key=_incoming_sort_key)
        rendered = [_format_incoming_edge(e) for e in ordered]
    return header + "\n" + "\n".join(rendered) + "\n"


def _format_outgoing_edge(edge: Mapping[str, Any]) -> str:
    etype = str(edge.get("type", "relates_to"))
    target = str(edge.get("to", ""))
    attribution = _edge_attribution(edge)
    return f"- {etype} -> [[{target}]]{attribution}"


def _format_incoming_edge(edge: Mapping[str, Any]) -> str:
    etype = str(edge.get("type", "relates_to"))
    source = str(edge.get("from", ""))
    attribution = _edge_attribution(edge)
    return f"- [[{source}]] --{etype}-->{attribution}"


def _edge_attribution(edge: Mapping[str, Any]) -> str:
    props = edge.get("props") or {}
    confidence = props.get("confidence") or "definite"
    source = props.get("source_strategy")
    if source:
        return f" _({confidence}, source: {source})_"
    return f" _({confidence})_"


def _render_source(props: Mapping[str, Any]) -> str:
    file_path = props.get("file")
    if not file_path:
        return ""
    span = props.get("span")
    if span:
        return f"\n**Source path:** {file_path}:{span}\n"
    return f"\n**Source path:** {file_path}\n"


def _render_community(community: str | None) -> str:
    if not community:
        return ""
    return f"\n**Community:** [[community:{community}]]\n"


def _safe_props(node: Mapping[str, Any]) -> dict[str, Any]:
    props = node.get("props")
    if isinstance(props, Mapping):
        return dict(props)
    return {}


# ---------------------------------------------------------------------------
# By-type page
# ---------------------------------------------------------------------------


def render_by_type_page(
    *,
    node_type: str,
    node_ids: list[str],
    id_map: Mapping[str, str],
) -> str:
    """Render the page that lists every node of a given type.

    Node IDs are listed alphabetically as wikilinks.
    """
    lines = [
        f"# Nodes of type `{node_type}`",
        "",
        f"Total: {len(node_ids)}",
        "",
    ]
    for nid in sorted(node_ids):
        lines.append(f"- [[{nid}]]")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# By-community page
# ---------------------------------------------------------------------------


def render_by_community_page(
    *,
    community_id: str,
    summary: Mapping[str, Any],
    node_ids: list[str],
    id_map: Mapping[str, str],
) -> str:
    """Render the page for a single ADR-0039 community.

    The summary block surfaces dominant type/language plus member count;
    the body lists every member node as a wikilink.
    """
    title = summary.get("title") or community_id
    dominant_type = summary.get("dominant_type")
    dominant_language = summary.get("dominant_language")
    lines = [
        f"# Community `{community_id}` -- {title}",
        "",
        f"Size: {len(node_ids)}",
    ]
    if dominant_type:
        lines.append(f"Dominant type: `{dominant_type}`")
    if dominant_language:
        lines.append(f"Dominant language: `{dominant_language}`")
    lines.append("")
    lines.append("## Members")
    lines.append("")
    for nid in sorted(node_ids):
        lines.append(f"- [[{nid}]]")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------


def render_index_page(
    *,
    nodes: Mapping[str, Mapping[str, Any]],
    communities: Mapping[str, Any],
) -> str:
    """Render the top-level entry point ``index.md``.

    Carries type counts, top hubs (when the community payload provides
    them), and a pointer to the per-type and per-community pages.
    """
    type_counts: dict[str, int] = {}
    for _nid, node in nodes.items():
        t = str(node.get("type", "unknown")) or "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1

    lines = [
        "# Wiki export",
        "",
        "This directory is generated by `wd export --format=wiki`.",
        "Each markdown file is part of a graph: cross-references are",
        "rendered as `[[node-id]]` wikilinks.",
        "",
        "## Counts by type",
        "",
        f"Total nodes: {len(nodes)}",
        "",
    ]
    for t in sorted(type_counts):
        lines.append(f"- [[by-type/{_safe_filename(t)}]] -- {type_counts[t]} {t}")
    lines.append("")
    lines.append("## Top hubs")
    lines.append("")
    hubs = communities.get("hubs") or []
    if hubs:
        for hub in hubs[:12]:
            hid = hub.get("id") or ""
            degree = hub.get("degree", "?")
            lines.append(f"- [[{hid}]] (degree {degree})")
    else:
        lines.append("_(none)_")
    lines.append("")
    lines.append("## Communities")
    lines.append("")
    comms = communities.get("communities") or []
    if comms:
        for c in comms[:12]:
            cid = c.get("id") or ""
            size = c.get("size", "?")
            title = c.get("title") or cid
            lines.append(
                f"- [[by-community/{_safe_filename(cid)}]] -- {title} ({size} nodes)"
            )
    else:
        lines.append("See `by-community/` for the full list.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _safe_filename(name: str) -> str:
    """Filename-safe slug.

    Mirrors :func:`weld._wiki_export._safe_filename` but kept local so
    the renderers module has no upward import dependency.
    """
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_.]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown"


__all__ = [
    "render_by_community_page",
    "render_by_type_page",
    "render_index_page",
    "render_node_page",
]
