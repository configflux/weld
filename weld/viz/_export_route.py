"""HTTP wrapper around :func:`weld.export.export` for the viz server.

The ``/api/export`` route in :mod:`weld.viz.server` delegates here so the
allowlist + filename logic lives next to the test surface (bd h6z0.14)
rather than inside the request handler. Reuses ``weld.export``'s pure
serializers as-is -- no new format implementations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from weld.export import export

# Only single-file string formats are exposed via HTTP. ``wiki`` (a
# multi-file directory exporter, ADR 0053) is intentionally excluded:
# it requires an output directory and is not suitable for an HTTP
# response. Unknown values 400 fast before the graph loader runs.
_ALLOWED_FORMATS = ("mermaid", "dot", "d2")

# Each format maps to a (mime, extension) pair. ``text/plain`` is the
# pragmatic content-type for all three -- they are syntax-coloured by
# downstream editors based on extension, not mime.
_FORMAT_META: dict[str, tuple[str, str]] = {
    "mermaid": ("text/plain; charset=utf-8", "mmd"),
    "dot": ("text/plain; charset=utf-8", "dot"),
    "d2": ("text/plain; charset=utf-8", "d2"),
}

# Filename charset: keep letters, digits, dash, underscore, dot. Anything
# else (including the colon/slash separators used in node ids) collapses
# to a single underscore so the result is filesystem-safe everywhere
# the browser drops the download.
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_part(value: str) -> str:
    """Sanitize a string fragment for inclusion in a download filename."""
    cleaned = _FILENAME_UNSAFE.sub("_", value).strip("_")
    return cleaned or "graph"


def _build_filename(node_id: str | None, extension: str) -> str:
    """Build a sensible download filename including the focused node id."""
    if node_id:
        return f"weld-graph-{_safe_filename_part(node_id)}.{extension}"
    return f"weld-graph.{extension}"


def export_response(
    root: Path | str,
    params: dict[str, Any],
) -> tuple[bytes, str, str]:
    """Render a graph export for the HTTP layer.

    Returns ``(body, content_type, filename)``. Raises :class:`ValueError`
    when the requested format is not in the allowlist or when
    :func:`weld.export.export` rejects the request -- the caller maps the
    exception to a 400 response.
    """
    raw_fmt = params.get("format")
    fmt = str(raw_fmt).strip() if raw_fmt not in (None, "") else ""
    if fmt not in _ALLOWED_FORMATS:
        raise ValueError(
            f"unknown export format: {fmt!r} "
            f"(allowed: {', '.join(_ALLOWED_FORMATS)})"
        )

    node_id_raw = params.get("node_id")
    node_id = str(node_id_raw).strip() if node_id_raw not in (None, "") else ""
    depth_raw = params.get("depth")
    try:
        depth = int(depth_raw) if depth_raw not in (None, "") else 1
    except (TypeError, ValueError):
        depth = 1
    # weld.export.export() loads the graph itself and falls through to
    # subgraph extraction when ``node_id`` is provided. The pure
    # serializers handle empty subgraphs (missing node id) by returning
    # the format's empty document, which is a deliberate "no surprise"
    # behavior for the HTTP caller.
    body = export(fmt, node_id=node_id or None, depth=depth, root=Path(root))

    ctype, extension = _FORMAT_META[fmt]
    filename = _build_filename(node_id or None, extension)
    return body.encode("utf-8"), ctype, filename
