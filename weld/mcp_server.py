"""Stdio MCP server exposing wd query helpers as structured tools.

Thin adapter over :mod:`weld.graph`, :mod:`weld.brief`, and
:mod:`weld.file_index` (ADR 0015). Each tool handler loads a fresh
:class:`weld.graph.Graph` and delegates to the same helper the CLI uses.
The ``mcp`` SDK is optional -- only :func:`run_stdio` requires it.

Tools: weld_query, weld_find, weld_context, weld_path, weld_brief,
weld_stale, weld_callers, weld_references, weld_trace, weld_export,
weld_impact, weld_enrich.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from weld._mcp_guard import (
    graph_present as _graph_present,
    load_error_payload as _load_error_payload,
    missing_graph_payload as _missing_graph_payload,
    serialize_dispatch as _serialize_dispatch,
    stamp_node_not_found as _stamp_node_not_found,
)
from weld._mcp_read import (
    FRESHNESS_TOOLS as _FRESHNESS_TOOLS,
    load_graph_for_read as _load_graph,
    stamp_freshness as _stamp_freshness,
)
from weld.brief import brief as _brief
from weld.read import read_query as _read_query
from weld.read import shape_brief as _shape_brief
from weld.read import shape_read_envelope as _shape_read_envelope
from weld.diff import load_and_diff as _load_and_diff
from weld.federation import FederatedGraph as _FederatedGraph
from weld.federation_tools import (
    federated_callers as _federated_callers,
    federated_references as _federated_references,
    federated_stale as _federated_stale,
)
from weld.file_index import find_files as _find_files
from weld.file_index import load_file_index as _load_file_index
from weld.graph import Graph as _Graph
from weld.mcp_helpers import weld_enrich as _weld_enrich
from weld.mcp_helpers import weld_impact as _weld_impact
from weld.mcp_helpers import weld_review_guarded as _weld_review_guarded
from weld.mcp_helpers import weld_trace as _weld_trace
from weld.workspace_state import find_workspaces_yaml as _find_workspaces_yaml

# ---------------------------------------------------------------------------
# Tool descriptors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tool:
    """A lightweight, SDK-agnostic description of an MCP tool."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any] = field(repr=False)

def _attach_children_status(
    graph: _Graph | _FederatedGraph, result: dict,
) -> dict:
    """Attach ``children_status`` when *graph* is a ``FederatedGraph``.

    Single-repo callers see no change. Federated callers receive a mapping of
    child name -> status payload (``present`` / ``missing`` /
    ``uninitialized`` / ``corrupt``) so agents can tell which child repos are
    indexed vs degraded without probing each one.
    """
    if isinstance(graph, _FederatedGraph):
        result["children_status"] = graph.children_status()
    return result

# ---------------------------------------------------------------------------
# Tool implementations (pure adapters)
# ---------------------------------------------------------------------------

