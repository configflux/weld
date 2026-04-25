"""Materialize parsed Agent Graph metadata into nodes and edges."""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any

from weld.agent_graph_metadata import AgentGraphReference, ParsedAgentGraphAsset

_NODE_ID_RE = re.compile(r"[^a-z0-9_.:-]+")


def materialize_agent_graph(
    root: Path,
    assets: list[Any],
    parsed: dict[str, ParsedAgentGraphAsset],
    *,
    platform_labels: dict[str, str],
    source_strategy: str,
) -> tuple[dict[str, dict], list[dict], dict[str, str]]:
    """Build deterministic graph nodes and edges from parsed static assets."""
    nodes, source_ids, index = _nodes_for_assets(
        assets, parsed, platform_labels, source_strategy,
    )
    edges = _edges_for_assets(
        root, assets, parsed, nodes, source_ids, index, platform_labels, source_strategy,
    )
    return nodes, edges, source_ids


def diagnostics_for_assets(
    assets: list[Any],
    parsed: dict[str, ParsedAgentGraphAsset],
    source_ids: dict[str, str],
) -> list[dict]:
    """Attach source node provenance to parser diagnostics."""
    diagnostics: list[dict] = []
    for asset in assets:
        for diagnostic in parsed[asset.path].diagnostics:
            copy_diag = copy.deepcopy(diagnostic)
            copy_diag.setdefault("source_node", source_ids[asset.path])
            diagnostics.append(copy_diag)
    return diagnostics


