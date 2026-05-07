"""Best-effort sidecar persistence for the discovery orchestrator.

Hosts the two helpers ``_discover_single_repo`` calls after each
graph build to keep ``wd query`` (query-state sidecar) and ``wd find``
(file index) in sync with the freshly-written ``graph.json``. Carved
out of :mod:`weld.discover` to keep that module under the 400-line
cap (CLAUDE.md "Line-Count Policy").

Failures inside both helpers are logged and swallowed -- a missing
sidecar simply means the next cold load rebuilds and writes one
itself; we never let an indexing hiccup fail the whole discovery run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from weld._query_sidecar import write_sidecar_for_bytes as _write_query_sidecar_bytes
from weld.serializer import dumps_graph as _dumps_graph


def persist_query_state_sidecar(weld_dir: Path, graph: dict) -> None:
    """Write the .weld/query_state.bin sidecar for the freshly-built graph.

    ADR 0031: the inverted index, BM25 corpus, and structural-score
    table are pure functions of the graph's node and edge sets and
    dominate the ``wd query`` cold path. Persisting them here makes
    the next cold ``Graph.load`` skip the rebuild.
    """
    try:
        from weld.query_state import build_query_state

        nodes = graph.get("nodes", {})
        edges = graph.get("edges", [])
        graph_bytes = _dumps_graph(graph).encode("utf-8")
        state = build_query_state(nodes, edges)
        _write_query_sidecar_bytes(weld_dir, graph_bytes, nodes, edges, state)
    except Exception as exc:  # noqa: BLE001 -- sidecar is best-effort.
        print(
            f"[weld] notice: skipped query-state sidecar write: {exc}",
            file=sys.stderr,
        )


def persist_file_index(root: Path) -> None:
    """Refresh the keyword-to-file index alongside the graph.

    The file index backs ``wd find`` and is functionally a sibling of
    ``graph.json``: callers expect both to be in sync after a
    discovery run. Historically only the standalone ``wd build-index``
    verb wrote it, so a fresh checkout that ran ``wd discover`` could
    leave ``wd find`` returning empty results for symbols that clearly
    existed on disk and in the graph (the canonical dogfood gap).
    """
    try:
        from weld.file_index import build_file_index, save_file_index

        index = build_file_index(root)
        save_file_index(root, index)
    except Exception as exc:  # noqa: BLE001 -- index refresh is best-effort.
        print(
            f"[weld] notice: skipped file-index refresh: {exc}",
            file=sys.stderr,
        )