def weld_query(
    term: str, limit: int = 20, *,
    full_neighborhood: bool = False, full_size: bool = False,
    include_speculative: bool = False, root: Path | str = ".",
) -> dict:
    """Tokenized ranked search. Delegates to ``Graph.query``; see
    :func:`_attach_children_status` for the federated-only extra field.

    Shaped by the shared :func:`weld.read.read_query`, so the answer is
    identical to ``wd query`` (ADR 0083): the speculative-match filter drops
    ``origin=unresolved`` sentinels from ``matches`` unless
    ``include_speculative=True``, then the ADR 0078 diet + ADR 0082 byte budget
    apply (all reported in ``omitted_neighbors``). ``full_neighborhood=True``
    restores the raw neighborhood; ``full_size=True`` keeps the diet but skips
    the byte budget. Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_query")
    g = _load_graph(Path(root))
    envelope = _read_query(
        g.query(term, limit=limit), include_speculative=include_speculative,
        full=full_neighborhood, full_size=full_size)
    return _attach_children_status(g, envelope)

def weld_find(term: str, limit: int | None = None, *, root: Path | str = ".") -> dict:
    """File-index substring search. Delegates to ``weld.file_index.find_files``;
    at a federated root fans out across every child index (ADR 0089), matching
    ``wd find``. Negative ``limit`` is ignored (pre-change MCP tolerance)."""
    effective_limit = limit if limit is None or limit >= 0 else None
    root_path = Path(root)
    if _find_workspaces_yaml(root_path) is not None:
        from weld._federation_find import federated_find
        return federated_find(root_path, term, limit=effective_limit)
    return _find_files(_load_file_index(root_path), term, limit=effective_limit)

def weld_context(
    node_id: str, *, full_neighborhood: bool = False, full_size: bool = False,
    root: Path | str = ".",
) -> dict:
    """Node + 1-hop neighborhood. Delegates to ``Graph.context``; see
    :func:`_attach_children_status` for the federated-only extra field.

    Bounded read shaping (ADR 0082) applies by default via the shared
    :func:`weld.read.shape_read_envelope`; ``full_neighborhood=True`` restores
    the raw neighborhood and ``full_size=True`` skips only the byte budget. A
    node-not-found miss is returned unchanged. Missing-graph guard applies
    (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_context")
    g = _load_graph(Path(root))
    envelope = _shape_read_envelope(
        g.context(node_id), full=full_neighborhood, full_size=full_size)
    return _attach_children_status(g, envelope)

def weld_path(from_id: str, to_id: str, *, root: Path | str = ".") -> dict:
    """Shortest path between two nodes. Delegates to ``Graph.path``; see
    :func:`_attach_children_status` for the federated-only extra field.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_path")
    g = _load_graph(Path(root))
    return _attach_children_status(g, g.path(from_id, to_id))

def weld_brief(
    area: str, limit: int = 20, *, full_size: bool = False,
    root: Path | str = ".",
) -> dict:
    """Stable brief JSON for *area*. Delegates to ``weld.brief.brief``, then
    bounds it via the shared :func:`weld.read.shape_brief` (ADR 0082):
    edges are de-dangled to emitted bucket nodes and the ``weld_query`` byte
    budget applies; ``full_size=True`` returns the unbounded brief. In a
    federated workspace the graph is a
    :class:`~weld.federation.FederatedGraph`, so the brief spans child repos.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_brief")
    g = _load_graph(Path(root))
    return _shape_brief(_brief(g, area, limit=limit), full_size=full_size)

def weld_stale(*, root: Path | str = ".") -> dict:
    """Graph freshness vs git HEAD. Delegates to ``Graph.stale``.

    In a federated workspace the result includes a ``children`` dict
    mapping each child name to its stale result (or a graceful
    degradation payload for non-present children).
    """
    g = _load_graph(Path(root))
    if isinstance(g, _FederatedGraph):
        return _federated_stale(g)
    return g.stale()

def weld_callers(
    symbol_id: str, depth: int = 1, *, root: Path | str = ".",
) -> dict:
    """Return direct (and optionally transitive) callers of *symbol_id*.

    In a federated workspace, prefixed symbol IDs (``child<US>local_id``)
    are resolved within the named child graph. Missing-graph guard
    applies (single-repo root only).
    """
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_callers")
    g = _load_graph(Path(root))
    if isinstance(g, _FederatedGraph):
        return _federated_callers(g, symbol_id, depth=depth)
    return g.callers(symbol_id, depth=depth)

def weld_export(
    format: str, node_id: str | None = None, depth: int = 1,
    *, root: Path | str = ".",
) -> dict:
    """Export graph to a visualization format. Delegates to ``weld.export``.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_export")
    from weld.export import export
    try:
        output = export(format, node_id=node_id, depth=depth, root=root)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"format": format, "output": output}


def weld_references(symbol_name: str, *, root: Path | str = ".") -> dict:
    """Return callers + file-index references for a bare symbol *name*.

    In a federated workspace, references fan out across all present
    children with prefixed IDs. Missing-graph guard applies (single-repo
    root only).
    """
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_references")
    g = _load_graph(Path(root))
    if isinstance(g, _FederatedGraph):
        refs = _federated_references(g, symbol_name)
    else:
        refs = g.references(symbol_name)
    index = _load_file_index(Path(root))
    refs["files"] = _find_files(index, symbol_name).get("files", [])
    return refs

def weld_diff(*, root: Path | str = ".") -> dict:
    """Return the graph diff between previous and current discovery run.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_diff")
    return _load_and_diff(Path(root))


def weld_trace(
    *,
    term: str | None = None,
    node_id: str | None = None,
    depth: int = 2,
    seed_limit: int = 5,
    root: Path | str = ".",
) -> dict:
    """Protocol-aware cross-boundary slice. Delegates to
    :func:`weld.mcp_helpers.weld_trace`. Missing-graph guard applies."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_trace")
    return _weld_trace(
        term=term, node_id=node_id, depth=depth, seed_limit=seed_limit, root=root,
    )


def weld_impact(target: str, depth: int = 3, *, root: Path | str = ".") -> dict:
    """Reverse-dependency blast radius. Delegates to
    :func:`weld.mcp_helpers.weld_impact`. Missing-graph guard applies."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_impact")
    return _weld_impact(target, depth=depth, root=root)


