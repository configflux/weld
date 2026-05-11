"""CLI argument parser for ``wd discover``.

Extracted from :mod:`weld.discover` to keep that file under the
400-line cap (ADR 0057 Wave 3 added the ``--emit-compile-db-stub``
flag, pushing the entry-point module over the line).
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wd discover",
        description="Run config-driven Weld discovery and emit graph JSON to stdout")
    parser.add_argument("root", nargs="?", default=".", help="Project root directory (default: .)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--incremental", action="store_true", default=False,
        help="Only re-extract changed files (default when state file exists)")
    mode.add_argument("--full", action="store_true", default=False,
        help="Force full discovery, ignoring any previous state")
    parser.add_argument(
        "--write-root-graph",
        action="store_true",
        default=False,
        help="On a federated root, write .weld/graph.json atomically "
             "inside the workspace lock (required for crash-safety).",
    )
    parser.add_argument(
        "--recurse", action="store_true", default=False,
        help="Cascade discovery into each present child before building the root meta-graph.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Atomically write canonical graph JSON to this path "
             "(parent directories are created). When set, stdout is "
             "empty; human status still goes to stderr. (ADR 0019)",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        default=False,
        help="Refuse project-local strategies under .weld/strategies/ and "
             "the external_json subprocess adapter. Use this when scanning "
             "an untrusted repository. (ADR 0024)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        default=False,
        help="Bypass the federated empty-graph guard. By default, a "
             "federated discover that would overwrite a non-empty graph "
             "with a 0-node meta-graph is refused; pass this flag to "
             "intentionally tear the workspace graph down. (ADR 0028)",
    )
    parser.add_argument(
        "--no-sqlite", action="store_true", default=False,
        help="Skip writing the sqlite sidecar (.weld/graph.db); graph.json "
             "is always written. The sidecar is a derived index that speeds "
             "up federation reads. (ADR 0058)",
    )
    parser.add_argument(
        "--quiet", action="store_true", default=False,
        help="Suppress the one-line stderr success summary "
             "(node/edge counts, elapsed time). Use for scripted callers.",
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        default=False,
        help="Skip the first-run enrichment prompt entirely (ADR 0052). "
             "Use for scripted callers or when the user has already "
             "decided how they will enrich. Also respected via the "
             "WELD_NO_ENRICH=1 environment variable.",
    )
    parser.add_argument(
        "--emit-compile-db-stub", action="store_true", default=False,
        help="Write a placeholder compile_commands.json documenting how to "
             "generate a real one for libclang (ADR 0057 Wave 3); exits.",
    )
    return parser


__all__ = ["build_parser"]
