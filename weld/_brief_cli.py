"""CLI wiring for ``wd brief``.

Split from :mod:`weld.brief` so that module stays the emission library (the
``brief()`` packet builder plus its classification/ranking helpers) while the
argparse plumbing and graph-loading dispatch live here, keeping both files
under the 400-line cap. ``weld.brief`` re-exports :func:`main` for backward
compatibility, so ``from weld.brief import main`` and the ``wd brief`` dispatch
in :mod:`weld.cli` are unchanged.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from weld._root_resolver import ROOT_HELP, resolve_weld_root
from weld._safe_text import dumps_safe_json


def _load_brief_graph(root: Path) -> Any:
    """Return the graph ``brief()`` reads, federating at a polyrepo root.

    ADR 0134 (Finding 01): ``wd brief`` federates so it spans child graphs like
    ``wd query`` / ``weld_brief`` (MCP), instead of reading only the root
    meta-graph and returning a silent empty result. ``brief()`` needs only
    ``query()`` / ``dump()``, both on ``FederatedGraph``, so this is a loader
    swap mirroring MCP ``load_graph_for_read`` -- ``wd brief --json`` stays
    byte-identical to ``weld_brief``. The caller enforces the graph-missing
    precondition first, so both branches see a present root graph.
    """
    from weld.workspace_state import find_workspaces_yaml

    if find_workspaces_yaml(root) is not None:
        from weld.federation import FederatedGraph

        return FederatedGraph(root)
    # A corrupt single-repo graph yields a structured error, not a traceback.
    from weld._graph_cli_errors import load_graph_or_exit
    from weld.graph import Graph

    return load_graph_or_exit(Graph(root))


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``wd brief``."""
    parser = argparse.ArgumentParser(
        prog="wd brief",
        description="Agent-facing context briefing with stable JSON contract",
    )
    parser.add_argument("term", help="Search term (same tokenization as query)")
    parser.add_argument("--root", type=Path, default=None, help=ROOT_HELP)
    parser.add_argument("--limit", type=int, default=20, help="Max nodes per section")
    parser.add_argument(
        "--no-refresh", dest="no_refresh", action="store_true", default=False,
        help="Skip auto-refresh on stale graph.",
    )
    parser.add_argument(
        "--full-size", dest="full_size", action="store_true", default=False,
        help="Skip the read byte budget and emit the full (edge-de-"
        "dangled) brief.",
    )
    args = parser.parse_args(argv)
    args.root = resolve_weld_root(args.root)  # ADR 0096

    from weld._auto_refresh import auto_refresh_if_stale
    from weld._graph_cli import _build_retry_hint, ensure_graph_exists
    from weld.brief import brief
    from weld.read import shape_brief

    # Friendly first-run message when the graph has not been built -- the same
    # cannot-answer precondition every graph-backed read hits (ADR 0134): a
    # graph-less root (federated or not) refuses with "No Weld graph found." +
    # non-zero exit rather than a well-formed empty payload.
    ensure_graph_exists(
        args.root, _build_retry_hint("brief", args.term), no_refresh=args.no_refresh,
    )
    # ADR 0051: auto-refresh stale graphs. ``brief`` always emits JSON,
    # so the human banner is unconditionally suppressed.
    auto_refresh_if_stale(args.root, no_refresh=args.no_refresh, json_output=True)
    g = _load_brief_graph(args.root)
    result = shape_brief(brief(g, args.term, limit=args.limit), full_size=args.full_size)
    sys.stdout.write(dumps_safe_json(result, indent=2) + "\n")
