"""Static consistency audit checks for persisted Agent Graphs."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from weld._agent_graph_constants import (
    _CLEAR_DESCRIPTION_TYPES, _VAGUE_DESCRIPTIONS,
)
from weld._agent_graph_strict import (
    all_render_linked, suppressed_duplicate_findings,
    suppressed_vague_findings,
)
from weld._agent_graph_unused_skill import (
    instruction_bodies, text_mentions_skill,
)
from weld.agent_graph_authority_audit import authority_findings
from weld.agent_graph_inventory import asset_entries, node_entry
from weld.agent_graph_render_pairs import render_pair_links

_RESPONSIBILITY_TYPES = {"agent", "command", "instruction", "prompt", "skill"}


def audit_graph(
    graph: dict[str, Any],
    *,
    root: Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Run deterministic static audit checks over a persisted Agent Graph.

    *root* is the repository root to consult when an audit check needs to
    re-read source files (e.g. byte-level rendered-copy drift detection).
    Description-only checks are unaffected when *root* is omitted.

    When *strict* is true, also surface canonical+rendered groups that
    ADR 0029 normally suppresses, as ``info``-level findings with codes
    suffixed ``_suppressed``. The default-mode finding set is unchanged.
    """
    findings: list[dict[str, Any]] = []
    nodes = graph.get("nodes", {})
    assets = asset_entries(graph)
    generated_links = render_pair_links(graph)
    findings.extend(_broken_references(graph, nodes))
    findings.extend(_duplicate_names(assets, generated_links))
    findings.extend(_responsibility_overlap(assets))
    findings.extend(_path_scope_overlap(graph, nodes))
    findings.extend(_permission_conflicts(graph, nodes, assets))
    findings.extend(_unsafe_hooks(assets))
    findings.extend(_vague_descriptions(assets))
    findings.extend(_platform_drift(assets))
    findings.extend(authority_findings(graph, assets, nodes, root=root))
    findings.extend(_unused_skills(graph, assets, root=root))
    findings.extend(_unreachable_subagents(graph, assets))
    findings.extend(_commands_missing_agents(graph, nodes))
    findings.extend(_missing_mcp_config(nodes))
    if strict:
        findings.extend(suppressed_duplicate_findings(
            assets, generated_links, finding_factory=_finding, norm=_norm,
        ))
        findings.extend(suppressed_vague_findings(
            assets, finding_factory=_finding, norm=_norm,
        ))
    findings = _sort_findings(findings)
    return {
        "findings": findings,
        "summary": {
            "by_severity": _severity_counts(findings),
            "finding_count": len(findings),
        },
    }


def _broken_references(
    graph: dict[str, Any],
    nodes: dict[str, dict],
) -> list[dict[str, Any]]:
    findings = []
    for diagnostic in graph.get("meta", {}).get("diagnostics", []):
        if diagnostic.get("code") != "agent_graph_broken_reference":
            continue
        source_id = diagnostic.get("source_node")
        source = node_entry(source_id, nodes.get(source_id, {})) if source_id else None
        findings.append(_finding(
            "broken_reference",
            "Broken reference",
            str(diagnostic.get("message") or "Referenced file does not exist."),
            [source] if source is not None else [],
        ))
    return findings


