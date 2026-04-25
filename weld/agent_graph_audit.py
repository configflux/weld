"""Static consistency audit checks for persisted Agent Graphs."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from weld.agent_graph_authority_audit import authority_findings
from weld.agent_graph_inventory import asset_entries, node_entry

_CLEAR_DESCRIPTION_TYPES = {"agent", "skill", "subagent"}
_RESPONSIBILITY_TYPES = {"agent", "command", "instruction", "prompt", "skill"}
_VAGUE_DESCRIPTIONS = {"content", "todo", "tbd", "misc", "general", "helper"}


def audit_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic static audit checks over a persisted Agent Graph."""
    findings: list[dict[str, Any]] = []
    nodes = graph.get("nodes", {})
    assets = asset_entries(graph)
    findings.extend(_broken_references(graph, nodes))
    findings.extend(_duplicate_names(assets))
    findings.extend(_responsibility_overlap(assets))
    findings.extend(_path_scope_overlap(graph, nodes))
    findings.extend(_permission_conflicts(graph, nodes, assets))
    findings.extend(_unsafe_hooks(assets))
    findings.extend(_vague_descriptions(assets))
    findings.extend(_platform_drift(assets))
    findings.extend(authority_findings(graph, assets, nodes))
    findings.extend(_unused_skills(graph, assets))
    findings.extend(_unreachable_subagents(graph, assets))
    findings.extend(_commands_missing_agents(graph, nodes))
    findings.extend(_missing_mcp_config(nodes))
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


def _duplicate_names(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = _group_assets(assets, lambda item: (item["type"], _norm(item["name"])))
    return [
        _finding(
            "duplicate_name",
            "Duplicate asset name",
            f"Multiple {items[0]['type']} assets share name {items[0]['name']!r}.",
            items,
        )
        for items in groups.values()
        if len(items) > 1
    ]


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


def _unused_skills(graph: dict[str, Any], assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used = {
        edge.get("to")
        for edge in graph.get("edges", [])
        if edge.get("type") == "uses_skill"
    }
    return [
        _finding(
            "unused_skill",
            "Unused skill",
            "Skill has no incoming uses_skill references.",
            [asset],
            severity="info",
        )
        for asset in assets
        if asset["type"] == "skill" and asset["id"] not in used
    ]


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
