"""Inventory and explanation helpers for persisted Agent Graphs."""

from __future__ import annotations

from typing import Any

ASSET_NODE_TYPES = frozenset({
    "agent",
    "command",
    "config",
    "hook",
    "instruction",
    "mcp-server",
    "prompt",
    "skill",
    "subagent",
    "workflow",
})

_RELATED_KEYS = {
    "command": "commands",
    "hook": "hooks",
    "mcp-server": "mcp_servers",
    "skill": "skills",
}


def asset_entries(
    graph: dict[str, Any],
    *,
    type_filter: str | None = None,
    platform_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic discovered customization asset entries."""
    entries = [
        entry
        for node_id, node in graph.get("nodes", {}).items()
        if (entry := asset_entry(node_id, node)) is not None
    ]
    if type_filter:
        entries = [entry for entry in entries if entry["type"] == type_filter]
    if platform_filter:
        platform = platform_filter.casefold()
        entries = [
            entry
            for entry in entries
            if platform in {
                entry["platform"].casefold(),
                entry["platform_name"].casefold(),
            }
        ]
    return _sort_entries(entries)


def asset_entry(node_id: str, node: dict[str, Any]) -> dict[str, Any] | None:
    """Return a listable customization asset entry for *node*, if applicable."""
    entry = node_entry(node_id, node)
    props = node.get("props") if isinstance(node.get("props"), dict) else {}
    if entry["type"] not in ASSET_NODE_TYPES:
        return None
    if props.get("source_strategy") != "agent_graph_static":
        return None
    if props.get("status") == "referenced":
        return None
    return entry


def node_entry(node_id: str, node: dict[str, Any]) -> dict[str, Any]:
    """Return a stable display entry for any graph node."""
    props = node.get("props") if isinstance(node.get("props"), dict) else {}
    platform = str(props.get("platform") or props.get("source_platform") or "generic")
    platform_name = str(props.get("platform_name") or platform)
    name = str(props.get("name") or node.get("label") or node_id)
    return {
        "description": single_line(props.get("description")),
        "id": node_id,
        "name": name,
        "path": str(props.get("file") or ""),
        "platform": platform,
        "platform_name": platform_name,
        "status": asset_status(props),
        "type": str(node.get("type") or ""),
    }


def explain_asset(graph: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Return a deterministic explanation payload for an asset query."""
    nodes = graph.get("nodes", {})
    target_id = resolve_asset_id(graph, query)
    if target_id is None:
        return None

    target = node_entry(target_id, nodes[target_id])
    assets = asset_entries(graph)
    incoming, outgoing = _relationships(graph, target_id)
    return {
        "asset": target,
        "incoming_references": incoming,
        "outgoing_references": outgoing,
        "overlaps": _overlaps(target, assets),
        "platform_variants": _platform_variants(target, assets),
        "purpose": target["description"],
        "related": _related(incoming + outgoing),
        "source_files": sorted({target["path"]} if target["path"] else set()),
    }


def impact_asset(graph: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Return a deterministic impact payload for a proposed asset change."""
    explanation = explain_asset(graph, query)
    if explanation is None:
        return None

    asset = explanation["asset"]
    same_name = [
        entry for entry in explanation["platform_variants"]
        if entry["id"] != asset["id"]
    ]
    same_purpose = _same_purpose_variants(asset, asset_entries(graph))
    canonical_assets = _canonical_assets(asset, asset_entries(graph))
    affected = _affected_nodes(
        explanation["outgoing_references"], explanation["incoming_references"],
        same_name, same_purpose, canonical_assets,
    )
    return {
        "affected_nodes": affected,
        "asset": asset,
        "authority_status": asset["status"],
        "canonical_assets": canonical_assets,
        "change_checklist": _change_checklist(
            asset, affected, same_name, same_purpose, canonical_assets,
        ),
        "downstream": explanation["outgoing_references"],
        "incoming_references": explanation["incoming_references"],
        "same_name_variants": same_name,
        "same_purpose_variants": same_purpose,
    }


def resolve_asset_id(graph: dict[str, Any], query: str) -> str | None:
    """Resolve a user query by node ID, source path, or asset name."""
    q = _clean_query(query)
    nodes = graph.get("nodes", {})
    if q in nodes and asset_entry(q, nodes[q]) is not None:
        return q

    entries = asset_entries(graph)
    path_matches = [entry for entry in entries if _clean_query(entry["path"]) == q]
    if path_matches:
        return _sort_entries(path_matches)[0]["id"]
    name_matches = [entry for entry in entries if _clean_query(entry["name"]) == q]
    if name_matches:
        return _best_connected_match(graph, name_matches)["id"]
    path_matches = [
        entry
        for entry in entries
        if entry["path"].startswith(f"{q}#")
    ]
    if path_matches:
        return _sort_entries(path_matches)[0]["id"]
    return None


def single_line(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def asset_status(props: dict[str, Any]) -> str:
    authority = props.get("authority")
    if authority is True:
        return "canonical"
    if authority == "canonical":
        return "canonical"
    if props.get("generated") is True:
        return "generated"
    if authority:
        return str(authority)
    status = props.get("status")
    return str(status) if status else "manual"


def _relationships(
    graph: dict[str, Any],
    target_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = graph.get("nodes", {})
    incoming: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    for edge in graph.get("edges", []):
        if edge.get("to") == target_id:
            incoming.append(_relationship(edge, nodes, "from"))
        if edge.get("from") == target_id:
            outgoing.append(_relationship(edge, nodes, "to"))
    return _sort_relationships(incoming), _sort_relationships(outgoing)


def _relationship(
    edge: dict[str, Any],
    nodes: dict[str, dict],
    other_key: str,
) -> dict[str, Any]:
    props = edge.get("props") if isinstance(edge.get("props"), dict) else {}
    other_id = str(edge.get(other_key) or "")
    node = nodes.get(other_id, {})
    return {
        "confidence": str(props.get("confidence") or ""),
        "edge_type": str(edge.get("type") or ""),
        "node": node_entry(other_id, node),
        "provenance": props.get("provenance") or {},
    }


def _related(relationships: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    related = {key: [] for key in _RELATED_KEYS.values()}
    seen: set[tuple[str, str]] = set()
    for relationship in relationships:
        node = relationship["node"]
        key = _RELATED_KEYS.get(node["type"])
        dedupe_key = (node["type"], node["id"])
        if key is None or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        related[key].append(node)
    return {key: _sort_entries(entries) for key, entries in related.items()}


def _platform_variants(
    target: dict[str, Any],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_name = _norm(target["name"])
    return _sort_entries([
        entry
        for entry in assets
        if entry["type"] == target["type"] and _norm(entry["name"]) == target_name
    ])


def _overlaps(
    target: dict[str, Any],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    overlaps: list[dict[str, Any]] = []
    target_name = _norm(target["name"])
    target_description = _norm(target["description"])
    for entry in assets:
        if entry["id"] == target["id"]:
            continue
        reasons = []
        if _norm(entry["name"]) == target_name:
            reasons.append("same name")
        if target_description and _norm(entry["description"]) == target_description:
            reasons.append("same description")
        if reasons:
            copy = dict(entry)
            copy["reason"] = ", ".join(reasons)
            overlaps.append(copy)
    return _sort_entries(overlaps)


def _same_purpose_variants(
    target: dict[str, Any],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_description = _norm(target["description"])
    if not target_description:
        return []
    return _sort_entries([
        entry
        for entry in assets
        if entry["id"] != target["id"]
        and _norm(entry["description"]) == target_description
    ])


def _canonical_assets(
    target: dict[str, Any],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_name = _norm(target["name"])
    return _sort_entries([
        entry for entry in assets
        if entry["type"] == target["type"]
        and _norm(entry["name"]) == target_name
        and entry["status"] == "canonical"
    ])


def _affected_nodes(
    downstream: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    same_name: list[dict[str, Any]],
    same_purpose: list[dict[str, Any]],
    canonical_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    affected: dict[str, dict[str, Any]] = {}
    for relationship in downstream + incoming:
        node = relationship["node"]
        affected[node["id"]] = node
    for entry in same_name + same_purpose + canonical_assets:
        affected[entry["id"]] = entry
    return _sort_entries(list(affected.values()))


def _change_checklist(
    asset: dict[str, Any],
    affected: list[dict[str, Any]],
    same_name: list[dict[str, Any]],
    same_purpose: list[dict[str, Any]],
    canonical_assets: list[dict[str, Any]],
) -> list[str]:
    checklist = []
    if asset["status"] != "canonical" and canonical_assets:
        primary = canonical_assets[0]
        checklist.append(f"Update canonical source first: {primary['path'] or primary['id']}")
    checklist.append(f"Review source asset: {asset['path'] or asset['id']}")
    if same_name:
        checklist.append("Update or intentionally leave same-name platform variants.")
    if same_purpose:
        checklist.append("Review same-purpose variants for wording or behavior drift.")
    if affected:
        checklist.append("Review directly affected graph nodes.")
    checklist.append(f"Run wd agents explain {asset['id']}")
    checklist.append(f"Run wd agents impact {asset['id']} --json")
    checklist.append("Run wd agents audit")
    return checklist


def _sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda item: (
        item["platform_name"].casefold(),
        item["type"],
        item["name"].casefold(),
        item["path"],
        item["id"],
    ))


def _sort_relationships(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda item: (
        item["edge_type"],
        item["node"]["type"],
        item["node"]["name"].casefold(),
        item["node"]["path"],
        item["node"]["id"],
        str(item["provenance"].get("raw", "")),
    ))


def _best_connected_match(
    graph: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    degrees = _node_degrees(graph)
    return sorted(
        entries,
        key=lambda item: (
            _authority_rank(item),
            -degrees.get(item["id"], 0),
            item["platform_name"].casefold(),
            item["type"],
            item["name"].casefold(),
            item["path"],
            item["id"],
        ),
    )[0]


def _node_degrees(graph: dict[str, Any]) -> dict[str, int]:
    degrees: dict[str, int] = {}
    for edge in graph.get("edges", []):
        for key in ("from", "to"):
            node_id = edge.get(key)
            if isinstance(node_id, str):
                degrees[node_id] = degrees.get(node_id, 0) + 1
    return degrees


def _clean_query(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.casefold()


def _authority_rank(item: dict[str, Any]) -> int:
    return {
        "canonical": 0,
        "manual": 1,
        "generated": 2,
        "derived": 3,
    }.get(item["status"], 4)


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())