def _duplicate_names(
    assets: list[dict[str, Any]],
    generated_links: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """ADR 0029: drop groups fully connected by ``generated_from`` (canonical+
    rendered pairs are intentional, not duplicates)."""
    groups = _group_assets(assets, lambda item: (item["type"], _norm(item["name"])))
    findings = []
    for items in groups.values():
        if len(items) <= 1 or all_render_linked(items, generated_links):
            continue
        findings.append(_finding(
            "duplicate_name",
            "Duplicate asset name",
            f"Multiple {items[0]['type']} assets share name {items[0]['name']!r}.",
            items,
        ))
    return findings


def _responsibility_overlap(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scoped = [
        asset for asset in assets
        if asset["type"] in _RESPONSIBILITY_TYPES and asset["description"]
    ]
    groups = _group_assets(scoped, lambda item: _norm(item["description"]))
    return [
        _finding(
            "responsibility_overlap",
            "Responsibility overlap",
            "Multiple assets use the same responsibility description.",
            items,
        )
        for items in groups.values()
        if len(items) > 1
    ]


def _path_scope_overlap(
    graph: dict[str, Any],
    nodes: dict[str, dict],
) -> list[dict[str, Any]]:
    by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in graph.get("edges", []):
        if edge.get("type") != "applies_to_path":
            continue
        source_id = edge.get("from")
        target_id = edge.get("to")
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            continue
        by_scope[target_id].append(node_entry(source_id, nodes.get(source_id, {})))
    return [
        _finding(
            "path_scope_overlap",
            "Path scope overlap",
            f"Multiple assets apply to path scope {nodes[scope_id]['label']!r}.",
            items,
        )
        for scope_id, items in by_scope.items()
        if len(items) > 1 and scope_id in nodes
    ]


def _permission_conflicts(
    graph: dict[str, Any],
    nodes: dict[str, dict],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    asset_ids = {asset["id"] for asset in assets}
    permissions: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"allow": set(), "deny": set()},
    )
    for edge in graph.get("edges", []):
        source_id = edge.get("from")
        target_id = edge.get("to")
        if source_id not in asset_ids or not isinstance(target_id, str):
            continue
        target = node_entry(target_id, nodes.get(target_id, {}))
        if target["type"] != "tool":
            continue
        if edge.get("type") == "provides_tool":
            permissions[source_id]["allow"].add(target["name"])
        if edge.get("type") == "restricts_tool":
            permissions[source_id]["deny"].add(target["name"])

    asset_by_id = {asset["id"]: asset for asset in assets}
    findings = []
    for items in _group_assets(assets, lambda item: _norm(item["name"])).values():
        allowed = set().union(*(permissions[item["id"]]["allow"] for item in items))
        denied = set().union(*(permissions[item["id"]]["deny"] for item in items))
        conflicts = sorted(allowed & denied)
        if conflicts:
            findings.append(_finding(
                "permission_conflict",
                "Tool permission conflict",
                f"Conflicting allow/deny tool hints: {', '.join(conflicts)}.",
                [asset_by_id[item["id"]] for item in items],
            ))
    return findings


def _unsafe_hooks(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for asset in assets:
        if asset["type"] != "hook":
            continue
        description = _norm(asset["description"])
        if not description or not any(word in description for word in ("risk", "rollback", "safe")):
            findings.append(_finding(
                "unsafe_hook",
                "Hook without safety description",
                "Hook does not describe safety, risk, or rollback behavior.",
                [asset],
            ))
    return findings


def _vague_descriptions(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for asset in assets:
        if asset["type"] not in _CLEAR_DESCRIPTION_TYPES:
            continue
        # ADR 0029: rendered copies strip frontmatter (ADR 0026); the canonical
        # still gets the check, so skip derived/generated to avoid double-counting.
        if asset["status"] in {"derived", "generated"}:
            continue
        description = _norm(asset["description"])
        words = [word for word in description.split() if word]
        if len(words) < 3 or description in _VAGUE_DESCRIPTIONS:
            findings.append(_finding(
                "vague_description",
                "Vague or missing description",
                f"{asset['type']} asset should have a precise activation description.",
                [asset],
            ))
    return findings


def _platform_drift(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    groups = _group_assets(assets, lambda item: (item["type"], _norm(item["name"])))
    for items in groups.values():
        descriptions = {_norm(item["description"]) for item in items if item["description"]}
        if len(items) > 1 and len(descriptions) > 1:
            findings.append(_finding(
                "platform_drift",
                "Platform variant drift",
                "Same-name platform variants have different descriptions.",
                items,
            ))
    return findings


def _unused_skills(
    graph: dict[str, Any],
    assets: list[dict[str, Any]],
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Skills with no ``uses_skill`` edges and no text mention in any
    agent or instruction body. The text-mention suppression silences
    instruction-mediated repos where AGENTS.md / project conventions
    activate skills indirectly."""
    used = {
        edge.get("to")
        for edge in graph.get("edges", [])
        if edge.get("type") == "uses_skill"
    }
    bodies = instruction_bodies(assets, root)
    findings: list[dict[str, Any]] = []
    for asset in assets:
        if asset["type"] != "skill" or asset["id"] in used:
            continue
        name = (asset["name"] or "").lower()
        if text_mentions_skill(name, bodies):
            continue
        findings.append(_finding(
            "unused_skill", "Unused skill",
            "Skill has no incoming uses_skill references.",
            [asset], severity="info",
        ))
    return findings


def _unreachable_subagents(
    graph: dict[str, Any],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reachable = {
        edge.get("to")
        for edge in graph.get("edges", [])
        if edge.get("type") in {"handoff_to", "invokes_agent"}
    }
    return [
        _finding(
            "unreachable_subagent",
            "Unreachable subagent",
            "Subagent has no incoming invocation or handoff reference.",
            [asset],
        )
        for asset in assets
        if asset["type"] == "subagent" and asset["id"] not in reachable
    ]


def _commands_missing_agents(
    graph: dict[str, Any],
    nodes: dict[str, dict],
) -> list[dict[str, Any]]:
    findings = []
    for edge in graph.get("edges", []):
        source = node_entry(str(edge.get("from") or ""), nodes.get(edge.get("from"), {}))
        target = node_entry(str(edge.get("to") or ""), nodes.get(edge.get("to"), {}))
        if (
            source["type"] == "command"
            and target["type"] == "agent"
            and target["status"] == "referenced"
        ):
            findings.append(_finding(
                "missing_agent",
                "Command references missing agent",
                f"Command {source['name']!r} references missing agent {target['name']!r}.",
                [source, target],
            ))
    return findings


def _missing_mcp_config(nodes: dict[str, dict]) -> list[dict[str, Any]]:
    findings = []
    for node_id, node in nodes.items():
        entry = node_entry(node_id, node)
        if entry["type"] == "mcp-server" and entry["status"] == "referenced":
            findings.append(_finding(
                "missing_mcp_config",
                "MCP server referenced but not configured",
                f"MCP server {entry['name']!r} is referenced but not configured.",
                [entry],
            ))
    return findings


def _finding(
    code: str,
    title: str,
    message: str,
    nodes: list[dict[str, Any]],
    *,
    severity: str = "warning",
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "nodes": _sort_nodes(nodes),
        "severity": severity,
        "title": title,
    }


def _group_assets(
    assets: list[dict[str, Any]],
    key_fn: Any,
) -> dict[Any, list[dict[str, Any]]]:
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        groups[key_fn(asset)].append(asset)
    return groups


def _sort_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(nodes, key=lambda node: (
        node["type"],
        node["name"].casefold(),
        node["platform_name"].casefold(),
        node["path"],
        node["id"],
    ))


def _sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(findings, key=lambda finding: (
        finding["severity"],
        finding["code"],
        finding["title"],
        finding["message"],
        [(node["type"], node["name"], node["path"], node["id"]) for node in finding["nodes"]],
    ))


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        severity = finding["severity"]
        counts[severity] = counts.get(severity, 0) + 1
    return dict(sorted(counts.items()))


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())
