"""Authority and rendered-copy audit checks for Agent Graphs."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from weld.agent_graph_inventory import node_entry


def authority_findings(
    graph: dict[str, Any],
    assets: list[dict[str, Any]],
    nodes: dict[str, dict],
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic authority and drift findings.

    When *root* is provided the audit additionally compares each rendered
    copy's on-disk bytes with what ``wd agents render`` would produce now.
    Without a *root* the byte-level check is skipped (the
    description-level checks below still run).
    """
    findings: list[dict[str, Any]] = []
    findings.extend(_ambiguous_canonical(assets))
    findings.extend(_rendered_copy_drift(graph, assets, nodes))
    findings.extend(_missing_render_targets(graph, nodes))
    if root is not None:
        findings.extend(_rendered_copy_content_drift(root, assets))
    return findings


def _rendered_copy_content_drift(
    root: Path,
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect byte-level drift between rendered copies and the canonical source.

    Complements ``rendered_copy_drift`` (which only compares description
    strings) by re-running the renderer in memory and flagging any pair
    whose on-disk bytes would differ.
    """
    # Local import: avoids importing the writer module when the audit is
    # invoked on a graph that has no sidecar (the writer pulls in
    # workspace_state, which is heavier than what the description-level
    # checks above need).
    from weld.agent_graph_render_writer import detect_content_drift

    drifts = detect_content_drift(root)
    if not drifts:
        return []
    asset_by_path = _assets_by_path(assets)
    findings: list[dict[str, Any]] = []
    for drift in drifts:
        canonical_asset = asset_by_path.get(drift["canonical"])
        rendered_asset = asset_by_path.get(drift["rendered"])
        nodes_for_finding = [a for a in (canonical_asset, rendered_asset) if a]
        findings.append(_finding(
            "rendered_copy_content_drift",
            "Rendered copy content drift",
            (
                f"Rendered copy {drift['rendered']!r} differs from a fresh "
                f"render of canonical source {drift['canonical']!r}. "
                "Run `wd agents render` to inspect the diff."
            ),
            nodes_for_finding,
        ))
    return findings


def _assets_by_path(assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for asset in assets:
        path = str(asset.get("path") or "")
        if path and path not in by_path:
            by_path[path] = asset
    return by_path


def _ambiguous_canonical(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        if asset["status"] == "canonical":
            groups[(asset["type"], _norm(asset["name"]))].append(asset)
    return [
        _finding(
            "ambiguous_canonical",
            "Ambiguous canonical ownership",
            f"Multiple canonical {items[0]['type']} assets share name {items[0]['name']!r}.",
            items,
        )
        for items in groups.values()
        if len(items) > 1
    ]


def _rendered_copy_drift(
    graph: dict[str, Any],
    assets: list[dict[str, Any]],
    nodes: dict[str, dict],
) -> list[dict[str, Any]]:
    asset_by_id = {asset["id"]: asset for asset in assets}
    pairs = _generated_pairs(graph, asset_by_id)
    pairs.extend(_same_name_authority_pairs(assets))
    findings = []
    seen: set[tuple[str, str]] = set()
    for rendered, canonical in pairs:
        key = (rendered["id"], canonical["id"])
        if key in seen or not _descriptions_drift(rendered, canonical):
            continue
        seen.add(key)
        findings.append(_finding(
            "rendered_copy_drift",
            "Rendered copy drift",
            "Rendered or generated copy differs from its canonical source description.",
            [canonical, rendered],
        ))
    return findings


def _missing_render_targets(
    graph: dict[str, Any],
    nodes: dict[str, dict],
) -> list[dict[str, Any]]:
    findings = []
    for diagnostic in graph.get("meta", {}).get("diagnostics", []):
        if diagnostic.get("code") != "agent_graph_missing_render_target":
            continue
        source_id = diagnostic.get("source_node")
        source = node_entry(source_id, nodes.get(source_id, {})) if source_id else None
        findings.append(_finding(
            "missing_render_target",
            "Rendered copy target missing",
            str(diagnostic.get("message") or "Rendered copy target is missing."),
            [source] if source is not None else [],
        ))
    return findings


def _generated_pairs(
    graph: dict[str, Any],
    asset_by_id: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs = []
    for edge in graph.get("edges", []):
        if edge.get("type") != "generated_from":
            continue
        rendered = asset_by_id.get(str(edge.get("from") or ""))
        canonical = asset_by_id.get(str(edge.get("to") or ""))
        if rendered is not None and canonical is not None:
            pairs.append((rendered, canonical))
    return pairs


def _same_name_authority_pairs(
    assets: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        groups[(asset["type"], _norm(asset["name"]))].append(asset)
    pairs = []
    for items in groups.values():
        canonical = [item for item in items if item["status"] == "canonical"]
        rendered = [
            item for item in items
            if item["status"] in {"derived", "generated"}
        ]
        for source in canonical:
            for copy in rendered:
                pairs.append((copy, source))
    return pairs


def _descriptions_drift(
    rendered: dict[str, Any],
    canonical: dict[str, Any],
) -> bool:
    rendered_description = _norm(rendered["description"])
    canonical_description = _norm(canonical["description"])
    return bool(rendered_description and canonical_description
                and rendered_description != canonical_description)


def _finding(
    code: str,
    title: str,
    message: str,
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "nodes": sorted(nodes, key=lambda node: (
            node["type"],
            node["name"].casefold(),
            node["platform_name"].casefold(),
            node["path"],
            node["id"],
        )),
        "severity": "warning",
        "title": title,
    }


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())
