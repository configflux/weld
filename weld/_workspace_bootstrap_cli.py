"""CLI plumbing for ``wd workspace bootstrap`` (ADR 0018).

Split from :mod:`weld.workspace_state` so that module stays under the
repo source-line cap and remains focused on the ledger / lockfile
contract. The argparse subparser definition and the
``_run_bootstrap_subcommand`` glue both live here; the ledger module
imports and delegates.
"""

from __future__ import annotations

import argparse
import json
import sys

__all__ = ["add_bootstrap_subparser", "run_bootstrap"]


def add_bootstrap_subparser(
    subparsers: argparse._SubParsersAction,
    default_max_depth: int,
) -> argparse.ArgumentParser:
    """Register the ``bootstrap`` subcommand on ``subparsers``."""
    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help=(
            "One-shot polyrepo bootstrap: init root, scan nested repos, "
            "init each child, recurse-discover, rebuild root meta-graph"
        ),
    )
    bootstrap_parser.add_argument(
        "--root", default=".",
        help="Workspace root directory (default: .)",
    )
    bootstrap_parser.add_argument(
        "--max-depth", type=int, default=default_max_depth,
        help=(
            "Maximum directory depth when scanning for nested git repos "
            f"(default: {default_max_depth})"
        ),
    )
    bootstrap_parser.add_argument(
        "--exclude-path", action="append", default=None, metavar="PATH",
        help=(
            "Directory name or workspace-relative path to exclude from the "
            "nested-repo scan. Repeatable. Persisted into workspaces.yaml so "
            "subsequent bootstraps stay excluded without re-passing the flag."
        ),
    )
    bootstrap_parser.add_argument(
        "--json", action="store_true",
        help="Emit a JSON summary of what the bootstrap did",
    )
    bootstrap_gitignore = bootstrap_parser.add_mutually_exclusive_group()
    bootstrap_gitignore.add_argument(
        "--ignore-all", action="store_true",
        help=(
            "Write a fully-ignoring .weld/.gitignore in the root and every "
            "child (every weld file ignored). Default ignores generated "
            "graphs but tracks config; pass --track-graphs to also track "
            "graph.json + agent-graph.json."
        ),
    )
    bootstrap_gitignore.add_argument(
        "--track-graphs", action="store_true",
        help=(
            "Track canonical graphs (graph.json + agent-graph.json) in "
            "addition to config in every .weld/.gitignore the bootstrap "
            "writes. Default ignores generated graphs."
        ),
    )
    return bootstrap_parser


def run_bootstrap(args: argparse.Namespace) -> int:
    """Execute ``wd workspace bootstrap`` via the orchestrator module."""
    from weld._workspace_bootstrap import bootstrap_workspace

    try:
        result = bootstrap_workspace(
            args.root,
            max_depth=args.max_depth,
            exclude_paths=args.exclude_path,
            ignore_all=args.ignore_all,
            track_graphs=args.track_graphs,
        )
    except FileNotFoundError as exc:
        print(f"[weld] error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "root_init_ran": result.root_init_ran,
            "workspace_yaml_written": result.workspace_yaml_written,
            "children_discovered": result.children_discovered,
            "children_initialized": result.children_initialized,
            "children_recursed": result.children_recursed,
            "children_present": result.children_present,
            "excluded_by_invalid_name": result.excluded_by_invalid_name,
            "errors": result.errors,
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write("\n".join(result.summary_lines()) + "\n")

    missing = set(result.children_discovered) - set(result.children_present)
    if result.children_discovered and missing:
        return 1
    return 0
