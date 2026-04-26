"""CLI commands for the static Weld Agent Graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from weld.agent_graph_audit import audit_graph
from weld.agent_graph_discovery import discover_agent_graph
from weld.agent_graph_inventory import asset_entries, explain_asset, impact_asset
from weld.agent_graph_plan import plan_change
from weld.agent_graph_render import (
    print_audit,
    print_change_plan,
    print_explanation,
    print_impact,
)
from weld.agent_graph_render_cli import add_render_parser, run_render
from weld.agent_graph_storage import (
    AgentGraphNotFoundError,
    agent_graph_path,
    load_agent_graph,
    write_agent_graph,
)
from weld.serializer import dumps_graph


def main(argv: list[str] | None = None) -> int:
    """Run ``wd agents`` subcommands."""
    parser = argparse.ArgumentParser(
        prog="wd agents",
        description=(
            "Discover, persist, inspect, audit, and plan changes for static "
            "AI customization assets."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_discover_parser(
        subparsers,
        "discover",
        "Scan known AI customization files and write .weld/agent-graph.json.",
    )
    _add_discover_parser(
        subparsers,
        "rediscover",
        "Refresh .weld/agent-graph.json from a new static Agent Graph scan.",
    )
    _add_list_parser(subparsers)
    _add_explain_parser(subparsers)
    _add_impact_parser(subparsers)
    _add_audit_parser(subparsers)
    _add_plan_change_parser(subparsers)
    add_render_parser(subparsers)
    args = parser.parse_args(argv)
    if args.command in {"discover", "rediscover"}:
        return _run_discover(args)
    if args.command == "list":
        return _run_list(args)
    if args.command == "explain":
        return _run_explain(args)
    if args.command == "impact":
        return _run_impact(args)
    if args.command == "audit":
        return _run_audit(args)
    if args.command == "plan-change":
        return _run_plan_change(args)
    if args.command == "render":
        return run_render(args)
    parser.error(f"unknown agents command: {args.command}")
    return 2


def _add_discover_parser(
    subparsers: Any,
    name: str,
    help_text: str,
) -> None:
    parser = subparsers.add_parser(name, help=help_text, description=help_text)
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to scan (default: current directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full Agent Graph JSON to stdout.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Scan without writing .weld/agent-graph.json.",
    )


def _run_discover(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    graph = discover_agent_graph(root)
    output_path: Path | None = None
    if not args.no_write:
        output_path = write_agent_graph(root, graph)
    if args.json:
        sys.stdout.write(dumps_graph(graph))
        return 0
    _print_discover_summary(graph, output_path, root=root, no_write=args.no_write)
    return 0


def _add_list_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "list",
        help="List persisted Agent Graph assets.",
        description="List discovered AI customization assets from .weld/agent-graph.json.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root containing .weld/agent-graph.json (default: current directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a stable JSON inventory.",
    )
    parser.add_argument(
        "--type",
        dest="type_filter",
        default=None,
        help="Only show assets with this canonical type.",
    )
    parser.add_argument(
        "--platform",
        dest="platform_filter",
        default=None,
        help="Only show assets from this source platform.",
    )


def _run_list(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    graph = _load_persisted_graph(root)
    if graph is None:
        return 2

    entries = asset_entries(
        graph,
        type_filter=args.type_filter,
        platform_filter=args.platform_filter,
    )
    if args.json:
        payload = {
            "assets": entries,
            "count": len(entries),
            "filters": {
                "platform": args.platform_filter,
                "type": args.type_filter,
            },
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0
    _print_asset_list(entries)
    return 0


def _add_explain_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "explain",
        help="Explain one discovered AI customization asset.",
        description=(
            "Explain one persisted Agent Graph asset by name, node ID, "
            "or source path."
        ),
    )
    parser.add_argument("asset", help="Asset name, node ID, or path to explain.")
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root containing .weld/agent-graph.json (default: current directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a stable JSON explanation.",
    )


def _run_explain(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    graph = _load_persisted_graph(root)
    if graph is None:
        return 2

    query = _query_relative_to_root(args.asset, root)
    explanation = explain_asset(graph, query)
    if explanation is None:
        print(f"Agent Graph asset not found: {args.asset}", file=sys.stderr)
        return 2
    if args.json:
        sys.stdout.write(json.dumps(explanation, indent=2, sort_keys=True) + "\n")
        return 0
    print_explanation(explanation)
    return 0


def _add_impact_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "impact",
        help="Show affected Agent Graph assets for a proposed change.",
        description=(
            "Show impact for one persisted Agent Graph asset by name, "
            "node ID, or source path."
        ),
    )
    parser.add_argument("asset", help="Asset name, node ID, or path to analyze.")
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root containing .weld/agent-graph.json (default: current directory).",
    )
    parser.add_argument("--json", action="store_true", help="Emit stable JSON impact.")


def _run_impact(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    graph = _load_persisted_graph(root)
    if graph is None:
        return 2
    query = _query_relative_to_root(args.asset, root)
    impact = impact_asset(graph, query)
    if impact is None:
        print(f"Agent Graph asset not found: {args.asset}", file=sys.stderr)
        return 2
    if args.json:
        sys.stdout.write(json.dumps(impact, indent=2, sort_keys=True) + "\n")
        return 0
    print_impact(impact)
    return 0


def _add_audit_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "audit",
        help="Audit the persisted Agent Graph for static consistency issues.",
        description="Audit .weld/agent-graph.json for static consistency issues.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root containing .weld/agent-graph.json (default: current directory).",
    )
    parser.add_argument("--json", action="store_true", help="Emit stable JSON findings.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Surface canonical+rendered groups silenced by ADR 0029 as "
            "info-level findings (codes suffixed `_suppressed`)."
        ),
    )


def _run_audit(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    graph = _load_persisted_graph(root)
    if graph is None:
        return 2
    payload = audit_graph(graph, root=root, strict=args.strict)
    if args.json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0
    print_audit(payload)
    return 0


def _add_plan_change_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "plan-change",
        help="Plan a static AI customization behavior change.",
        description=(
            "Plan a static AI customization behavior change using "
            ".weld/agent-graph.json."
        ),
    )
    parser.add_argument("request", help="Natural-language change request to plan.")
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root containing .weld/agent-graph.json (default: current directory).",
    )
    parser.add_argument("--json", action="store_true", help="Emit stable JSON plan.")


def _run_plan_change(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    graph = _load_persisted_graph(root)
    if graph is None:
        return 2
    payload = plan_change(graph, args.request)
    if args.json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0
    print_change_plan(payload)
    return 0


def _load_persisted_graph(root: Path) -> dict[str, Any] | None:
    try:
        return load_agent_graph(root)
    except AgentGraphNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return None


def _query_relative_to_root(raw: str, root: Path) -> str:
    path = Path(raw)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return raw
    return raw[2:] if raw.startswith("./") else raw


def _print_asset_list(entries: list[dict[str, Any]]) -> None:
    if not entries:
        print("No Agent Graph assets found.")
        return

    current_platform: str | None = None
    for entry in entries:
        if entry["platform_name"] != current_platform:
            if current_platform is not None:
                print()
            current_platform = entry["platform_name"]
            print(current_platform)
        print(_format_asset_row(entry))


def _format_asset_row(entry: dict[str, Any]) -> str:
    description = entry["description"]
    suffix = f" - {description}" if description else ""
    return (
        f"  {entry['type']:<12} {entry['name']:<24} "
        f"{entry['path']} [{entry['status']}]{suffix}"
    ).rstrip()


def _print_discover_summary(
    graph: dict[str, Any],
    output_path: Path | None,
    *,
    root: Path,
    no_write: bool,
) -> None:
    meta = graph.get("meta", {})
    diagnostics = meta.get("diagnostics") or []
    discovered_from = meta.get("discovered_from") or []
    print("Agent Graph discovery")
    print(f"Root: {_display_path(root, root)}")
    print(f"Assets: {len(discovered_from)}")
    print(f"Nodes: {len(graph.get('nodes', {}))}")
    print(f"Edges: {len(graph.get('edges', []))}")
    print(f"Diagnostics: {len(diagnostics)}")
    if no_write:
        print("Write: skipped (--no-write)")
    else:
        path = output_path or agent_graph_path(root)
        print(f"Write: {_display_path(path, root)}")


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() or "."
    except ValueError:
        return path.as_posix()
