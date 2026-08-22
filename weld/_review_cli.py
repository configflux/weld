"""``wd review`` CLI dispatcher (ADR 0055, ADR 0040).

Subcommands::

    wd review list   [--type EDGE_TYPE] [--source STRATEGY] [--limit N] [--json]
    wd review show   <edge-id> [--json]
    wd review accept <edge-id|--pattern '<dsl>'> [--reason ...] [--yes] [--json]
    wd review reject <edge-id|--pattern '<dsl>'> [--reason ...] [--yes] [--json]
    wd review reset  <edge-id> [--json]
    wd review status [--json]

Bulk operations (``--pattern``) require ``--yes`` for non-interactive
runs. The DSL is parsed via :func:`weld._review_pattern.parse_pattern`
with regex length bounds so the CLI cannot be hung by a crafted input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from weld._review import (
    accept_edge,
    detect_ghost_emit,
    list_pending,
    mint_edge_id,
    reject_edge,
    reset_decision,
    show_edge,
    status_summary,
)
from weld._review_pattern import PatternError, match, parse_pattern
from weld._review_state import load_state
from weld._safe_text import dumps_safe_json, sanitize_terminal_text
from weld.graph import Graph


def _out(payload: Any) -> None:
    sys.stdout.write(dumps_safe_json(payload, indent=2) + "\n")


def _emit(payload: Any, *, as_json: bool, render) -> None:
    """The one write boundary for ``wd review``.

    The renderer arrives as a callback, so the escape has to sit here rather
    than at each ``_render_*``: every line it emits names graph-derived edge
    ids, node ids and strategy names.
    """
    if as_json:
        _out(payload)
        return
    sys.stdout.write(sanitize_terminal_text(render(payload)))


def _render_status(payload: dict) -> str:
    return (
        f"review status\n"
        f"  pending:  {payload.get('pending', 0)}\n"
        f"  accepted: {payload.get('accepted', 0)}\n"
        f"  rejected: {payload.get('rejected', 0)}\n"
        f"  stale:    {payload.get('stale', 0)}\n"
    )


def _render_list(payload: dict) -> str:
    edges = payload.get("edges") or []
    lines = [f"review list (pending={len(edges)}):"]
    for e in edges:
        lines.append(
            f"  {e['review_id']}  {e['from']} --{e['type']}--> {e['to']}"
        )
        if e.get("source_strategy"):
            lines.append(f"    source: {e['source_strategy']}")
        prov = e.get("provenance") or {}
        rationale = prov.get("rationale") if isinstance(prov, dict) else None
        if rationale:
            lines.append(f"    rationale: {rationale}")
    return "\n".join(lines) + "\n"


def _render_show(payload: dict) -> str:
    if "error" in payload:
        return f"error: {payload['error']}\n"
    return _render_list({"edges": [payload]})


def _render_decision(payload: dict) -> str:
    if "error" in payload:
        return f"error: {payload['error']}\n"
    return (
        f"{payload['review_id']}: {payload['decision']}"
        + (f" (confidence={payload['confidence']})"
           if "confidence" in payload else "")
        + "\n"
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wd review",
        description=(
            "Triage speculative edges in the connected structure "
            "(accept / reject / reset)."
        ),
    )
    p.add_argument(
        "--root", type=Path, default=Path("."), help="Project root.",
    )
    sub = p.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="List pending speculative edges.")
    p_list.add_argument("--type", dest="type_filter", default=None)
    p_list.add_argument("--source", dest="source_filter", default=None)
    p_list.add_argument("--limit", type=int, default=None)
    p_list.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit JSON envelope.",
    )

    p_show = sub.add_parser("show", help="Show one edge by review id.")
    p_show.add_argument("edge_id")
    p_show.add_argument(
        "--json", dest="as_json", action="store_true", help="Emit JSON.",
    )

    for verb, action_help in (
        ("accept", "Promote speculative -> definite for an edge."),
        ("reject", "Drop an edge at next discover."),
    ):
        sp = sub.add_parser(verb, help=action_help)
        sp.add_argument("edge_id", nargs="?", default=None)
        sp.add_argument("--reason", default="")
        sp.add_argument(
            "--pattern", default=None,
            help=(
                "Bulk filter (DSL: type=X source=Y target~RE from~RE). "
                "Requires --yes for unattended runs."
            ),
        )
        sp.add_argument(
            "--yes", dest="yes", action="store_true",
            help="Confirm bulk --pattern operation.",
        )
        sp.add_argument(
            "--json", dest="as_json", action="store_true",
            help="Emit JSON.",
        )

    p_reset = sub.add_parser("reset", help="Reset an edge back to pending.")
    p_reset.add_argument("edge_id")
    p_reset.add_argument(
        "--json", dest="as_json", action="store_true", help="Emit JSON.",
    )

    p_status = sub.add_parser("status", help="Summary counts.")
    p_status.add_argument(
        "--json", dest="as_json", action="store_true", help="Emit JSON.",
    )

    return p


def _bulk_target_edges(root: Path, pattern_text: str) -> list[dict]:
    """Return edges matching *pattern_text*. Speculative-only.

    Bulk operations are restricted to edges still in the
    ``speculative`` bucket because that is the review queue's input.
    Already-decided edges fall out of the set, preventing a bulk
    accept/reject from clobbering an existing decision.
    """
    g = Graph(root)
    g.load()
    state = load_state(root)
    decided = set(state.decisions.keys())
    pat = parse_pattern(pattern_text)
    out: list[dict] = []
    for edge in g.dump().get("edges", []):
        props = edge.get("props") or {}
        if props.get("confidence") != "speculative":
            continue
        if mint_edge_id(edge) in decided:
            continue
        if not match(pat, edge):
            continue
        out.append(edge)
    return out


def _run_decision(args: argparse.Namespace, verb: str) -> int:
    func = accept_edge if verb == "accept" else reject_edge
    if args.pattern is not None:
        if not args.yes:
            sys.stderr.write(
                "wd review: bulk --pattern requires --yes for "
                "unattended execution.\n",
            )
            return 2
        try:
            targets = _bulk_target_edges(args.root, args.pattern)
        except PatternError as exc:
            sys.stderr.write(f"wd review: {exc}\n")
            return 2
        report: dict[str, Any] = {
            verb + "ed": 0, "edges": [], "errors": [],
        }
        for edge in targets:
            eid = mint_edge_id(edge)
            res = func(args.root, eid, reason=args.reason)
            if "error" in res:
                report["errors"].append(res)
                continue
            report[verb + "ed"] = report[verb + "ed"] + 1
            report["edges"].append(res)
        _emit(
            report, as_json=args.as_json,
            render=lambda p: f"{verb}ed {p[verb + 'ed']} edges\n",
        )
        return 0
    if not args.edge_id:
        sys.stderr.write(
            f"wd review {verb}: edge_id or --pattern required.\n",
        )
        return 2
    res = func(args.root, args.edge_id, reason=args.reason)
    _emit(res, as_json=args.as_json, render=_render_decision)
    return 0 if "error" not in res else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1
    if args.cmd == "list":
        payload = list_pending(
            args.root,
            limit=args.limit,
            type_filter=args.type_filter,
            source_filter=args.source_filter,
        )
        ghosts = detect_ghost_emit(args.root)
        if ghosts:
            payload["ghost_emits"] = ghosts
        _emit(payload, as_json=args.as_json, render=_render_list)
        return 0
    if args.cmd == "show":
        payload = show_edge(args.root, args.edge_id)
        _emit(payload, as_json=args.as_json, render=_render_show)
        return 0 if "error" not in payload else 1
    if args.cmd == "status":
        payload = status_summary(args.root)
        ghosts = detect_ghost_emit(args.root)
        if ghosts:
            payload["ghost_emits"] = len(ghosts)
        _emit(payload, as_json=args.as_json, render=_render_status)
        return 0
    if args.cmd == "reset":
        res = reset_decision(args.root, args.edge_id)
        _emit(res, as_json=args.as_json, render=_render_decision)
        return 0
    if args.cmd in ("accept", "reject"):
        return _run_decision(args, args.cmd)
    parser.print_help()
    return 1


__all__ = ["main"]
