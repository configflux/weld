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


def run_export(argv: list[str]) -> int:
    """Parse export subcommand args and run the export."""
    from weld.export import export

    parser = argparse.ArgumentParser(prog="wd export")
    parser.add_argument(
        "--format",
        "-f",
        default="mermaid",
        choices=("mermaid", "dot", "d2"),
        help="Output format (default: mermaid)",
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
    output = export(args.format, node_id=node_id, depth=args.depth)
    sys.stdout.write(output)
    return 0
