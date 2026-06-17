"""Shared structured-error layer for the CLI and MCP surfaces.

Agents abandon tools that fail opaquely. This module is the single source of
the machine-readable error vocabulary so both the CLI (one-line ``error_code``
+ ``hint`` on stderr, nonzero exit) and the MCP server (a structured payload
instead of a raised exception / transport crash) emit *identical* codes.

It extends the pattern that ``weld._mcp_guard`` already established for the
missing-graph case (``graph_missing`` -> "Run: wd init"). The code list:

* ``graph_missing``   -- ``.weld/graph.json`` is absent (first run).
* ``graph_corrupt``   -- the file exists but is not valid JSON (truncated /
  half-written / hand-edited). Surfaced from ``json.JSONDecodeError``.
* ``schema_mismatch`` -- ``meta.schema_version`` is newer than this build can
  read. Surfaced from ``weld._graph_schema.SchemaVersionError``.
* ``node_not_found``  -- a requested node id resolves to nothing.

Safety contract (ADR 0025 trust posture / ADR 0035 local-only no-leak): the
*detail* attached to a corrupt-graph error is derived only from the parser's
own positional metadata (byte offset, line, column) -- it never echoes the
raw bytes that failed to parse, so a secret living in a half-written graph
cannot leak into stderr, terminal scrollback, or an MCP payload. The
schema-mismatch message is operator-facing structural text (a version number
and the word "upgrade") and is safe to surface verbatim.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# -- Error-code vocabulary -------------------------------------------------
#: ``.weld/graph.json`` absent. Mirrors the legacy ``_mcp_guard`` code so the
#: MCP and CLI surfaces stay byte-identical.
GRAPH_MISSING = "graph_missing"
#: File present but not valid JSON (truncated / corrupt / hand-edited).
GRAPH_CORRUPT = "graph_corrupt"
#: ``meta.schema_version`` newer than this build supports.
SCHEMA_MISMATCH = "schema_mismatch"
#: A requested node id resolves to no node.
NODE_NOT_FOUND = "node_not_found"

#: Stable, copy-pasteable remediation hint per code. Wording is matched
#: against by tests and onboarding docs -- keep it stable.
ERROR_HINTS: dict[str, str] = {
    GRAPH_MISSING: "Run: wd init (if no config), then wd discover.",
    GRAPH_CORRUPT: "graph.json is not valid JSON. Rebuild it: wd discover.",
    SCHEMA_MISMATCH: (
        "graph.json was written by a newer Weld. Upgrade weld, or rebuild "
        "with this version: wd discover."
    ),
    NODE_NOT_FOUND: "Check the node id (wd query <term> to find it).",
}

#: Default human-readable summary per code, used when no parser detail is
#: available. The corrupt/schema summaries are intentionally generic so the
#: surface never depends on unsafe content.
_DEFAULT_ERROR: dict[str, str] = {
    GRAPH_MISSING: "No Weld graph found.",
    GRAPH_CORRUPT: "Weld graph file is corrupt (invalid JSON).",
    SCHEMA_MISMATCH: "Weld graph schema version is unsupported.",
    NODE_NOT_FOUND: "Node not found.",
}


def structured_payload(
    code: str,
    *,
    detail: str | None = None,
    retry_cmd: str | None = None,
) -> dict:
    """Return the MCP-shaped error payload for *code*.

    Shape is the legacy ``_mcp_guard.missing_graph_payload`` contract:
    ``{"error", "error_code", "hint"}`` plus an optional ``"retry"`` field.
    *detail* (when present) replaces the default ``error`` summary -- callers
    pass only *safe* detail (e.g. a byte offset), never raw file content. The
    ``hint`` always comes from the stable :data:`ERROR_HINTS` vocabulary.
    """
    error = detail or _DEFAULT_ERROR.get(code, "Weld error.")
    payload: dict = {
        "error": error,
        "error_code": code,
        "hint": ERROR_HINTS.get(code, ""),
    }
    if retry_cmd is not None:
        payload["retry"] = f"Then retry: {retry_cmd}."
    return payload


def format_error_line(code: str, detail: str | None = None) -> str:
    """Format the one-line CLI stderr string for *code*.

    Shape: ``error[<code>]: <summary> | hint: <hint>`` on a single line, so an
    agent parsing stderr can extract the code and the remediation without
    multi-line scraping. *detail* is the safe summary (parser position for
    corrupt, the version text for schema mismatch); it never carries raw file
    bytes.
    """
    summary = detail or _DEFAULT_ERROR.get(code, "Weld error.")
    hint = ERROR_HINTS.get(code, "")
    return f"error[{code}]: {summary} | hint: {hint}"


def _safe_json_detail(exc: json.JSONDecodeError) -> str:
    """Localize a JSON parse failure *without* echoing the source bytes.

    ``json.JSONDecodeError`` carries ``msg`` (a generic reason like
    "Expecting ':' delimiter"), plus ``pos`` / ``lineno`` / ``colno`` -- all
    positional metadata, none of which is file content. We deliberately do
    NOT include ``exc.doc`` (the raw text) so secrets in a half-written graph
    never reach the surface.
    """
    return (
        f"invalid JSON: {exc.msg} "
        f"(line {exc.lineno}, column {exc.colno}, byte {exc.pos})"
    )


#: Matches the ``graph.json at <path> has`` clause that ``SchemaVersionError``
#: (ADR 0012) prepends to its message. ``<path>`` is greedy-free (no "has")
#: so only the path token is replaced.
_SCHEMA_PATH_CLAUSE = re.compile(r"graph\.json at .*? has ")


def _safe_schema_detail(exc: BaseException, path: Path) -> str:
    """Schema-mismatch summary with the absolute graph path stripped.

    The underlying ``SchemaVersionError`` (ADR 0012) embeds the *absolute*
    path to ``graph.json`` so an operator can read the mismatch off a log
    line. That is fine on a private log but the message also reaches an MCP
    client; an absolute path can reveal a home directory / username. We keep
    the operator-useful structural signal (the observed and supported
    ``schema_version`` numbers and the upgrade guidance) but replace the
    ``graph.json at <path> has`` clause with a path-free ``graph.json has``
    so neither surface leaks the filesystem location. The explicit *path*
    replacement is kept as a belt-and-suspenders fallback for any message
    variant the regex does not anchor.
    """
    redacted = _SCHEMA_PATH_CLAUSE.sub("graph.json has ", str(exc), count=1)
    return redacted.replace(str(path), "graph.json")


def classify_graph_load_error(
    exc: BaseException, path: Path,
) -> tuple[str | None, str]:
    """Map a graph-load exception to ``(error_code, safe_message)``.

    Recognizes the two load-time failures both surfaces must handle:

    * :class:`json.JSONDecodeError` -> :data:`GRAPH_CORRUPT` with a position
      detail (never the raw bytes).
    * :class:`weld._graph_schema.SchemaVersionError` -> :data:`SCHEMA_MISMATCH`
      with the structural version detail, the absolute graph path stripped
      (see :func:`_safe_schema_detail`).

    Returns ``(None, "")`` for any other exception so callers re-raise it
    unchanged rather than mislabeling an unrelated bug. *path* is the
    ``.weld/graph.json`` location used to redact the schema message.
    """
    # Import locally to avoid a runtime import cycle (graph -> _errors).
    from weld._graph_schema import SchemaVersionError

    if isinstance(exc, json.JSONDecodeError):
        return GRAPH_CORRUPT, _safe_json_detail(exc)
    if isinstance(exc, SchemaVersionError):
        return SCHEMA_MISMATCH, _safe_schema_detail(exc, path)
    return None, ""
