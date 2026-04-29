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


def _positive_int(value: str) -> int:
    """argparse validator: accept only strictly-positive integers.

    ``wd stats --top 0`` or a negative value would silently return an
    empty top-authority list, which is worse than a clear error message.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer, got {value!r}",
        ) from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer, got {parsed}",
        )
    return parsed


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


# Graph-backed read commands. A missing `.weld/graph.json` here yields an
# actionable first-run message instead of a silently empty payload. Mutating
# commands (add-*/rm-*/import/touch) and diagnostic commands
# (stale/stats/dump/list/validate*) are intentionally excluded. ``find`` is
# also excluded because it reads the file-index, not the graph -- users can
# run ``wd build-index`` + ``wd find`` without ever producing a graph.
_READ_COMMANDS = frozenset(
    {"query", "context", "path", "callers", "references"}
)


def _build_retry_hint(cmd: str, *positional: str, **flags: str) -> str:
    """Format a copy-paste ``wd <cmd> ...`` retry hint.

    Centralizes the quote/flag pattern so call sites (here in
    :func:`_retry_hint` plus the inline hints in ``brief`` / ``trace`` /
    ``impact`` / ``diff`` / ``enrich``) all produce the same shape.

    - Positional args are quoted in order:
      ``_build_retry_hint("path", "a:b", "c:d")`` -> ``wd path "a:b" "c:d"``.
    - Keyword flags become ``--flag "value"`` (underscores in the keyword
      become dashes):
      ``_build_retry_hint("enrich", node="entity:Store")`` ->
      ``wd enrich --node "entity:Store"``.
    - With no extra args: ``_build_retry_hint("diff")`` -> ``wd diff``.
    """
    parts = [f"wd {cmd}"]
    for value in positional:
        parts.append(f'"{value}"')
    for flag, value in flags.items():
        parts.append(f'--{flag.replace("_", "-")} "{value}"')
    return " ".join(parts)


def _retry_hint(cmd: str, args) -> str:
    """Format a copy-paste retry command for the guidance block."""
    if cmd == "path":
        return _build_retry_hint("path", args.from_id, args.to_id)
    if cmd == "context":
        return _build_retry_hint("context", args.node_id)
    if cmd == "callers":
        return _build_retry_hint("callers", args.symbol)
    if cmd == "references":
        return _build_retry_hint("references", args.name)
    # query / brief take a bare term.
    term = getattr(args, "term", None) or "<term>"
    return _build_retry_hint(cmd, term)


def missing_graph_message(retry_cmd: str) -> str:
    """Return the friendly missing-graph guidance block (tracked issue / -uqo).

    Used by graph-backed read commands (``wd brief`` / ``query`` /
    ``context`` / ``path`` / ``callers`` / ``references`` / ``trace`` /
    ``impact`` / ``diff`` / ``enrich``) when ``.weld/graph.json`` has not
    yet been produced. ``wd find`` is intentionally exempt -- it reads the
    file-index, not the graph. Keep the wording stable -- onboarding docs
    and tests match against its substrings.
    """
    return (
        "No Weld graph found.\n"
        "Run: wd init (if no config), then wd discover.\n"
        f'Then retry: {retry_cmd}.'
    )


def ensure_graph_exists(root: Path, retry_cmd: str) -> None:
    """Exit with an actionable message when ``.weld/graph.json`` is missing.

    This is a no-op when the graph file is present (even if empty). Callers
    should invoke this *before* constructing a :class:`~weld.graph.Graph` so
    first-run users get guidance instead of an empty-payload success.
    """
    graph_path = Path(root) / ".weld" / "graph.json"
    if graph_path.exists():
        return
    sys.stderr.write(missing_graph_message(retry_cmd) + "\n")
    sys.exit(1)


def main(argv: list[str] | None = None, *, prog: str = "wd") -> None:  # noqa: C901
    parser = argparse.ArgumentParser(prog=prog, description="Connected structure CLI")
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
    p_ae = sub.add_parser(
        "add-edge",
        help=(
            "Add an edge. Use --props for provenance, e.g. --props "
            "'{\"source\":\"llm\"}'. (Replaces 0.3.0 --source/--relation "
            "flags.)"
        ),
    )
    p_ae.add_argument("from_id", help="Source node ID")
    p_ae.add_argument("to_id", help="Target node ID")
    p_ae.add_argument("--type", required=True, dest="edge_type",
                       choices=sorted(VALID_EDGE_TYPES), help="Edge type")
    p_ae.add_argument(
        "--props",
        default="{}",
        help=(
            "JSON properties, e.g. '{\"source\":\"llm\","
            "\"confidence\":\"inferred\"}'. Use props.source to record "
            "provenance for tool-generated edges."
        ),
    )
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
    p_find.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of file entries to return (default 20, mirrors wd query)",
    )
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
    sub.add_parser(
        "touch",
        help=(
            "Stamp meta.git_sha=HEAD + meta.updated_at=now without "
            "mutating nodes/edges (use after enrichment-only commits)."
        ),
    )
    sub.add_parser("dump", help="Full graph JSON")
    p_stats = sub.add_parser("stats", help="Summary counts")
    p_stats.add_argument(
        "--top",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Cap on top_authority_nodes list (default: 5).",
    )
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
    if cmd in _READ_COMMANDS:
        # Single-repo read path: surface a friendly first-run message when
        # the graph has not been built yet (tracked issue).
        ensure_graph_exists(args.root, _retry_hint(cmd, args))
    g = Graph(args.root)
    g.load()
    mutates = False
    if cmd == "find":
        from weld.file_index import find_files, load_file_index
        index = load_file_index(args.root)
        _out(find_files(index, args.term, limit=args.limit))
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
    elif cmd == "touch":
        g.save(touch_git_sha=True)
        _out({
            "git_sha": g.dump().get("meta", {}).get("git_sha"),
            "updated_at": g.dump().get("meta", {}).get("updated_at"),
        })
    elif cmd == "dump":
        _out(g.dump())
    elif cmd == "stats":
        from weld._graph_stats_cli import build_stats_payload
        _out(build_stats_payload(args.root, g, top=args.top))
    elif cmd == "import":
        raw = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
        data = json.loads(raw)
        from weld.trace_contract import trace_contract_warnings

        warnings = trace_contract_warnings(data)
        result = g.merge_import(data)
        if warnings:
            result["warnings"] = warnings
            for warning in warnings:
                print(f"[weld] warning: {warning}", file=sys.stderr)
        _out(result)
        mutates = True
    elif cmd == "validate":
        from weld._validate_diagnostics import format_validation_report
        from weld.contract import validate_graph
        errs = validate_graph(g.dump())
        _out({"valid": not errs, "errors": [str(e) for e in errs]})
        if errs:
            graph_path = Path(args.root) / ".weld" / "graph.json"
            sys.stderr.write(format_validation_report(
                errs, source=str(graph_path),
            ))
            sys.exit(1)
    elif cmd == "validate-fragment":
        from weld._validate_diagnostics import format_validation_report
        from weld.contract import validate_fragment
        raw = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
        data = json.loads(raw)
        errs = validate_fragment(
            data,
            source_label=args.source_label,
            allow_dangling_edges=args.allow_dangling,
        )
        warnings = []
        if not errs:
            from weld.trace_contract import trace_contract_warnings

            warnings = trace_contract_warnings(data)
        _out({
            "valid": not errs,
            "errors": [str(e) for e in errs],
            "warnings": warnings,
        })
        for warning in warnings:
            print(f"[weld] warning: {warning}", file=sys.stderr)
        if errs:
            source = "<stdin>" if str(args.file) == "-" else str(args.file)
            sys.stderr.write(format_validation_report(errs, source=source))
            sys.exit(1)
    if mutates:
        # Mutating CLI paths implicitly advance meta.git_sha to HEAD so
        # enrichment-only commits do not trigger [stale] false positives
        # (ADR 0017). Outside a git repo this is a silent no-op.
        g.save(touch_git_sha=True)
