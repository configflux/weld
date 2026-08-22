"""Renderer-aware writers for the graph CLI commands.

The one place ``wd``'s graph commands turn a payload into bytes on stdout:
JSON under ``--json`` via :func:`weld._safe_text.dumps_safe_json`, otherwise
the renderer's text passed through
:func:`weld._safe_text.sanitize_terminal_text`. Holding that as a single
chokepoint is the point -- a payload cannot reach a terminal unsanitized by
arriving along a different dispatch path.

``--json`` values are unchanged by the safe emitter: it only escapes DEL/C1
into ``\\uXXXX``, which parses back to the identical string, so machine
consumers and the CLI/MCP byte-identity parity contract are both untouched
while an operator piping ``--json`` into a pager stops being a live
control-sequence channel.

Both dispatchers route through here and neither is imported back:
:mod:`weld._graph_cli_single` imports the writers directly, while
:func:`weld._graph_cli.main` injects them into
:func:`weld._graph_cli_federated.run_federated_cli`. That keeps this module
a leaf of the CLI layer.
"""

from __future__ import annotations

import sys

from weld._safe_text import dumps_safe_json, sanitize_terminal_text


def _out(data: object) -> None:
    sys.stdout.write(dumps_safe_json(data, indent=2) + "\n")


def _emit(args, data: object, renderer) -> None:
    """Raw JSON when ``--json``, else text sanitized per :mod:`weld._safe_text`."""
    if getattr(args, "as_json", False):
        _out(data)
        return
    sys.stdout.write(sanitize_terminal_text(renderer(data)))


def _emit_node_lookup(args, data: dict, renderer) -> None:
    """Thin adapter to :func:`weld._graph_cli_errors.emit_node_lookup`.

    Passes :func:`_emit` so the error module stays free of a back-import.
    """
    from weld._graph_cli_errors import emit_node_lookup

    emit_node_lookup(args, data, renderer, _emit)
