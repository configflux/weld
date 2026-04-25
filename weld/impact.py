"""Blast-radius analysis for graph nodes and file paths.

The ``impact()`` helper walks the graph in reverse from a target node (or all
nodes discovered from a file path) to find direct and transitive dependents.
It then summarizes which public surfaces are affected so agents can answer
"what breaks if I change this?" with a stable JSON contract.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import sys
from collections import deque
from pathlib import Path

from weld.graph import Graph

IMPACT_VERSION = 1
_API_ENDPOINT_TYPES = frozenset(["route", "rpc", "channel"])
_ENTRYPOINT_FILES = frozenset(["weld/cli.py", "weld/__main__.py"])
_COMMAND_MODULE_EXCLUSIONS = frozenset(["cli", "graph", "__main__"])


def _edge_key(edge: dict) -> str:
    props = json.dumps(edge.get("props", {}), sort_keys=True, ensure_ascii=True)
    return f"{edge['from']}|{edge['to']}|{edge['type']}|{props}"


def _normalize_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip()
    if not cleaned:
        return ""
    normalized = posixpath.normpath(cleaned)
    return "" if normalized == "." else normalized


def _resolve_target_nodes(graph: Graph, target: str) -> tuple[str, list[str]]:
    data = graph.dump()
    nodes: dict[str, dict] = data.get("nodes", {})
    if target in nodes:
        return "node", [target]

    normalized = _normalize_path(target)
    matches: set[str] = set()
    file_node_id = f"file:{normalized}"
    if file_node_id in nodes:
        matches.add(file_node_id)

    for node_id, node in nodes.items():
        props = node.get("props") or {}
        if _normalize_path(str(props.get("file", ""))) == normalized:
            matches.add(node_id)

    return "path", sorted(matches)


def _reverse_bfs(
    graph: Graph,
    seed_ids: list[str],
    depth: int,
) -> tuple[dict[str, int], list[dict]]:
    data = graph.dump()
    reverse_adj: dict[str, list[dict]] = {}
    for edge in data.get("edges", []):
        reverse_adj.setdefault(edge["to"], []).append(edge)

    seen: set[str] = set(seed_ids)
    dependents: dict[str, int] = {}
    edges: list[dict] = []
    seen_edges: set[str] = set()
    queue: deque[tuple[str, int]] = deque((node_id, 0) for node_id in seed_ids)

    while queue:
        current, hop = queue.popleft()
        if hop >= depth:
            continue
        for edge in reverse_adj.get(current, []):
            edge_id = _edge_key(edge)
            if edge_id not in seen_edges:
                seen_edges.add(edge_id)
                edges.append(edge)
            src = edge["from"]
            if src in seen:
                continue
            next_hop = hop + 1
            seen.add(src)
            dependents[src] = next_hop
            queue.append((src, next_hop))

    return dependents, edges


def _node_with_hop(graph: Graph, node_id: str, hop: int) -> dict:
    node = graph.get_node(node_id)
    if node is None:
        return {"id": node_id, "hop": hop}
    return {**node, "hop": hop}


def _surface_bucket(node: dict) -> str | None:
    node_type = node.get("type")
    if node_type == "command":
        return "cli_commands"
    if node_type == "tool":
        return "mcp_tools"
    if node_type in _API_ENDPOINT_TYPES:
        return "api_endpoints"
    if node_type == "entrypoint":
        return "entrypoints"
    if node_type == "boundary":
        return "boundaries"
    return None


def _derived_cli_command(node: dict) -> dict | None:
    props = node.get("props") or {}
    file_path = _normalize_path(str(props.get("file", "")))
    qualname = props.get("qualname")
    if not file_path.startswith("weld/") or not file_path.endswith(".py"):
        return None
    if qualname != "main":
        return None
    module_name = Path(file_path).stem
    if module_name in _COMMAND_MODULE_EXCLUSIONS:
        return None
    return {
        "id": f"command:wd {module_name}",
        "type": "command",
        "label": f"wd {module_name}",
        "props": {"derived_from": node["id"], "file": file_path},
        "hop": node.get("hop", 0),
    }


def _derived_entrypoint(node: dict) -> dict | None:
    props = node.get("props") or {}
    file_path = _normalize_path(str(props.get("file", "")))
    qualname = props.get("qualname")
    if file_path in _ENTRYPOINT_FILES and qualname == "main":
        return {
            "id": "entrypoint:wd",
            "type": "entrypoint",
            "label": "wd entrypoint",
            "props": {"derived_from": node["id"], "file": file_path},
            "hop": node.get("hop", 0),
        }
    return None


def _derived_mcp_tool(node: dict) -> dict | None:
    props = node.get("props") or {}
    file_path = _normalize_path(str(props.get("file", "")))
    qualname = str(props.get("qualname", ""))
    if file_path not in {"weld/mcp_helpers.py", "weld/mcp_server.py"}:
        return None
    if not qualname.startswith("weld_"):
        return None
    return {
        "id": f"tool:{qualname}",
        "type": "tool",
        "label": qualname,
        "props": {"derived_from": node["id"], "file": file_path},
        "hop": node.get("hop", 0),
    }


def _derived_surfaces(node: dict) -> list[dict]:
    surfaces: list[dict] = []
    for derived in (
        _derived_cli_command(node),
        _derived_entrypoint(node),
        _derived_mcp_tool(node),
    ):
        if derived is not None:
            surfaces.append(derived)
    return surfaces


def _collect_surfaces(nodes: list[dict]) -> dict[str, list[dict]]:
    surfaces = {
        "cli_commands": [],
        "mcp_tools": [],
        "api_endpoints": [],
        "entrypoints": [],
        "boundaries": [],
    }
    seen: dict[str, set[str]] = {key: set() for key in surfaces}
    for node in nodes:
        expanded = [node, *_derived_surfaces(node)]
        for candidate in expanded:
            bucket = _surface_bucket(candidate)
            if bucket is None:
                continue
            if candidate["id"] in seen[bucket]:
                continue
            seen[bucket].add(candidate["id"])
            surfaces[bucket].append(candidate)
    return surfaces


def _validated_depth(depth: int) -> int:
    if depth < 0:
        raise ValueError("depth must be >= 0")
    return depth


def _parse_depth(raw: str) -> int:
    try:
        return _validated_depth(int(raw))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _seed_nodes(graph: Graph, seed_ids: list[str]) -> list[dict]:
    seeds: list[dict] = []
    for seed_id in seed_ids:
        node = graph.get_node(seed_id)
        if node is not None:
            seeds.append({**node, "hop": 0})
    return seeds


def _risk_level(surfaces: dict[str, list[dict]]) -> str:
    if surfaces["api_endpoints"] or surfaces["entrypoints"] or surfaces["boundaries"]:
        return "HIGH"
    if surfaces["cli_commands"] or surfaces["mcp_tools"]:
        return "MEDIUM"
    return "LOW"


def impact(graph: Graph, *, target: str, depth: int = 3) -> dict:
    """Return the reverse-dependency blast radius for *target*."""
    depth = _validated_depth(depth)
    target_kind, seed_ids = _resolve_target_nodes(graph, target)
    result = {
        "impact_version": IMPACT_VERSION,
        "target": {
            "input": target,
            "kind": target_kind,
            "resolved_nodes": seed_ids,
        },
        "depth": depth,
        "direct_dependents": [],
        "transitive_dependents": [],
        "affected_surfaces": {
            "cli_commands": [],
            "mcp_tools": [],
            "api_endpoints": [],
            "entrypoints": [],
            "boundaries": [],
        },
        "risk_level": "LOW",
        "edges": [],
        "warnings": [],
    }
    if not seed_ids:
        result["warnings"].append(f"no nodes matched target: {target}")
        return result

    dependents, edges = _reverse_bfs(graph, seed_ids, depth)
    nodes = [
        _node_with_hop(graph, node_id, hop)
        for node_id, hop in sorted(dependents.items(), key=lambda item: (item[1], item[0]))
    ]
    direct = [node for node in nodes if node["hop"] == 1]
    transitive = [node for node in nodes if node["hop"] > 1]
    surfaces = _collect_surfaces([*_seed_nodes(graph, seed_ids), *nodes])

    result["direct_dependents"] = direct
    result["transitive_dependents"] = transitive
    result["affected_surfaces"] = surfaces
    result["risk_level"] = _risk_level(surfaces)
    result["edges"] = edges
    return result


def format_human(result: dict) -> str:
    """Render an impact result as a short human-readable summary."""
    target = result["target"]["input"]
    lines = [
        f"Target: {target}",
        f"Resolved nodes: {len(result['target']['resolved_nodes'])}",
        f"Risk: {result['risk_level']}",
        f"Direct dependents: {len(result['direct_dependents'])}",
        f"Transitive dependents: {len(result['transitive_dependents'])}",
    ]
    surfaces = result["affected_surfaces"]
    if any(surfaces.values()):
        lines.append("Affected surfaces:")
        lines.append(f"- CLI commands: {len(surfaces['cli_commands'])}")
        lines.append(f"- MCP tools: {len(surfaces['mcp_tools'])}")
        lines.append(f"- API endpoints: {len(surfaces['api_endpoints'])}")
        lines.append(f"- Entry points: {len(surfaces['entrypoints'])}")
        lines.append(f"- Boundaries: {len(surfaces['boundaries'])}")
    for warning in result.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd impact``."""
    parser = argparse.ArgumentParser(
        prog="wd impact",
        description="Reverse-dependency blast radius for a node id or file path.",
    )
    parser.add_argument("target", help="Node id or repo-relative file path")
    parser.add_argument(
        "--depth",
        type=_parse_depth,
        default=3,
        help="Maximum reverse traversal depth (default: 3)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the stable JSON envelope instead of human-readable text",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root containing .weld/graph.json",
    )
    args = parser.parse_args(argv)

    from weld._graph_cli import _build_retry_hint, ensure_graph_exists

    # Surface a friendly first-run message when the graph has not been
    # built yet; mirrors the behaviour of read commands in _graph_cli
    # (bd-5038-3nr.2 / bd-5038-uqo).
    ensure_graph_exists(args.root, _build_retry_hint("impact", args.target))

    graph = Graph(args.root)
    graph.load()
    result = impact(graph, target=args.target, depth=args.depth)
    if args.json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_human(result))
    return 0