def weld_enrich(
    *,
    node_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    force: bool = False,
    max_tokens: int | None = None,
    max_cost: float | None = None,
    root: Path | str = ".",
) -> dict:
    """LLM-assisted enrichment. Delegates to
    :func:`weld.mcp_helpers.weld_enrich`. Missing-graph guard applies."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_enrich")
    return _weld_enrich(
        node_id=node_id, provider=provider, model=model, force=force,
        max_tokens=max_tokens, max_cost=max_cost, root=root,
    )


# ---------------------------------------------------------------------------
# Registry + dispatch
# ---------------------------------------------------------------------------

def build_tools() -> list[Tool]:
    """Return the list of registered MCP tools.

    The order is stable to make test pinning easy.
    """
    from weld._mcp_tools import build_tools as _build_tools_impl

    return _build_tools_impl(
        weld_query=weld_query,
        weld_find=weld_find,
        weld_context=weld_context,
        weld_path=weld_path,
        weld_brief=weld_brief,
        weld_stale=weld_stale,
        weld_callers=weld_callers,
        weld_references=weld_references,
        weld_export=weld_export,
        weld_diff=weld_diff,
        weld_trace=weld_trace,
        weld_impact=weld_impact,
        weld_enrich=weld_enrich,
        weld_review=_weld_review_guarded,
        tool_cls=Tool,
    )

def _dispatch_inner(
    tool_name: str, arguments: dict | None, *, root: Path | str = ".",
) -> dict:
    """Select the tool by name and invoke it. Raises ``KeyError`` on miss.

    A corrupt/truncated ``graph.json`` (``json.JSONDecodeError``) or a graph
    written by a newer Weld (``SchemaVersionError``) raised from
    ``Graph.load`` inside a tool handler is converted to the shared
    structured-error payload (``error_code`` + ``hint`` via
    :mod:`weld._errors`) instead of escaping -- so the same code an
    unhandled ``JSONDecodeError`` used to produce a transport crash now
    returns a parseable error to the client. A node-not-found result
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
    for tool in build_tools():
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
) -> dict:
    """Dispatch a tool call by name (used by tests and ``run_stdio``).

    Wraps :func:`_dispatch_inner` with :class:`weld._telemetry.Recorder`
    so every MCP tool call appends one event (ADR 0035). The Recorder
    swallows its own writer errors -- telemetry failures never alter the
    dispatch result or replace the original exception. MCP has no exit
    code, so we set the schema sentinel ``exit_code = -1``. Raises
    ``KeyError`` when *tool_name* is not registered.
    """
    from weld._telemetry import Recorder

    # Recorder accepts root=None and falls back to Path.cwd() internally.
    try:
        rroot = root if isinstance(root, Path) else Path(root)
    except (TypeError, ValueError):
        rroot = None
    with Recorder(surface="mcp", command=tool_name, flags=[], root=rroot) as rec:
        rec.set_exit_code(-1)  # ADR 0035 MCP sentinel; no exit concept.
        return _dispatch_inner(tool_name, arguments, root=root)


def dispatch_to_text_payload(
    tool_name: str, arguments: dict | None, *, root: Path | str = ".",
) -> str:
    """Dispatch and return a JSON string the stdio layer wraps in TextContent.

    SDK-free seam used by the stdio ``_call_tool`` handler. Graph-load
    failures are already converted to a structured payload by
    :func:`_dispatch_inner`; the remaining transport guarantees (unknown
    tool -> payload, last-resort serialization) live in
    :func:`weld._mcp_guard.serialize_dispatch`.
    """
    return _serialize_dispatch(dispatch, tool_name, arguments, root)

# ---------------------------------------------------------------------------
# Stdio entry point (optional; requires the ``mcp`` SDK)
# ---------------------------------------------------------------------------
# The transport lives in :mod:`weld._mcp_stdio` to keep this module -- the
# SDK-free tool adapters and registry -- under the line-count cap. Re-exported
# here so ``weld.mcp_server.run_stdio`` / ``main`` and ``python -m
# weld.mcp_server`` keep working.
from weld._mcp_stdio import main, run_stdio  # noqa: E402,F401  (re-export)

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
