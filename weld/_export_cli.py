"""CLI argument parsing for ``wd export``.

Extracted from :mod:`weld.cli` so the top-level dispatcher stays under the
400-line cap (CLAUDE.md line-count policy).

The export command accepts the centre node id either as a positional
``<node>`` argument (canonical, matches ``wd impact`` / ``context`` /
``callers`` / ``references``) or via the legacy ``--node`` flag, which is
deprecated for one release and emits a one-line ``DeprecationWarning`` to
stderr when used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from weld._safe_text import sanitize_terminal_text


def run_export(argv: list[str]) -> int:
    """Parse export subcommand args and run the export."""
    from weld._auto_refresh import auto_refresh_if_stale
    from weld._graph_cli_errors import load_graph_or_exit
    from weld.export import export
    from weld.graph import Graph

    parser = argparse.ArgumentParser(prog="wd export")
    parser.add_argument(
        "--format",
        "-f",
        default="mermaid",
        choices=("mermaid", "dot", "d2", "wiki"),
        help=(
            "Output format. ``mermaid``/``dot``/``d2`` stream a single "
            "diagram to stdout. ``wiki`` writes a directory "
            "tree of markdown wikilinks to ``--output``."
        ),
    )
    parser.add_argument(
        "node",
        nargs="?",
        default=None,
        help="Centre node id for subgraph extraction (e.g. entity:Store)",
    )
    parser.add_argument(
        "--node",
        dest="node_flag",
        default=None,
        help=(
            "[deprecated] Centre node id; use the positional <node> "
            "argument instead. The flag is kept for one release."
        ),
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="BFS depth for subgraph extraction (default: 1)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root containing .weld/graph.json",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help=(
            "Output directory for multi-file formats (required for "
            "``--format=wiki``). Ignored by string-output formats."
        ),
    )
    parser.add_argument(
        "--no-refresh",
        dest="no_refresh",
        action="store_true",
        default=False,
        help=(
            "Skip the auto-refresh that runs when the graph is stale. "
            "A warning is emitted to stderr."
        ),
    )
    args = parser.parse_args(argv)
    node_id = args.node
    if args.node_flag is not None:
        if node_id is None:
            node_id = args.node_flag
        sys.stderr.write(
            "DeprecationWarning: 'wd export --node <id>' is deprecated; "
            "pass <node> as a positional argument instead "
            "(e.g. 'wd export entity:Store').\n"
        )
    # Multi-file formats require ``--output``; surface a friendly error
    # rather than letting ``export()`` raise a bare ValueError.
    if args.format == "wiki" and args.output is None:
        parser.error("--format=wiki requires --output=<dir>")
    # ADR 0051: auto-refresh stale graphs before exporting. Diagram
    # output is canonical artifact -- a stale graph would otherwise emit
    # a stale diagram with no warning. ``json_output=True`` keeps the
    # banner suppressed since export streams structured text to stdout.
    auto_refresh_if_stale(
        args.root,
        no_refresh=args.no_refresh,
        json_output=True,
    )
    # bd tl32: this CLI-only probe load is what turns a corrupt/truncated
    # graph.json or a directory at the graph path into the structured
    # `error[<code>]: ... | hint: ...` contract every sibling read command
    # gives, instead of a raw traceback -- this command had no guard
    # anywhere in its call chain before that fix. The MCP and viz-server
    # callers have no such probe load; they call export() with root= only,
    # so their own exception classifiers still see the raw load exception.
    #
    # bd 6vq7: the loaded Graph used to be discarded here, so export()
    # would construct+load a second Graph internally from the same
    # graph.json -- reading and JSON-parsing it twice per invocation.
    # Threading it through via graph= makes export() use this one instead.
    g = load_graph_or_exit(Graph(args.root))
    output = export(
        args.format,
        node_id=node_id,
        depth=args.depth,
        root=args.root,
        output=args.output,
        graph=g,
    )
    sys.stdout.write(sanitize_terminal_text(output))
    return 0
