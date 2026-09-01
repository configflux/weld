"""Shared structured-error layer for the CLI and MCP surfaces.

Agents abandon tools that fail opaquely. This module is the single source of
the machine-readable error vocabulary so both the CLI (one-line ``error_code``
+ ``hint`` on stderr, nonzero exit) and the MCP server (a structured payload
instead of a raised exception / transport crash) emit *identical* codes.

It extends the pattern that ``weld._mcp_guard`` already established for the
missing-graph case (``graph_missing`` -> "Run: wd init"). The code list:

* ``graph_missing``   -- ``.weld/graph.json`` is absent (first run).
* ``graph_corrupt``   -- the file exists but is not valid JSON (truncated /
  half-written / hand-edited), the path exists but is not a regular file
  (e.g. a directory), or the parsed payload is missing (or has the wrong
  type for) ``nodes``/``edges``. Surfaced from ``json.JSONDecodeError``,
  ``IsADirectoryError``, or ``weld._graph_schema.GraphShapeError``.
* ``schema_mismatch`` -- ``meta.schema_version`` is newer than this build can
  read. Surfaced from ``weld._graph_schema.SchemaVersionError``.
* ``node_not_found``  -- a requested node id resolves to nothing.
* ``invalid_enrichment`` -- a write carries a ``props.enrichment`` record that
  is missing required fields, which discovery would silently drop (ADR 0097).
* ``root_out_of_bounds`` -- a request asked a server to answer from a
  directory outside the repository it serves (ADR 0096 §4).
* ``file_index_missing`` -- ``.weld/file-index.json`` is absent, so ``find``
  has no artifact to search and its empty result says nothing about the term.

Safety contract (ADR 0025 trust posture / ADR 0035 local-only no-leak): the
*detail* attached to a corrupt-graph error is derived only from the parser's
own positional metadata (byte offset, line, column) -- it never echoes the
raw bytes that failed to parse, so a secret living in a half-written graph
cannot leak into stderr, terminal scrollback, or an MCP payload. The
schema-mismatch message is operator-facing structural text (a version number
and the word "upgrade") and is safe to surface verbatim. The same rule governs
``invalid_enrichment``: a rejected record can hold arbitrary author text, so
the detail names the missing *field names* and never echoes their values.
``root_out_of_bounds`` goes one step further: its summary is a constant, so a
refused request cannot reflect the path it asked for and the refusal cannot be
used to probe which directories exist on the server's disk.

``graph_missing`` is the one code whose detail is derived from a *path* the
caller named -- :func:`weld._mcp_guard.missing_graph_payload` appends the
worktree-seeding cause for the root a read was answered from. It is safe for
the ``root_out_of_bounds`` reason, one step earlier: what
:func:`weld._worktree_seed.seed_blocked_reason` returns is a fixed constant
selected by a predicate, never interpolated from the path. So the payload
distinguishes only whether the answering checkout is a linked worktree
missing its config -- a property of the repository the server already
serves, not of the requested path's existence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from weld._safe_text import sanitize_terminal_line

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
#: A write carries a ``props.enrichment`` that is not a structurally-complete
#: record, so discovery would silently drop it (ADR 0097).
INVALID_ENRICHMENT = "invalid_enrichment"
#: A request named a root the serving process may not answer from: not an
#: existing directory of the same repository (ADR 0096 §4). Server surfaces
#: only -- an operator running the CLI already has the process's own
#: filesystem authority, so there is nothing there to bound.
ROOT_OUT_OF_BOUNDS = "root_out_of_bounds"
#: The tool *has* a graph but a required computation input is absent, so the
#: empty/zero result carries no information about the question asked (ADR 0134).
#: The one genuinely-new distinction the cannot-answer contract adds: ``impact``
#: on a ``repo:`` node in a root graph with no cross-repo edges cannot compute
#: dependents at all, so "0 dependents, Risk: LOW" is a fabricated verdict, not a
#: measured zero. Surfaced by ``impact`` as ``Risk: UNKNOWN``. Distinct from a
#: measured empty result, which stays a correct exit-0 answer.
RESULT_UNKNOWN = "result_unknown"
#: ``.weld/file-index.json`` is absent, so ``find`` has nothing to search
#: (ADR 0134). The sibling of ``graph_missing`` for the *other* artifact a
#: read can answer from: ``find`` never needed a graph, but it does need an
#: index, and an index that was never built makes "no matches" a statement
#: about weld's own state rather than about the term. At a federation root
#: the condition is that no index exists anywhere the fan-out reaches --
#: root or child.
FILE_INDEX_MISSING = "file_index_missing"

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
    INVALID_ENRICHMENT: (
        "props.enrichment needs non-empty provider, model, timestamp and "
        'description. For an agent-written record use provider "manual", '
        'model "agent-reviewed" and an ISO-8601 UTC timestamp.'
    ),
    ROOT_OUT_OF_BOUNDS: (
        "root must name an existing directory in the same repository the "
        "server was started against -- a linked worktree or the main "
        "checkout. Omit root to use the server's own root; child repos of a "
        "polyrepo workspace are not addressable this way."
    ),
    RESULT_UNKNOWN: (
        "No cross-repo resolver is wired, so cross-repo dependents cannot be "
        "computed. Declare one under cross_repo_strategies in "
        ".weld/workspaces.yaml, then re-run wd discover."
    ),
    FILE_INDEX_MISSING: "Run: wd discover.",
}

#: Default human-readable summary per code, used when no parser detail is
#: available. The corrupt/schema summaries are intentionally generic so the
#: surface never depends on unsafe content.
_DEFAULT_ERROR: dict[str, str] = {
    GRAPH_MISSING: "No Weld graph found.",
    GRAPH_CORRUPT: "Weld graph file is corrupt (invalid JSON).",
    SCHEMA_MISMATCH: "Weld graph schema version is unsupported.",
    NODE_NOT_FOUND: "Node not found.",
    INVALID_ENRICHMENT: "Enrichment record is structurally incomplete.",
    ROOT_OUT_OF_BOUNDS: "Requested root is outside the served repository.",
    RESULT_UNKNOWN: (
        "Risk: UNKNOWN -- cross-repo dependents cannot be computed for this "
        "repo node because no cross-repo resolver is wired."
    ),
    FILE_INDEX_MISSING: "No Weld file index found.",
}


def default_summary(code: str) -> str:
    """Return the standing human-readable summary for *code*.

    The read accessor for :data:`_DEFAULT_ERROR`, so a caller building a
    *detail* that **extends** the standing summary rather than replacing it
    outright does not have to restate the summary's wording. That is what
    ``graph_missing`` does when it can name why this particular checkout has
    no graph: the headline every consumer already matches on has to survive
    the added line, and a second copy of it is how that stops being true.
    """
    return _DEFAULT_ERROR.get(code, "Weld error.")


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
    error = detail or default_summary(code)
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

    Some details legitimately interpolate a *node id*
    (``invalid_enrichment`` names the node whose write was refused), and node
    ids are derived from scanned paths and symbol names -- so a hostile repo
    can smuggle ANSI/control bytes into one. This is the stderr write boundary
    for every code, so the terminal-safety escape lands here
    (:mod:`weld._safe_text`), in the single-line variant: a CR or LF in the
    detail must not overwrite the line or forge a second diagnostic. Only the
    *text* surface is escaped; :func:`structured_payload` stays raw so the
    MCP/JSON contract is unchanged.
    """
    summary = detail or default_summary(code)
    hint = ERROR_HINTS.get(code, "")
    return sanitize_terminal_line(f"error[{code}]: {summary} | hint: {hint}")


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


