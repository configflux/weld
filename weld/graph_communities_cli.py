"""CLI adapter for ``wd graph communities``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from weld.graph import Graph
from weld.graph_communities import build_graph_communities
from weld.graph_communities_render import (
    dumps_communities,
    render_community_report,
    write_community_artifacts,
)


def run_graph_communities(args: Any, graph: Graph) -> None:
    """Print or write deterministic community analysis for a loaded graph.

    Per ADR 0040, the default output is the markdown report (human form);
    pass ``--json`` (or ``--format json``) to emit the JSON envelope.
    """
    payload = build_graph_communities(
        graph.dump(),
        top=args.top,
        stale=graph.stale(),
    )
    if args.write:
        output_dir = _resolve_output_dir(args.root, args.output_dir)
        write_community_artifacts(output_dir, payload)
    fmt = _resolve_format(args)
    if fmt == "json":
        sys.stdout.write(dumps_communities(payload))
    else:
        sys.stdout.write(render_community_report(payload))


def _resolve_format(args: Any) -> str:
    """Pick the output format honouring ADR 0040.

    ``--json`` (the standard cross-command flag) wins. Otherwise an
    explicit ``--format`` value is honoured. When neither is set the
    default is ``markdown`` per the convention.
    """
    if getattr(args, "as_json", False):
        return "json"
    explicit = getattr(args, "format", None)
    if explicit:
        return explicit
    return "markdown"


def _resolve_output_dir(root: Path, output_dir: Path) -> Path:
    if output_dir.is_absolute():
        return output_dir
    return root / output_dir
