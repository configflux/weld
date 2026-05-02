"""CLI entry point for the connected structure graph commands.

Houses the argparse-based ``main()`` dispatcher and small helpers used only
by the CLI path (``_deep_merge``, ``_out``).  Extracted from ``weld.graph``
to keep the core ``Graph`` class under the 400-line default.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from weld._graph_cli_parser import build_parser
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


def _emit(args, data: object, renderer) -> None:
    """Write *data* as JSON when ``--json`` is set, else rendered text (ADR 0040)."""
    if getattr(args, "as_json", False):
        _out(data)
        return
    sys.stdout.write(renderer(data))


# Graph-backed read commands. A missing `.weld/graph.json` here yields an
# actionable first-run message instead of a silently empty payload. Mutating
# commands (add-*/rm-*/import/touch) and diagnostic commands
# (stale/stats/dump/list/validate*) are intentionally excluded. ``find`` is
# also excluded because it reads the file-index, not the graph -- users can
# run ``wd build-index`` + ``wd find`` without ever producing a graph.
_READ_COMMANDS = frozenset(
    {"query", "context", "path", "callers", "references", "communities"}
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
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(1)
    cmd = args.command
    if cmd in {"query", "context", "path"}:
        from weld.workspace_state import load_workspace_config

        if load_workspace_config(args.root) is not None:
            from weld.federation import FederatedGraph
            from weld._cli_render import (
                render_context, render_path, render_query,
            )

            fg = FederatedGraph(args.root)
            if cmd == "query":
                _emit(args, fg.query(args.term, args.limit), render_query)
            elif cmd == "context":
                _emit(args, fg.context(args.node_id), render_context)
            else:
                _emit(args, fg.path(args.from_id, args.to_id), render_path)
            return
    if cmd in _READ_COMMANDS:
        # Single-repo read path: surface a friendly first-run message when
        # the graph has not been built yet (tracked issue).
        ensure_graph_exists(args.root, _retry_hint(cmd, args))
    g = Graph(args.root)
    g.load()
    mutates = False
    if cmd == "find":
        from weld._cli_render import render_find
        from weld.file_index import find_files, load_file_index
        index = load_file_index(args.root)
        _emit(args, find_files(index, args.term, limit=args.limit), render_find)
    elif cmd == "query":
        from weld._cli_render import render_query
        _emit(args, g.query(args.term, args.limit), render_query)
    elif cmd == "context":
        from weld._cli_render import render_context
        _emit(args, g.context(args.node_id), render_context)
    elif cmd == "callers":
        from weld._cli_render import render_callers
        _emit(args, g.callers(args.symbol, depth=args.depth), render_callers)
    elif cmd == "references":
        from weld._cli_render import render_references
        from weld.file_index import find_files, load_file_index
        index = load_file_index(args.root)
        refs = g.references(args.name)
        refs["files"] = find_files(index, args.name).get("files", [])
        _emit(args, refs, render_references)
    elif cmd == "path":
        from weld._cli_render import render_path
        _emit(args, g.path(args.from_id, args.to_id), render_path)
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
        from weld._cli_render import render_stale
        _emit(args, g.stale(), render_stale)
    elif cmd == "touch":
        g.save(touch_git_sha=True)
        _out({
            "git_sha": g.dump().get("meta", {}).get("git_sha"),
            "updated_at": g.dump().get("meta", {}).get("updated_at"),
        })
    elif cmd == "dump":
        _out(g.dump())
    elif cmd == "stats":
        from weld._cli_render import render_stats
        from weld._graph_stats_cli import build_stats_payload
        _emit(args, build_stats_payload(args.root, g, top=args.top), render_stats)
    elif cmd == "communities":
        from weld.graph_communities_cli import run_graph_communities
        run_graph_communities(args, g)
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