#: Detail for a graph path that exists but is not a regular file (e.g. a
#: directory). Fixed and path-free by construction -- unlike the JSON-decode
#: and schema-mismatch details, it carries no dynamic content at all, so
#: there is nothing derived from the exception (which, via ``OSError.
#: filename``, would otherwise embed the absolute path) to redact.
_NOT_A_FILE_DETAIL = "graph.json is not a regular file (found a directory)."


def classify_graph_load_error(
    exc: BaseException, path: Path,
) -> tuple[str | None, str]:
    """Map a graph-load exception to ``(error_code, safe_message)``.

    Recognizes the load-time failures both surfaces must handle:

    * :class:`json.JSONDecodeError` -> :data:`GRAPH_CORRUPT` with a position
      detail (never the raw bytes).
    * :class:`IsADirectoryError` -> :data:`GRAPH_CORRUPT` with a fixed,
      path-free detail. ``Graph.load`` gates on ``Path.exists()`` (true for a
      directory too), so a directory left at ``.weld/graph.json`` raises this
      from the ``read_text()`` call rather than from a JSON parse -- but the
      graph is equally unusable and the remedy is identical (``wd
      discover``), so it is classified the same as corrupt rather than
      escaping as a raw exception carrying a filesystem path (bd 9yc8).
    * :class:`weld._graph_schema.GraphShapeError` -> :data:`GRAPH_CORRUPT`
      with the validator's own message. Raised when a syntactically valid
      JSON object is missing ``nodes``/``edges`` or holds the wrong type for
      either (e.g. ``{"meta": {...}}`` alone) -- structurally unusable the
      same way a decode failure is, and the message already names only a
      fixed key and a Python type, never file content, so it needs no
      further redaction (unlike the schema-mismatch path below).
    * :class:`weld._graph_schema.SchemaVersionError` -> :data:`SCHEMA_MISMATCH`
      with the structural version detail, the absolute graph path stripped
      (see :func:`_safe_schema_detail`).

    Returns ``(None, "")`` for any other exception so callers re-raise it
    unchanged rather than mislabeling an unrelated bug. *path* is the
    ``.weld/graph.json`` location used to redact the schema message.
    """
    # Import locally to avoid a runtime import cycle (graph -> _errors).
    from weld._graph_schema import GraphShapeError, SchemaVersionError

    if isinstance(exc, json.JSONDecodeError):
        return GRAPH_CORRUPT, _safe_json_detail(exc)
    if isinstance(exc, IsADirectoryError):
        return GRAPH_CORRUPT, _NOT_A_FILE_DETAIL
    if isinstance(exc, GraphShapeError):
        return GRAPH_CORRUPT, str(exc)
    if isinstance(exc, SchemaVersionError):
        return SCHEMA_MISMATCH, _safe_schema_detail(exc, path)
    return None, ""
