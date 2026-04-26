"""ADR 0029: shared canonical<->rendered pair resolution for audit and explain.

Discovery sometimes records ``generated_from`` against a ``file:`` node
rather than the canonical asset node (when both exist for the same path).
Audit suppression and explain payloads both bridge that gap by also
consulting ``render_paths`` declarations -- but ONLY when the declaring
node is canonical (``authority == "canonical"`` or normalized truthy
equivalent). A non-canonical node that claims to render another asset
must NOT contribute to the bridge: otherwise a malicious or
mis-configured frontmatter could mask a legitimate ``duplicate_name``
or ``vague_description`` finding by impersonating the renderer
contract. Explicit ``generated_from`` edges produced by discover are
independent provenance and continue to bridge regardless of declaring
authority -- the authority gate applies only to the ``render_paths``
claim layer."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from weld.agent_graph_assets import ASSET_NODE_TYPES, node_entry


def render_pair_links(graph: dict[str, Any]) -> dict[str, set[str]]:
    """Undirected adjacency of asset IDs forming a canonical+rendered pair.

    Only nodes whose ``authority`` is canonical contribute their
    ``render_paths`` to the bridge -- see module docstring for the
    trust-boundary rationale."""
    links: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("edges", []):
        if edge.get("type") != "generated_from":
            continue
        src, dst = edge.get("from"), edge.get("to")
        if isinstance(src, str) and isinstance(dst, str):
            links[src].add(dst)
            links[dst].add(src)
    by_path = _assets_by_path(graph)
    for node in graph.get("nodes", {}).values():
        props = _props(node)
        if not _is_canonical(props):
            continue
        canonical_path = str(props.get("file") or "")
        for render_path in props.get("render_paths") or []:
            for c in by_path.get(canonical_path, ()):
                for d in by_path.get(str(render_path), ()):
                    links[c].add(d)
                    links[d].add(c)
    return links


def render_pair_partners(
    graph: dict[str, Any], target_id: str, target_path: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (canonical_partners, rendered_partners) as relationship-shaped
    entries (with ``edge_type = 'generated_from'``) for *target_id*.

    The ``render_paths`` claim layer is honored only when the declaring
    node is canonical. ``generated_from`` edges from discover are still
    honored unconditionally."""
    nodes = graph.get("nodes", {})
    by_path = _assets_by_path(graph)
    canonical_ids: set[str] = set()
    rendered_ids: set[str] = set()
    target_props = _props(nodes.get(target_id, {}))
    if _is_canonical(target_props):
        for render_path in target_props.get("render_paths") or []:
            for asset_id in by_path.get(str(render_path), ()):
                if asset_id != target_id:
                    rendered_ids.add(asset_id)
    for node in nodes.values():
        props = _props(node)
        if not _is_canonical(props):
            continue
        canonical_path = str(props.get("file") or "")
        renders = {str(p) for p in (props.get("render_paths") or [])}
        if target_path and target_path in renders:
            for asset_id in by_path.get(canonical_path, ()):
                if asset_id != target_id:
                    canonical_ids.add(asset_id)
        if target_path and canonical_path == target_path:
            for r_path in renders:
                for asset_id in by_path.get(r_path, ()):
                    if asset_id != target_id:
                        rendered_ids.add(asset_id)
    for edge in graph.get("edges", []):
        if edge.get("type") != "generated_from":
            continue
        if edge.get("from") == target_id and isinstance(edge.get("to"), str):
            canonical_ids.add(str(edge["to"]))
        if edge.get("to") == target_id and isinstance(edge.get("from"), str):
            rendered_ids.add(str(edge["from"]))
    canonical_ids.discard(target_id)
    rendered_ids.discard(target_id)
    return (
        _as_relationships(sorted(canonical_ids), nodes),
        _as_relationships(sorted(rendered_ids), nodes),
    )


def _is_canonical(props: dict[str, Any]) -> bool:
    """Return ``True`` iff the node props mark this asset as the
    canonical authority. Mirrors the truthy values accepted by
    ``asset_status`` in ``agent_graph_assets``: the literal string
    ``"canonical"`` (the value the authority pipeline normalizes to,
    see ``_authority_value`` in ``agent_graph_authority``) and the
    boolean ``True`` (the raw frontmatter form before normalization).
    Anything else -- ``"derived"``, ``"manual"``, missing, or any
    string that has not been normalized -- is treated as
    non-authoritative for bridge purposes."""
    authority = props.get("authority")
    return authority == "canonical" or authority is True


def _assets_by_path(graph: dict[str, Any]) -> dict[str, list[str]]:
    """Map asset path -> list of asset IDs at that path. Mirrors
    ``asset_entry`` filters without importing it (avoids a cycle)."""
    by_path: dict[str, list[str]] = defaultdict(list)
    for node_id, node in graph.get("nodes", {}).items():
        props = _props(node)
        node_type = str(node.get("type") or "")
        if node_type not in ASSET_NODE_TYPES:
            continue
        if props.get("source_strategy") != "agent_graph_static":
            continue
        if props.get("status") == "referenced":
            continue
        path = str(props.get("file") or "")
        if path:
            by_path[path].append(node_id)
    return by_path


def _props(node: dict[str, Any]) -> dict[str, Any]:
    props = node.get("props")
    return props if isinstance(props, dict) else {}


def _as_relationships(
    asset_ids: list[str], nodes: dict[str, dict],
) -> list[dict[str, Any]]:
    return [
        {
            "confidence": "definite",
            "edge_type": "generated_from",
            "node": node_entry(asset_id, nodes.get(asset_id, {})),
            "provenance": {},
        }
        for asset_id in asset_ids
    ]
