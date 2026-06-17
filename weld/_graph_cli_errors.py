"""CLI-side structured-error guards for graph-load and node-lookup failures.

Split out of :mod:`weld._graph_cli` to keep that dispatcher under the
line-count cap. These two helpers turn the failure modes a graph-backed read
command can hit into the shared one-line ``error[<code>]: <summary> | hint:
<hint>`` contract (vocabulary in :mod:`weld._errors`, identical to the MCP
surface) with a nonzero exit, instead of a raw traceback or a silent exit-0:

* :func:`load_graph_or_exit` -- a corrupt/truncated ``graph.json``
  (``json.JSONDecodeError``) or one written by a newer Weld
  (``SchemaVersionError``).
* :func:`emit_node_lookup` -- a ``Graph.context`` / ``Graph.callers`` result
  that reports an unknown node id.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable


def load_graph_or_exit(graph):  # type: ignore[no-untyped-def]
    """Load *graph*, converting load failures to a structured stderr line.

    A corrupt/truncated ``graph.json`` (``json.JSONDecodeError``) or a graph
    written by a newer Weld (``SchemaVersionError``) used to escape
    ``Graph.load`` as an unhandled traceback. This wraps the load so those
    cases emit the shared one-line ``error[<code>]: ... | hint: ...`` contract
    and exit nonzero. Any other exception is re-raised unchanged -- we never
    mislabel an unrelated bug as a graph problem. Returns *graph* for chaining.
    """
    from weld._errors import classify_graph_load_error, format_error_line

    try:
        graph.load()
    except Exception as exc:  # noqa: BLE001 - classify then re-raise non-graph
        code, detail = classify_graph_load_error(exc, getattr(graph, "_path", Path()))
        if code is None:
            raise
        sys.stderr.write(format_error_line(code, detail) + "\n")
        sys.exit(1)
    return graph


def emit_node_lookup(
    args, data: dict, renderer, emit: Callable[..., None],
) -> None:
    """Emit a node-lookup result, exiting nonzero on a ``node not found``.

    ``Graph.context`` / ``Graph.callers`` return ``{"error": "node not
    found: ..."}`` for an unknown id but the CLI historically rendered that
    as a success (exit 0). The structured-error contract requires a bad node
    id to be a real failure: stamp the stable ``node_not_found``
    ``error_code`` onto the payload and exit nonzero after emitting via
    *emit* (the caller's renderer-aware writer). A resolved result is emitted
    unchanged.
    """
    from weld._errors import NODE_NOT_FOUND

    if isinstance(data, dict) and isinstance(data.get("error"), str) \
            and "not found" in data["error"]:
        emit(args, {**data, "error_code": NODE_NOT_FOUND}, renderer)
        sys.exit(1)
    emit(args, data, renderer)
