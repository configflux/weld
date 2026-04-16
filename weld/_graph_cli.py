"""CLI entry point for the connected structure graph commands.

Houses the argparse-based ``main()`` dispatcher and small helpers used only
by the CLI path (``_deep_merge``, ``_out``).  Extracted from ``weld.graph``
to keep the core ``Graph`` class under the 400-line default.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from weld.contract import VALID_EDGE_TYPES, VALID_NODE_TYPES
from weld.graph import Graph


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _out(data: object) -> None:
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> None:  # noqa: C901
    parser = argparse.ArgumentParser(prog="wd", description="Connected structure CLI")
    parser.add_argument("--root", type=Path, default=Path("."), help="Project root directory")
    sub = parser.add_subparsers(dest="command")
    p_query = sub.add_parser("query", help="Tokenized search (fields: id, label, props.file, props.exports, props.description)")
    p_query.add_argument("term", help="Search term (multi-word is tokenized)")
    p_query.add_argument("--limit", type=int, default=20)
    p_ctx = sub.add_parser("context", help="Node + neighborhood")
    p_ctx.add_argument("node_id", help="Node ID")
    p_path = sub.add_parser("path", help="Shortest path between nodes")
    p_path.add_argument("from_id", help="Start node ID")
    p_path.add_argument("to_id", help="End node ID")
    p_an = sub.add_parser("add-node", help="Add or update a node")
    p_an.add_argument("id", help="Node ID (e.g. entity:Store)")
    p_an.add_argument("--type", required=True, dest="node_type",
                       choices=sorted(VALID_NODE_TYPES), help="Node type")
    p_an.add_argument("--label", default="", help="Human-readable label")
    p_an.add_argument("--props", default="{}", help="JSON properties")
    p_an.add_argument("--merge", action="store_true", help="Deep-merge props into existing node")
    p_ae = sub.add_parser("add-edge", help="Add an edge")
    p_ae.add_argument("from_id", help="Source node ID")
    p_ae.add_argument("to_id", help="Target node ID")
    p_ae.add_argument("--type", required=True, dest="edge_type",
                       choices=sorted(VALID_EDGE_TYPES), help="Edge type")
    p_ae.add_argument("--props", default="{}", help="JSON properties")
    p_rn = sub.add_parser("rm-node", help="Remove a node and its edges")
    p_rn.add_argument("id", help="Node ID")
    p_re = sub.add_parser("rm-edge", help="Remove edge(s)")
    p_re.add_argument("from_id", help="Source node ID")
    p_re.add_argument("to_id", help="Target node ID")
    p_re.add_argument("--type", dest="edge_type", choices=sorted(VALID_EDGE_TYPES),
                       default=None, help="Edge type filter")
    p_list = sub.add_parser("list", help="List nodes")
    p_list.add_argument("--type", dest="type_filter", choices=sorted(VALID_NODE_TYPES),
                         default=None, help="Filter by type")
    p_find = sub.add_parser("find", help="Search file index by keyword")
    p_find.add_argument("term", help="Search term (substring match)")
    p_callers = sub.add_parser(
        "callers", help="Direct (and optionally transitive) callers of a symbol"
    )
    p_callers.add_argument("symbol", help="Full symbol id, e.g. symbol:py:weld.discover:_load_strategy")
    p_callers.add_argument("--depth", type=int, default=1, help="Caller traversal depth (default 1)")
    p_refs = sub.add_parser(
        "references",
        help="Callers + textual file-index references for a symbol name",
    )
    p_refs.add_argument("name", help="Bare symbol name, e.g. _load_strategy")
    sub.add_parser("stale", help="Check if graph is stale vs current HEAD")
    sub.add_parser("dump", help="Full graph JSON")
    sub.add_parser("stats", help="Summary counts")
    p_imp = sub.add_parser("import", help="Import/merge from file")
    p_imp.add_argument("file", type=Path, help="JSON file to import")
    sub.add_parser("validate", help="Validate graph against the metadata contract")
    p_vf = sub.add_parser("validate-fragment", help="Validate a JSON fragment")
    p_vf.add_argument("file", type=Path, help="JSON fragment file")
    p_vf.add_argument("--source-label", default="fragment", help="Diagnostic label")
    p_vf.add_argument("--allow-dangling", action="store_true", help="Skip ref checks")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(1)
    cmd = args.command
    if cmd in {"query", "context", "path"}:
        from weld.workspace_state import load_workspace_config

        if load_workspace_config(args.root) is not None:
            from weld.federation import FederatedGraph

            fg = FederatedGraph(args.root)
            if cmd == "query":
                _out(fg.query(args.term, args.limit))
            elif cmd == "context":
                _out(fg.context(args.node_id))
            else:
                _out(fg.path(args.from_id, args.to_id))
            return
    g = Graph(args.root)
    g.load()
    mutates = False
    if cmd == "find":
        from weld.file_index import find_files, load_file_index
        index = load_file_index(args.root)
        _out(find_files(index, args.term))
    elif cmd == "query":
        _out(g.query(args.term, args.limit))
    elif cmd == "context":
        _out(g.context(args.node_id))
    elif cmd == "callers":
        _out(g.callers(args.symbol, depth=args.depth))
    elif cmd == "references":
        from weld.file_index import find_files, load_file_index
        index = load_file_index(args.root)
        refs = g.references(args.name)
        refs["files"] = find_files(index, args.name).get("files", [])
        _out(refs)
    elif cmd == "path":
        _out(g.path(args.from_id, args.to_id))
    elif cmd == "add-node":
        props = json.loads(args.props)
        if args.merge:
            existing = g.get_node(args.id)
            if existing:
                merged = _deep_merge(existing.get("props", {}), props)
                label = args.label or existing.get("label", args.id)
                _out(g.add_node(args.id, args.node_type, label, merged))
            else:
                _out(g.add_node(args.id, args.node_type, args.label or args.id, props))
        else:
            _out(g.add_node(args.id, args.node_type, args.label or args.id, props))
        mutates = True
    elif cmd == "add-edge":
        props = json.loads(args.props)
        _out(g.add_edge(args.from_id, args.to_id, args.edge_type, props))
        mutates = True
    elif cmd == "rm-node":
        removed = g.rm_node(args.id)
        _out({"removed": removed, "id": args.id})
        mutates = True
    elif cmd == "rm-edge":
        count = g.rm_edge(args.from_id, args.to_id, args.edge_type)
        _out({"removed_count": count})
        mutates = True
    elif cmd == "list":
        _out(g.list_nodes(args.type_filter))
    elif cmd == "stale":
        _out(g.stale())
    elif cmd == "dump":
        _out(g.dump())
    elif cmd == "stats":
        _out(g.stats())
    elif cmd == "import":
        raw = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
        data = json.loads(raw)
        result = g.merge_import(data)
        _out(result)
        mutates = True
    elif cmd == "validate":
        from weld.contract import validate_graph
        errs = validate_graph(g.dump())
        _out({"valid": not errs, "errors": [str(e) for e in errs]})
        if errs:
            sys.exit(1)
    elif cmd == "validate-fragment":
        from weld.contract import validate_fragment
        raw = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
        data = json.loads(raw)
        errs = validate_fragment(
            data,
            source_label=args.source_label,
            allow_dangling_edges=args.allow_dangling,
        )
        _out({"valid": not errs, "errors": [str(e) for e in errs]})
        if errs:
            sys.exit(1)
    if mutates:
        g.save()
