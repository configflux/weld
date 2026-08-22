"""Name-based tool dispatch for the weld MCP server.

Split out of :mod:`weld.mcp_server` to keep that module -- the SDK-free tool
adapters and their registry -- under the line-count cap, the same reason the
stdio transport lives in :mod:`weld._mcp_stdio`. What lands here is the call
path a tool *name* travels: request-root resolution, the registry lookup,
graph-load error conversion, the freshness stamp, and the telemetry record.
The adapters themselves stay in :mod:`weld.mcp_server`, which wraps all three
entry points below (injecting its own ``build_tools``) -- so
``weld.mcp_server.dispatch`` and ``weld.mcp_server.dispatch_to_text_payload``
remain the import path for tests, :mod:`weld._mcp_stdio`, and every other
caller.

Nothing here imports :mod:`weld.mcp_server`, even lazily (ADR 0130
disposition #7): a function-local import back into the module that wraps
this one is exactly what used to make the two a cycle. Instead, every
function below takes the live tool registry as an explicit *tools_provider*
parameter, dependency-injected by the composition root
(:mod:`weld.mcp_server`) at its one real call site -- never bound as a
def-time default, which would either recreate the import or freeze a stale
reference.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Callable

from weld._mcp_guard import (
    load_error_payload as _load_error_payload,
    resolve_dispatch_root as _resolve_dispatch_root,
    serialize_dispatch as _serialize_dispatch,
    stamp_node_not_found as _stamp_node_not_found,
)
from weld._mcp_read import (
    FRESHNESS_TOOLS as _FRESHNESS_TOOLS,
    stamp_freshness as _stamp_freshness,
)


def _dispatch_inner(
    tool_name: str, arguments: dict | None, *, root: Path | str = ".",
    tools_provider: Callable[[], list],
) -> dict:
    """Select the tool by name and invoke it. Raises ``KeyError`` on miss.

    *root* is already resolved, and *arguments* no longer carries the
    request's own ``root`` -- :func:`dispatch` owns both, because a handler
    declaring ``root=`` cannot also receive it in ``**args``. *tools_provider*
    is the live tool registry (:func:`weld.mcp_server.build_tools`), supplied
    by :func:`dispatch` -- see the module docstring for why this is a
    parameter and not an import.

    A corrupt/truncated ``graph.json`` (``json.JSONDecodeError``), a graph
    written by a newer Weld (``SchemaVersionError``), or a syntactically
    valid ``graph.json`` missing (or with the wrong type for)
    ``nodes``/``edges`` (``GraphShapeError``) raised from ``Graph.load``
    inside a tool handler is converted to the shared structured-error
    payload (``error_code`` + ``hint`` via :mod:`weld._errors`) instead of
    escaping -- so the same code an unhandled ``JSONDecodeError`` used to
    produce a transport crash now returns a parseable error to the client. A node-not-found result
    (``weld_context`` / ``weld_callers`` on an unknown id) is stamped with the
    shared ``node_not_found`` code by :func:`weld._mcp_guard.\
stamp_node_not_found`, matching the CLI. A successful graph-backed *read*
    payload (the tools in :data:`weld._mcp_read.FRESHNESS_TOOLS`) is stamped
    with the additive ``freshness`` object (``{stale, commits_behind}``) by
    :func:`weld._mcp_read.stamp_freshness` so the agent never consumes a stale
    answer without a signal (bd 85tb.3); the stamp no-ops on any error payload.
    Unknown tool names still raise ``KeyError`` (the registry contract); the
    stdio layer turns that into a payload via :func:`dispatch_to_text_payload`.
    """
    args = dict(arguments or {})
    for tool in tools_provider():
        if tool.name == tool_name:
            try:
                result = _stamp_node_not_found(tool.handler(**args, root=root))
            except Exception as exc:  # noqa: BLE001 - classify graph-load only
                payload = _load_error_payload(exc, root)
                if payload is None:
                    raise
                return payload
            if tool_name in _FRESHNESS_TOOLS:
                result = _stamp_freshness(result, root)
            return result
    raise KeyError(f"unknown weld MCP tool: {tool_name}")


def dispatch(
    tool_name: str, arguments: dict | None, *, root: Path | str = ".",
    tools_provider: Callable[[], list],
) -> dict:
    """Dispatch a tool call by name (used by tests and ``run_stdio``).

    Which checkout answers is settled here, once, by
    :func:`weld._mcp_guard.resolve_dispatch_root`: a request may name a
    ``root``, bounded to checkouts of the repository the server was started
    against, and that argument is removed from *arguments* before the handler
    sees it (ADR 0096 §4). Everything downstream -- the handler, the freshness
    stamp, and the telemetry event -- then uses the *resolved* root. A refused
    root is served the structured payload instead, and is still recorded (as a
    call against the serving root) so a run of them is visible in the ledger.

    *tools_provider* is the live tool registry -- required, not defaulted:
    the only caller that matters in production is
    :func:`weld.mcp_server.dispatch`, which always supplies its own
    ``build_tools`` explicitly (ADR 0130 disposition #7), so a missing value
    here is a caller bug worth failing loudly on rather than papering over.

    Wraps :func:`_dispatch_inner` with :class:`weld._telemetry.Recorder`
    so every MCP tool call appends one event (ADR 0035). The Recorder
    swallows its own writer errors -- telemetry failures never alter the
    dispatch result or replace the original exception. MCP has no exit
    code, so we set the schema sentinel ``exit_code = -1``. Raises
    ``KeyError`` when *tool_name* is not registered.
    """
    from weld._telemetry import Recorder

    args = dict(arguments or {})
    rroot, refused = _resolve_dispatch_root(tool_name, args, root)
    # A refusal leaves rroot unset; Path(root) is safe there because the
    # resolver builds the serving root before it inspects the request.
    with Recorder(
        surface="mcp", command=tool_name, flags=[],
        root=rroot if rroot is not None else Path(root),
    ) as rec:
        rec.set_exit_code(-1)  # ADR 0035 MCP sentinel; no exit concept.
        if refused is not None:
            return refused
        return _dispatch_inner(
            tool_name, args, root=rroot, tools_provider=tools_provider)


def dispatch_to_text_payload(
    tool_name: str, arguments: dict | None, *, root: Path | str = ".",
    tools_provider: Callable[[], list],
) -> str:
    """Dispatch and return a JSON string the stdio layer wraps in TextContent.

    SDK-free seam used by the stdio ``_call_tool`` handler. Graph-load
    failures are already converted to a structured payload by
    :func:`_dispatch_inner`; the remaining transport guarantees (unknown
    tool -> payload, last-resort serialization) live in
    :func:`weld._mcp_guard.serialize_dispatch`, whose ``dispatch`` callback
    contract takes exactly ``(tool_name, arguments, root=...)`` -- so
    *tools_provider* is bound into a fresh :func:`functools.partial` here
    (built at call time, never a def-time default) rather than threaded
    through that seam.
    """
    return _serialize_dispatch(
        partial(dispatch, tools_provider=tools_provider),
        tool_name, arguments, root,
    )
