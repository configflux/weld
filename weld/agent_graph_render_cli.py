"""CLI surface for ``wd agents render`` (preview, ADR 0026).

This module wires the dry-run / write / force flags described in ADR 0026
to :mod:`weld.agent_graph_render_writer`. It is intentionally thin: all
filesystem decisions live in the writer module and all parsing of the
``.weld/agents.yaml`` sidecar is delegated to
:mod:`weld.agent_graph_authority`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from weld.agent_graph_render_writer import (
    PlannedRender,
    apply_plan,
    plan_all,
)

_HELP = (
    "[preview] Render canonical Agent Graph assets to per-platform copies "
    "declared in .weld/agents.yaml. Default behavior is dry-run/diff only; "
    "use --write to apply, and --write --force to overwrite an existing "
    "rendered file whose bytes differ. The command, its flags, and its "
    "output may change before v1.0."
)


def add_render_parser(subparsers: Any) -> None:
    """Register the ``render`` subcommand under ``wd agents``."""
    parser = subparsers.add_parser(
        "render",
        help="[preview] Render canonical Agent Graph assets (dry-run by default).",
        description=_HELP,
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to render under (default: current directory).",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write rendered files to disk. Without this flag the command never writes.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "With --write, overwrite an existing rendered file whose bytes "
            "differ from the renderer's output. Ignored without --write."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a stable JSON plan (or apply result) instead of human-readable output.",
    )


def run_render(args: argparse.Namespace) -> int:
    """Execute the render subcommand."""
    root = Path(args.root).resolve()
    plan = plan_all(root)
    if args.write:
        return _do_write(plan, root, force=args.force, as_json=args.json)
    return _do_dry_run(plan, as_json=args.json)


# --- handlers --------------------------------------------------------------


def _do_dry_run(plan: list[PlannedRender], *, as_json: bool) -> int:
    has_changes = any(entry.action != "skip" for entry in plan)
    has_errors = any(entry.action == "error" for entry in plan)
    if as_json:
        sys.stdout.write(json.dumps(_plan_payload(plan), indent=2, sort_keys=True) + "\n")
    else:
        _print_dry_run(plan)
    return 1 if has_changes or has_errors else 0


def _do_write(
    plan: list[PlannedRender],
    root: Path,
    *,
    force: bool,
    as_json: bool,
) -> int:
    applied, refusals = apply_plan(plan, root, force=force)
    if as_json:
        payload = {
            "applied": [_pair_dict(entry) for entry in applied],
            "refusals": refusals,
            "summary": _summary(plan),
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        _print_write(applied, refusals)
    if refusals:
        for refusal in refusals:
            if refusal["reason"] == "exists_no_force":
                print(
                    f"refused: {refusal['rendered']} differs from canonical "
                    f"{refusal['canonical']}; rerun with --force to overwrite.",
                    file=sys.stderr,
                )
            elif refusal["reason"] == "missing_canonical":
                print(
                    f"error: canonical asset missing for {refusal['rendered']}: "
                    f"{refusal['canonical']}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"refused: {refusal['rendered']} ({refusal['reason']})",
                    file=sys.stderr,
                )
        return 1
    return 0


# --- formatting ------------------------------------------------------------


def _print_dry_run(plan: list[PlannedRender]) -> None:
    if not plan:
        print("No canonical -> rendered pairs declared in .weld/agents.yaml.")
        return
    for entry in plan:
        marker = _action_marker(entry.action)
        print(
            f"{marker} {entry.pair.rendered} "
            f"<- {entry.pair.canonical} ({entry.action}: {entry.reason})"
        )
        if entry.diff:
            sys.stdout.write(entry.diff)
            if not entry.diff.endswith("\n"):
                print()
    print()
    print(_summary_line(plan))
    print("Run with --write to apply, or --write --force to overwrite existing files.")


def _print_write(
    applied: list[PlannedRender],
    refusals: list[dict[str, str]],
) -> None:
    for entry in applied:
        print(
            f"wrote {entry.pair.rendered} <- {entry.pair.canonical} "
            f"({entry.action})"
        )
    if not applied and not refusals:
        print("Nothing to do; all rendered copies are in sync.")


def _summary_line(plan: list[PlannedRender]) -> str:
    counts = _summary(plan)
    return (
        f"Summary: {counts['create']} to create, {counts['update']} to update, "
        f"{counts['skip']} in sync, {counts['error']} error(s)."
    )


def _summary(plan: list[PlannedRender]) -> dict[str, int]:
    counts = {"create": 0, "update": 0, "skip": 0, "error": 0}
    for entry in plan:
        counts[entry.action] = counts.get(entry.action, 0) + 1
    return counts


def _plan_payload(plan: list[PlannedRender]) -> dict[str, Any]:
    return {
        "pairs": [_pair_dict(entry) for entry in plan],
        "summary": _summary(plan),
    }


def _pair_dict(entry: PlannedRender) -> dict[str, Any]:
    return {
        "canonical": entry.pair.canonical,
        "rendered": entry.pair.rendered,
        "name": entry.pair.name,
        "action": entry.action,
        "reason": entry.reason,
        "diff": entry.diff,
    }


def _action_marker(action: str) -> str:
    return {
        "create": "+",
        "update": "~",
        "skip": "=",
        "error": "!",
    }.get(action, "?")