def _nodes_for_assets(
    assets: list[Any],
    parsed: dict[str, ParsedAgentGraphAsset],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> tuple[dict[str, dict], dict[str, str], dict[tuple[str, str], list[str]]]:
    nodes: dict[str, dict] = {}
    source_ids: dict[str, str] = {}
    index: dict[tuple[str, str], list[str]] = {}
    used_ids: set[str] = set()
    for asset in assets:
        node_id = _node_id_for_values(
            asset.node_type, asset.platform, asset.name, asset.path, used_ids,
        )
        props = asset.props()
        props.update(copy.deepcopy(parsed[asset.path].props))
        nodes[node_id] = {
            "type": asset.node_type,
            "label": str(props.get("name") or asset.name),
            "props": props,
        }
        source_ids[asset.path] = node_id
        _index_node(index, nodes, node_id)
    for asset in assets:
        for derived in parsed[asset.path].derived_nodes:
            props = {
                "file": derived.path,
                "name": derived.name,
                "platform": derived.platform,
                "platform_name": platform_labels.get(derived.platform, derived.platform),
                "source_kind": derived.source_kind,
                "source_platform": derived.platform,
                "source_strategy": source_strategy,
                "status": "manual",
            }
            props.update(copy.deepcopy(derived.props))
            node_id = _node_id_for_values(
                derived.node_type, derived.platform, derived.name, derived.path, used_ids,
            )
            nodes[node_id] = {"type": derived.node_type, "label": derived.name, "props": props}
            source_ids[derived.path] = node_id
            _index_node(index, nodes, node_id)
    return nodes, source_ids, index


def _edges_for_assets(
    root: Path,
    assets: list[Any],
    parsed: dict[str, ParsedAgentGraphAsset],
    nodes: dict[str, dict],
    source_ids: dict[str, str],
    index: dict[tuple[str, str], list[str]],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> list[dict]:
    edges: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    used_ids = set(nodes)
    for asset in assets:
        source_id = source_ids[asset.path]
        _add_reference_edges(
            root, asset.path, asset.platform, source_id, parsed[asset.path].references,
            nodes, used_ids, index, edges, seen, platform_labels, source_strategy,
        )
        for derived in parsed[asset.path].derived_nodes:
            derived_id = source_ids[derived.path]
            edge_type = "triggers_on_event" if derived.node_type == "hook" else "configures"
            _add_edge(edges, seen, source_id, derived_id, edge_type, {
                "source_strategy": source_strategy,
                "confidence": "definite",
                "provenance": {"file": asset.path, "line": 1, "raw": derived.path},
            })
            _add_reference_edges(
                root, asset.path, derived.platform, derived_id, derived.references,
                nodes, used_ids, index, edges, seen, platform_labels, source_strategy,
            )
    return edges


def _add_reference_edges(
    root: Path,
    source_path: str,
    source_platform: str,
    source_id: str,
    references: tuple[AgentGraphReference, ...],
    nodes: dict[str, dict],
    used_ids: set[str],
    index: dict[tuple[str, str], list[str]],
    edges: list[dict],
    seen: set[tuple[str, str, str, str]],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> None:
    for reference in references:
        for target_id in _target_ids(
            root, source_path, source_platform, reference, nodes, used_ids,
            index, platform_labels, source_strategy,
        ):
            _add_edge(edges, seen, source_id, target_id, reference.edge_type, {
                "source_strategy": source_strategy,
                "confidence": reference.confidence,
                "provenance": {
                    "file": source_path,
                    "line": reference.line,
                    "raw": reference.raw,
                    "target": reference.target_name,
                    "target_type": reference.target_type,
                },
            })


def _target_ids(
    root: Path,
    source_path: str,
    source_platform: str,
    reference: AgentGraphReference,
    nodes: dict[str, dict],
    used_ids: set[str],
    index: dict[tuple[str, str], list[str]],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> list[str]:
    if reference.target_type == "file":
        target = _resolved_file(root, source_path, reference.target_path or reference.target_name)
        return [_ensure_node(
            nodes, used_ids, index, "file", "generic", target or reference.target_name,
            {"file": target or reference.target_name, "exists": target is not None},
            platform_labels, source_strategy,
        )]
    if reference.target_type in {"scope", "tool"}:
        return [_ensure_node(
            nodes, used_ids, index, reference.target_type, "generic",
            reference.target_name, {}, platform_labels, source_strategy,
        )]

    matches = index.get((reference.target_type, _norm(reference.target_name)), [])
    same_platform = [
        node_id for node_id in matches
        if nodes[node_id]["props"].get("platform") == source_platform
    ]
    if same_platform or matches:
        return same_platform or matches[:1]
    return [_ensure_node(
        nodes, used_ids, index, reference.target_type, source_platform,
        reference.target_name, {"status": "referenced"}, platform_labels, source_strategy,
    )]


def _ensure_node(
    nodes: dict[str, dict],
    used_ids: set[str],
    index: dict[tuple[str, str], list[str]],
    node_type: str,
    platform: str,
    name: str,
    props: dict[str, Any],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> str:
    existing = index.get((node_type, _norm(name)), [])
    if existing and node_type in {"file", "scope", "tool"}:
        return existing[0]
    merged = {"name": name, "source_strategy": source_strategy, "status": "referenced"}
    if platform != "generic":
        merged["platform"] = platform
        merged["platform_name"] = platform_labels.get(platform, platform)
    merged.update(copy.deepcopy(props))
    node_id = _node_id_for_values(
        node_type, platform, name, str(merged.get("file", name)), used_ids,
    )
    nodes[node_id] = {"type": node_type, "label": name, "props": merged}
    _index_node(index, nodes, node_id)
    return node_id


def _resolved_file(root: Path, source_path: str, target: str) -> str | None:
    for candidate in (root / target, (root / source_path).parent / target):
        try:
            rel = candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if (root / rel).is_file():
            return rel
    return None


def _node_id_for_values(
    node_type: str,
    platform: str,
    name: str,
    path: str,
    used_ids: set[str],
) -> str:
    base = _slug(f"{node_type}:{platform}:{name}")
    if base not in used_ids:
        used_ids.add(base)
        return base
    candidate = f"{base}:{hashlib.sha1(path.encode('utf-8')).hexdigest()[:8]}"
    used_ids.add(candidate)
    return candidate


def _index_node(index: dict[tuple[str, str], list[str]], nodes: dict[str, dict], node_id: str) -> None:
    node = nodes[node_id]
    names = {node["label"], node.get("props", {}).get("name")}
    for name in names:
        if name:
            index.setdefault((node["type"], _norm(str(name))), []).append(node_id)


def _add_edge(
    edges: list[dict],
    seen: set[tuple[str, str, str, str]],
    from_id: str,
    to_id: str,
    edge_type: str,
    props: dict[str, Any],
) -> None:
    raw = str(props.get("provenance", {}).get("raw", ""))
    key = (from_id, to_id, edge_type, raw)
    if key in seen:
        return
    seen.add(key)
    edges.append({"from": from_id, "to": to_id, "type": edge_type, "props": props})


def _norm(value: str) -> str:
    return _slug(value)


def _slug(value: str) -> str:
    lowered = value.strip().lower()
    collapsed = _NODE_ID_RE.sub("-", lowered)
    return collapsed.strip("-") or "asset"

