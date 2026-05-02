"""Argparse construction for the ``weld._graph_cli`` dispatcher.

Extracted from :mod:`weld._graph_cli` so the dispatcher stays under the
400-line cap (CLAUDE.md line-count policy). The split is purely
mechanical: every subparser definition that lived inline in
``_graph_cli.main`` lives here, returning the configured
:class:`argparse.ArgumentParser`. Behaviour is unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from weld.contract import VALID_EDGE_TYPES, VALID_NODE_TYPES

_JSON_HELP = "Emit JSON envelope instead of human text (ADR 0040)."


def _positive_int(value: str) -> int:
    """argparse validator: accept only strictly-positive integers.

    Mirrors the historical helper from :mod:`weld._graph_cli` so a bad
    invocation never silently truncates a top-N list to zero entries.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer, got {value!r}",
        ) from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer, got {parsed}",
        )
    return parsed


def build_parser(prog: str = "wd") -> argparse.ArgumentParser:
    """Return the fully configured ``wd`` graph-namespace parser."""
    parser = argparse.ArgumentParser(
        prog=prog, description="Connected structure CLI",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root directory",
    )
    sub = parser.add_subparsers(dest="command")
    _add_query(sub)
    _add_context(sub)
    _add_path(sub)
    _add_mutators(sub)
    _add_list(sub)
    _add_find(sub)
    _add_callers_refs(sub)
    _add_simple(sub)
    _add_stats(sub)
    _add_communities(sub)
    _add_import_validate(sub)
    return parser


def _add_query(sub) -> None:
    p = sub.add_parser(
        "query",
        help=(
            "Tokenized search (fields: id, label, props.file, props.exports, "
            "props.description)"
        ),
    )
    p.add_argument(
        "term",
        help=(
            "Search term (multi-word is tokenized; strict-AND first, OR "
            "fallback when AND is empty)"
        ),
    )
    p.add_argument("--limit", type=int, default=20)
    p.add_argument(
        "--json", dest="as_json", action="store_true", help=_JSON_HELP,
    )


def _add_context(sub) -> None:
    p = sub.add_parser("context", help="Node + neighborhood")
    p.add_argument("node_id", help="Node ID")
    p.add_argument(
        "--json", dest="as_json", action="store_true", help=_JSON_HELP,
    )


def _add_path(sub) -> None:
    p = sub.add_parser("path", help="Shortest path between nodes")
    p.add_argument("from_id", help="Start node ID")
    p.add_argument("to_id", help="End node ID")
    p.add_argument(
        "--json", dest="as_json", action="store_true", help=_JSON_HELP,
    )


def _add_mutators(sub) -> None:
    p_an = sub.add_parser("add-node", help="Add or update a node")
    p_an.add_argument("id", help="Node ID (e.g. entity:Store)")
    p_an.add_argument(
        "--type", required=True, dest="node_type",
        choices=sorted(VALID_NODE_TYPES), help="Node type",
    )
    p_an.add_argument("--label", default="", help="Human-readable label")
    p_an.add_argument("--props", default="{}", help="JSON properties")
    p_an.add_argument(
        "--merge", action="store_true",
        help="Deep-merge props into existing node",
    )
    p_ae = sub.add_parser(
        "add-edge",
        help=(
            "Add an edge. Use --props for provenance, e.g. --props "
            "'{\"source\":\"llm\"}'. (Replaces 0.3.0 --source/--relation "
            "flags.)"
        ),
    )
    p_ae.add_argument("from_id", help="Source node ID")
    p_ae.add_argument("to_id", help="Target node ID")
    p_ae.add_argument(
        "--type", required=True, dest="edge_type",
        choices=sorted(VALID_EDGE_TYPES), help="Edge type",
    )
    p_ae.add_argument(
        "--props", default="{}",
        help=(
            "JSON properties, e.g. '{\"source\":\"llm\","
            "\"confidence\":\"inferred\"}'. Use props.source to record "
            "provenance for tool-generated edges."
        ),
    )
    p_rn = sub.add_parser("rm-node", help="Remove a node and its edges")
    p_rn.add_argument("id", help="Node ID")
    p_re = sub.add_parser("rm-edge", help="Remove edge(s)")
    p_re.add_argument("from_id", help="Source node ID")
    p_re.add_argument("to_id", help="Target node ID")
    p_re.add_argument(
        "--type", dest="edge_type", choices=sorted(VALID_EDGE_TYPES),
        default=None, help="Edge type filter",
    )


