"""Success-line summary formatter for ``wd discover``.

Mirrors the UX of ``wd build-index`` (``Indexed N files -> path``) so a
user running discovery sees a one-line confirmation that something
happened, instead of ~3.5s of dead silence.

The line goes to stderr only -- stdout still carries the canonical graph
JSON in default mode and is empty in ``--output`` mode (ADR 0019). JSON
consumers piping discovery's stdout to ``jq`` are therefore unaffected.

Format (matches ``Indexed 1027 files -> .weld/file-index.json`` style):

    wrote {N} nodes / {M} edges -> {path} ({elapsed}s)

When the run wrote graph JSON to stdout instead of a file (no
``--output`` and no ``--write-root-graph``), the path arrow is omitted:

    wrote {N} nodes / {M} edges ({elapsed}s)
"""

from __future__ import annotations

import sys
from pathlib import Path


def format_summary(graph: dict, output_path: Path | None, elapsed_s: float) -> str:
    """Build the one-line discovery success summary.

    *graph* is the canonical graph dict returned by :func:`discover`.
    *output_path* is the destination file when discovery wrote to disk
    (``--output`` or federated ``--write-root-graph``); pass ``None``
    when stdout is the sink. *elapsed_s* is the wall-clock seconds the
    discovery run took, formatted to two decimals so very short and
    very long runs both stay legible.
    """
    nodes = graph.get("nodes", {}) or {}
    edges = graph.get("edges", []) or []
    n_nodes = len(nodes)
    n_edges = len(edges)
    base = f"wrote {n_nodes} nodes / {n_edges} edges"
    if output_path is not None:
        base += f" -> {output_path}"
    return f"{base} ({elapsed_s:.2f}s)"


def emit_summary(
    graph: dict,
    output_path: Path | None,
    elapsed_s: float,
    *,
    quiet: bool,
) -> None:
    """Print the summary to stderr unless *quiet* is set.

    Centralised so the CLI's success path stays a single call site and
    so tests can rely on the format being computed in exactly one
    place.
    """
    if quiet:
        return
    print(format_summary(graph, output_path, elapsed_s), file=sys.stderr)
