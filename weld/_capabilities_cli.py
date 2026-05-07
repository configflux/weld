"""``wd capabilities`` CLI implementation (ADR 0043 Layer B).

Lives in a separate module to keep :mod:`weld.cli` under the 400-line
cap. Exposes :func:`main` which the dispatcher in :mod:`weld.cli`
delegates to.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from weld.capabilities import (
    compute_capabilities,
    detect_missing,
)
from weld.graph import Graph


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wd capabilities",
        description=(
            "Print the runtime capability matrix: per-language and "
            "per-framework evidence weld actually has for the loaded "
            "graph (ADR 0043 Layer B)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable matrix",
    )
    parser.add_argument(
        "--missing",
        action="store_true",
        help=(
            "List frameworks present-on-disk but not yet emitting useful "
            "edges (suggests where to extend support)"
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root containing .weld/graph.json (default: cwd)",
    )
    return parser


def _format_languages(languages: dict[str, dict[str, bool]]) -> list[str]:
    if not languages:
        return ["Languages: (none)"]
    lines = ["Languages:"]
    flags = ["file", "module", "imports", "symbols", "calls", "tests"]
    header = "  " + "  ".join([f"{f:<8}" for f in flags])
    lines.append(f"  language    {header}")
    for name in sorted(languages):
        row = languages[name]
        cells = "  ".join(
            "yes     " if row.get(flag) else "no      " for flag in flags
        )
        lines.append(f"  {name:<10}    {cells}")
    return lines


def _format_frameworks(frameworks: dict[str, dict[str, bool]]) -> list[str]:
    if not frameworks:
        return ["Frameworks: (none)"]
    lines = ["Frameworks:"]
    flags = ["nodes_emitted", "srcs_edges", "deps_edges", "test_edges"]
    header = "  " + "  ".join([f"{f:<14}" for f in flags])
    lines.append(f"  framework         {header}")
    for name in sorted(frameworks):
        row = frameworks[name]
        cells = "  ".join(
            "yes           " if row.get(flag) else "no            "
            for flag in flags
        )
        lines.append(f"  {name:<16}  {cells}")
    return lines


def _format_human(matrix: dict, missing: list[str] | None) -> str:
    lines: list[str] = []
    lines.extend(_format_languages(matrix.get("languages") or {}))
    lines.append("")
    lines.extend(_format_frameworks(matrix.get("frameworks") or {}))
    if missing is not None:
        lines.append("")
        if missing:
            lines.append("Missing (file present, no edges yet):")
            for name in missing:
                lines.append(f"  - {name}")
        else:
            lines.append("Missing: (none detected on disk)")
    return "\n".join(lines) + "\n"


_EMPTY_GRAPH: dict = {"meta": {}, "nodes": {}, "edges": []}


def _load_graph_data(root: Path) -> dict:
    """Return the raw graph dict at *root*, or an empty graph on missing/corrupt.

    ``wd capabilities`` should still report the registry-completeness
    rows (every language/framework with all-False flags) even before
    the user has run ``wd discover`` or when the on-disk graph is
    corrupted. Mirrors the defensive posture of
    :func:`weld.capabilities.compute_capabilities_for_graph`, which the
    ``wd impact`` envelope relies on.

    On a corrupt or unreadable ``.weld/graph.json`` this emits a
    one-line warning to ``stderr`` and returns the empty-graph fallback
    so the CLI can continue with registry-completeness rows.
    """
    graph_path = root / ".weld" / "graph.json"
    if not graph_path.is_file():
        return dict(_EMPTY_GRAPH)
    try:
        g = Graph(root)
        g.load()
        return g._data  # type: ignore[attr-defined]
    except Exception as exc:
        sys.stderr.write(
            f"warning: failed to load {graph_path}: {exc.__class__.__name__}: "
            f"{exc}; continuing with registry-completeness rows only\n",
        )
        return dict(_EMPTY_GRAPH)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = args.root
    matrix = compute_capabilities(_load_graph_data(root), root)
    missing: list[str] | None = None
    if args.missing:
        missing = detect_missing(root)

    if args.json:
        if args.missing:
            payload: dict | list = missing or []
        else:
            payload = matrix
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_format_human(matrix, missing))
    return 0
