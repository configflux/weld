"""Single-repo CLI dispatch for the graph commands.

Entered by :func:`weld._graph_cli.main` for every command that is not served
at a federated root: the graph-backed reads, the mutating ``add-*`` /
``rm-*`` / ``import`` / ``migrate`` / ``touch`` family, and the diagnostic
``stale`` / ``stats`` / ``dump`` / ``list`` / ``validate*`` commands.

The mirror image of :mod:`weld._graph_cli_federated`: one dispatcher per
graph shape, both driven by the same parsed ``args`` and both writing
through the shared chokepoint in :mod:`weld._graph_cli_emit`. Kept out of
``weld/_graph_cli.py`` so that module stays a parser-and-route entry point
within the 400-line cap.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from weld._graph_cli_emit import _emit, _emit_node_lookup, _out
from weld._notice import emit
from weld._query_surface import (
    apply_callers_envelope,
    apply_context_envelope,
    apply_references_envelope,
)
from weld._query_surface import apply_query_envelope as _query_envelope
from weld._safe_text import sanitize_terminal_text
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


def run_single_repo(cmd: str, args) -> None:  # noqa: C901 -- CLI dispatch chain
    from weld._graph_cli_errors import load_graph_or_exit
    g = load_graph_or_exit(Graph(args.root))
    mutates = False
    if cmd == "find":
        from weld._cli_render import render_find
        from weld._file_index_coverage import ensure_index_covers_surface
        from weld._find_precondition import ensure_file_index_exists
        from weld.file_index import find_files, load_file_index
        # ADR 0134: the precondition belongs to the artifact this command
        # reads. `find` needs no graph -- but an index that was never built
        # makes "no matches" a report on weld's own state, not on the term.
        ensure_file_index_exists(args.root, args.term)
        # bd yw4b: the graph's freshness signals cannot speak for this index
        # (its surface is the broader set), so ask the index itself whether it
        # still accounts for the tree before answering "no matches" from it.
        ensure_index_covers_surface(
            args.root, no_refresh=getattr(args, "no_refresh", False),
        )
        index = load_file_index(args.root)
        _emit(args, find_files(index, args.term, limit=args.limit), render_find)
    elif cmd == "query":
        from weld._cli_render import render_query
        _emit(
            args,
            _query_envelope(args, g.query(args.term, args.limit)),
            render_query,
        )
    elif cmd == "context":
        from weld._cli_render import render_context
        _emit_node_lookup(
            args,
            apply_context_envelope(args, g.context(args.node_id)),
            render_context,
        )
    elif cmd == "callers":
        from weld._cli_render import render_callers
        _emit_node_lookup(
            args,
            apply_callers_envelope(args, g.callers(args.symbol, depth=args.depth)),
            render_callers,
        )
    elif cmd == "references":
        from weld._cli_render import render_references
        from weld.file_index import find_files, load_file_index
        index = load_file_index(args.root)
        refs = g.references(args.name)
        refs["files"] = find_files(index, args.name).get("files", [])
        # bd hp6e: route through the node-lookup emitter, not the plain one, so
        # a node-id argument naming nothing exits non-zero like `callers` and
        # `context` already do. graph_referrers.references had produced the
        # "node not found" payload since bd nywd, but _emit discarded its
        # status, so the verb printed an error and exited 0. "No references" is
        # what a reader sees before deleting a symbol as dead or calling a
        # refactor safe; a typo, a stale pasted id, or a symbol that has since
        # MOVED must not render as "nothing uses this".
        _emit_node_lookup(args, apply_references_envelope(args, refs), render_references)
    elif cmd == "path":
        from weld._cli_render import render_path
        _emit(args, g.path(args.from_id, args.to_id), render_path)
    elif cmd == "add-node":
        from weld._graph_cli_errors import reject_invalid_enrichment
        incoming = json.loads(args.props)
        props = incoming
        label = args.label or args.id
        existing = g.get_node(args.id) if args.merge else None
        if existing:
            props = _deep_merge(existing.get("props", {}), incoming)
            label = args.label or existing.get("label", args.id)
        # ADR 0097: judge the enrichment record before mutating anything.
        reject_invalid_enrichment(args.id, incoming, props)
        _out(g.add_node(args.id, args.node_type, label, props))
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
        from weld._stale_payload import stale_payload
        # ADR 0066 §2: at a federated root, fold per-child staleness into
        # the root payload; a single repo returns g.stale() unchanged.
        payload = stale_payload(args.root, g.stale())
        _emit(args, payload, render_stale)
        # ADR 0110: --check turns the report into a gate. The verdict is
        # the payload's own top-level ``stale`` and nothing re-derived
        # here -- it already aliases ``source_stale`` (which ADR 0101
        # folds ``coverage_stale`` into) at a single repo, and already
        # ORs in child drift at a federated root. Printing first means a
        # failing CI job shows *why* it failed.
        if getattr(args, "check", False) and payload.get("stale"):
            sys.exit(1)
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
                emit(f"[weld] warning: {warning}")
        _out(result)
        mutates = True
    elif cmd == "validate":
        from weld._federation_ids import federation_id_index_for_root
        from weld._validate_diagnostics import format_validation_report
        from weld.contract import validate_graph
        # ADR 0137 ss3: at a workspace root, an endpoint pointing into a child
        # is checked against that child's ids rather than waved through on the
        # shape of the id. ``validate`` deliberately stays out of
        # FEDERATED_CLI_COMMANDS: that route serves federated *reads* and has
        # no validate branch, and the document under test is the root
        # meta-graph exactly as written -- which is what ``g`` already holds.
        errs = validate_graph(
            g.dump(), id_index=federation_id_index_for_root(args.root),
        )
        _out({"valid": not errs, "errors": [str(e) for e in errs]})
        if errs:
            graph_path = Path(args.root) / ".weld" / "graph.json"
            # The report names the offending node/edge ids verbatim.
            sys.stderr.write(sanitize_terminal_text(format_validation_report(
                errs, source=str(graph_path),
            )))
            sys.exit(1)
    elif cmd == "migrate":
        # ADR 0050: --add-confidence backfills missing edge.confidence
        # props by classifying each edge's source_strategy against the
        # static defaults map. Future migrations land here as
        # additional --flag options on the same subcommand.
        if not getattr(args, "add_confidence", False):
            sys.stderr.write(
                "wd migrate: pass --add-confidence (ADR 0050) to "
                "backfill missing edge confidence props.\n",
            )
            sys.exit(2)
        from weld._graph_migrate import backfill_confidence
        data = g.dump()
        report = backfill_confidence(data)
        # backfill_confidence mutates `data` in place; the in-memory
        # graph holds the same dict reference, so save() persists it.
        g.save(touch_git_sha=True)
        _out(report.to_dict())
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
            emit(f"[weld] warning: {warning}")
        if errs:
            source = "<stdin>" if str(args.file) == "-" else str(args.file)
            # Fragment ids come from whatever produced the fragment.
            sys.stderr.write(
                sanitize_terminal_text(format_validation_report(errs, source=source))
            )
            sys.exit(1)
    if mutates:
        # Mutating CLI paths implicitly advance meta.git_sha to HEAD so
        # enrichment-only commits do not trigger [stale] false positives
        # (ADR 0017). Outside a git repo this is a silent no-op.
        g.save(touch_git_sha=True)