def _add_list(sub) -> None:
    p = sub.add_parser("list", help="List nodes")
    p.add_argument(
        "--type", dest="type_filter", choices=sorted(VALID_NODE_TYPES),
        default=None, help="Filter by type",
    )


def _add_find(sub) -> None:
    p = sub.add_parser("find", help="Search file index by keyword")
    p.add_argument("term", help="Search term (substring match)")
    p.add_argument(
        "--limit", type=int, default=20,
        help=(
            "Maximum number of file entries to return (default 20, mirrors "
            "wd query)"
        ),
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true", help=_JSON_HELP,
    )


def _add_callers_refs(sub) -> None:
    p_callers = sub.add_parser(
        "callers",
        help="Direct (and optionally transitive) callers of a symbol",
    )
    p_callers.add_argument(
        "symbol",
        help=(
            "Symbol id or bare name, e.g. "
            "'symbol:py:weld.discover:_load_strategy' or '_load_strategy'"
        ),
    )
    p_callers.add_argument(
        "--depth", type=int, default=1,
        help="Caller traversal depth (default 1)",
    )
    p_callers.add_argument(
        "--json", dest="as_json", action="store_true", help=_JSON_HELP,
    )
    p_refs = sub.add_parser(
        "references",
        help="Callers + textual file-index references for a symbol name",
    )
    p_refs.add_argument("name", help="Bare symbol name, e.g. _load_strategy")
    p_refs.add_argument(
        "--json", dest="as_json", action="store_true", help=_JSON_HELP,
    )


def _add_simple(sub) -> None:
    p_stale = sub.add_parser(
        "stale", help="Check if graph is stale vs current HEAD",
    )
    p_stale.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit JSON envelope instead of human key:value pairs (ADR 0040).",
    )
    sub.add_parser(
        "touch",
        help=(
            "Stamp meta.git_sha=HEAD + meta.updated_at=now without "
            "mutating nodes/edges (use after enrichment-only commits)."
        ),
    )
    sub.add_parser("dump", help="Full graph JSON")


def _add_stats(sub) -> None:
    p = sub.add_parser("stats", help="Summary counts")
    p.add_argument(
        "--top", type=_positive_int, default=None, metavar="N",
        help="Cap on top_authority_nodes list (default: 5).",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true", help=_JSON_HELP,
    )


def _add_communities(sub) -> None:
    p = sub.add_parser("communities", help="Detect graph communities")
    p.add_argument(
        "--format", choices=("json", "markdown"), default=None,
        help=(
            "Output format. Default per ADR 0040 is the markdown report; "
            "passing 'json' (or --json) emits the JSON envelope."
        ),
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Alias for --format json; emits the JSON envelope (ADR 0040).",
    )
    p.add_argument(
        "--top", type=_positive_int, default=12, metavar="N",
        help=(
            "Number of community summaries and per-community items to "
            "report (default: 12)"
        ),
    )
    p.add_argument(
        "--write", action="store_true",
        help="Write graph-community JSON, report, and index artifacts",
    )
    p.add_argument(
        "--output-dir", type=Path, default=Path(".weld"),
        help="Artifact directory used with --write (default: .weld)",
    )


def _add_import_validate(sub) -> None:
    p_imp = sub.add_parser("import", help="Import/merge from file")
    p_imp.add_argument("file", type=Path, help="JSON file to import")
    sub.add_parser(
        "validate", help="Validate graph against the metadata contract",
    )
    p_vf = sub.add_parser(
        "validate-fragment", help="Validate a JSON fragment",
    )
    p_vf.add_argument("file", type=Path, help="JSON fragment file")
    p_vf.add_argument(
        "--source-label", default="fragment", help="Diagnostic label",
    )
    p_vf.add_argument(
        "--allow-dangling", action="store_true", help="Skip ref checks",
    )
