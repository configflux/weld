"""Graph-load guards for the weld MCP server.

When ``.weld/graph.json`` is absent, the underlying ``Graph`` silently
constructs an empty in-memory graph and graph-backed read tools return
empty payloads. That is hostile to MCP clients: agents that prompted
``weld_query`` get back an empty match list with no signal that the
graph has not been built. The CLI already handles this via
:func:`weld._graph_cli.ensure_graph_exists` -- this module mirrors that
contract at the MCP boundary so both surfaces emit identical guidance.

It also owns the boundary that converts a graph that exists but cannot be
loaded -- a corrupt/truncated ``graph.json`` (``json.JSONDecodeError``) or
one written by a newer Weld (``SchemaVersionError``) -- into the same shared
structured payload instead of letting the exception escape as a transport
crash (:func:`load_error_payload`), and the last-resort serializer that
guarantees the stdio session never dies on a single bad call
(:func:`serialize_dispatch`).

Federated workspaces (``.weld/workspaces.yaml`` present at root) are
exempt: the federation loader reports per-child status via
``children_status``. ``weld_find`` is also exempt because it reads the
file index, not the graph (matches CLI behavior).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from weld._errors import (
    ERROR_HINTS,
    GRAPH_MISSING,
    NODE_NOT_FOUND,
    classify_graph_load_error,
    structured_payload,
)
from weld.workspace_state import find_workspaces_yaml as _find_workspaces_yaml


def missing_graph_payload(retry_cmd: str = "weld_query / weld_context / ...") -> dict:
    """Structured actionable-error payload for missing graphs.

    Single-sourced from :func:`weld._errors.structured_payload` so the
    ``error_code`` / ``hint`` vocabulary stays identical to the CLI surface
    and to the other graph-load failure codes (``graph_corrupt`` /
    ``schema_mismatch``). Wording mirrors
    :func:`weld._graph_cli.missing_graph_message`; the stable ``error_code``
    lets MCP clients render the hint without parsing the human-readable
    message.
    """
    return structured_payload(GRAPH_MISSING, retry_cmd=retry_cmd)


def graph_present(root: Path | str) -> bool:
    """Return ``True`` when graph-backed MCP tools can safely load *root*.

    Single-repo root requires ``.weld/graph.json``. A federated root
    (``.weld/workspaces.yaml`` present) is always considered ready --
    the federation layer reports per-child status separately. Callers
    that hit ``False`` should short-circuit with
    :func:`missing_graph_payload`.
    """
    root_path = Path(root)
    if (root_path / ".weld" / "graph.json").exists():
        return True
    if _find_workspaces_yaml(root_path) is not None:
        return True
    return False


def load_error_payload(exc: BaseException, root: Path | str) -> dict | None:
    """Return a structured payload for a graph-load exception, else ``None``.

    A ``json.JSONDecodeError`` (corrupt/truncated graph) or a
    ``SchemaVersionError`` (graph from a newer Weld) raised from a tool
    handler becomes the shared ``error_code`` + ``hint`` payload. Any other
    exception returns ``None`` so the caller re-raises it unchanged -- an
    unrelated bug must not be mislabeled as a graph problem.
    """
    code, detail = classify_graph_load_error(exc, Path(root))
    if code is None:
        return None
    return structured_payload(code, detail=detail)


def stamp_node_not_found(result: dict) -> dict:
    """Add the shared ``node_not_found`` ``error_code`` + ``hint`` in place.

    ``Graph.context`` / ``Graph.callers`` (and the federated variants) report
    an unknown node id as ``{"error": "node not found: <id>", ...}`` with no
    machine-readable code. This mirrors :func:`weld._graph_cli_errors.\
emit_node_lookup` -- the CLI's ``context`` / ``callers`` commands stamp the
    same :data:`weld._errors.NODE_NOT_FOUND` code -- so both surfaces emit an
    identical code an agent can branch on.

    The predicate is the CLI's: a dict whose ``error`` is a string containing
    ``"not found"``. That deliberately excludes ``weld_path`` (which signals a
    miss via ``{"path": None, "reason": ...}`` and is *not* CLI-stamped) and
    the federated ``child not available`` error, keeping exact parity. The
    existing human-readable ``error`` text and any sibling fields (``symbol``,
    ``callers``, ``edges``) are preserved -- the code/hint are purely additive,
    and the only id echoed is the caller-supplied one already in ``error``.
    Already-stamped or non-error payloads pass through untouched.
    """
    if not isinstance(result, dict) or "error_code" in result:
        return result
    error = result.get("error")
    if isinstance(error, str) and "not found" in error:
        result["error_code"] = NODE_NOT_FOUND
        result["hint"] = ERROR_HINTS[NODE_NOT_FOUND]
    return result


def serialize_dispatch(
    dispatch: Callable[..., dict],
    tool_name: str,
    arguments: dict | None,
    root: Path | str,
) -> str:
    """Run *dispatch* and serialize the result (or error) to a JSON string.

    SDK-free seam for the stdio ``_call_tool`` handler. It guarantees the
    transport never crashes: graph-load failures are already converted to a
    structured payload upstream, an unknown tool name (``KeyError``) becomes
    an ``{"error": ...}`` payload, and any other unexpected exception is also
    serialized rather than torn through the long-lived stdio session.
    """
    try:
        result: Any = dispatch(tool_name, arguments, root=root)
    except KeyError as exc:
        result = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - last-resort transport guard
        result = {"error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(result, ensure_ascii=False)
