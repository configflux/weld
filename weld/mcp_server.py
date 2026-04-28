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

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from weld._mcp_guard import (
    graph_present as _graph_present,
    missing_graph_payload as _missing_graph_payload,
)
from weld.brief import brief as _brief
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

def _load_graph(root: Path) -> _Graph | _FederatedGraph:
    """Return a ``FederatedGraph`` when ``workspaces.yaml`` is present at
    *root*, else a single-repo ``Graph``. Lets MCP tools that use
    ``query``/``context``/``path`` transparently span child repos."""
    if _find_workspaces_yaml(root) is not None:
        return _FederatedGraph(root)
    return _load_single_repo_graph(root)


def _load_single_repo_graph(root: Path) -> _Graph:
    """Load a plain ``Graph`` at *root*. Used as fallback by
    ``_load_graph`` when no ``workspaces.yaml`` is present."""
    g = _Graph(root)
    g.load()
    return g


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

def weld_query(term: str, limit: int = 20, *, root: Path | str = ".") -> dict:
    """Tokenized ranked search. Delegates to ``Graph.query``; see
    :func:`_attach_children_status` for the federated-only extra field.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_query")
    g = _load_graph(Path(root))
    return _attach_children_status(g, g.query(term, limit=limit))

def weld_find(term: str, limit: int | None = None, *, root: Path | str = ".") -> dict:
    """File-index substring search. Delegates to ``weld.file_index.find_files``.

    ``limit`` is forwarded to ``find_files``, which slices the ranked result
    and emits a ``score`` field on every file entry (the number of matching
    tokens, identical to the signal used for ordering). Negative ``limit``
    values are ignored to preserve the pre-change tolerance at the MCP
    boundary.
    """
    index = _load_file_index(Path(root))
    effective_limit = limit if limit is None or limit >= 0 else None
    return _find_files(index, term, limit=effective_limit)

def weld_context(node_id: str, *, root: Path | str = ".") -> dict:
    """Node + 1-hop neighborhood. Delegates to ``Graph.context``; see
    :func:`_attach_children_status` for the federated-only extra field.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_context")
    g = _load_graph(Path(root))
    return _attach_children_status(g, g.context(node_id))

def weld_path(from_id: str, to_id: str, *, root: Path | str = ".") -> dict:
    """Shortest path between two nodes. Delegates to ``Graph.path``; see
    :func:`_attach_children_status` for the federated-only extra field.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_path")
    g = _load_graph(Path(root))
    return _attach_children_status(g, g.path(from_id, to_id))

def weld_brief(area: str, limit: int = 20, *, root: Path | str = ".") -> dict:
    """Stable brief JSON for *area*. Delegates to ``weld.brief.brief``.

    In a federated workspace the underlying graph is a
    :class:`~weld.federation.FederatedGraph` whose ``query`` and ``dump``
    methods span child repos, so the brief transparently includes child
    matches. Missing-graph guard applies (single-repo root only).
    """
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_brief")
    g = _load_graph(Path(root))
    return _brief(g, area, limit=limit)

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
        tool_cls=Tool,
    )

def _dispatch_inner(
    tool_name: str, arguments: dict | None, *, root: Path | str = ".",
) -> dict:
    """Select the tool by name and invoke it. Raises ``KeyError`` on miss."""
    args = dict(arguments or {})
    for tool in build_tools():
        if tool.name == tool_name:
            return tool.handler(**args, root=root)
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

# ---------------------------------------------------------------------------
# Stdio entry point (optional; requires the ``mcp`` SDK)
# ---------------------------------------------------------------------------

_HELP = """Usage: python -m weld.mcp_server [ROOT]

Run the Weld MCP stdio server for ROOT, or the current directory when ROOT
is omitted. The stdio server requires the optional MCP SDK:

  pip install 'configflux-weld[mcp]'

The rest of the weld package, including `wd mcp config`, works without that
extra.
"""

def run_stdio(root: Path | str = ".") -> int:
    """Run the stdio MCP server loop.

    Imports the ``mcp`` SDK lazily so the rest of this module stays usable
    without it.
    """
    try:
        from mcp.server import Server  # type: ignore
        from mcp.server.stdio import stdio_server  # type: ignore
        from mcp.types import TextContent, Tool as McpTool  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only with extras
        sys.stderr.write(
            "weld.mcp_server: the 'mcp' Python SDK is not installed. "
            "Install the optional extra with "
            "'pip install \"configflux-weld[mcp]\"' to run the "
            f"stdio server. Original error: {exc}\n"
        )
        return 2

    import asyncio

    server: Server = Server("weld")
    tools = build_tools()

    @server.list_tools()  # type: ignore[misc]
    async def _list_tools() -> list[McpTool]:  # pragma: no cover - requires sdk
        return [
            McpTool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in tools
        ]

    @server.call_tool()  # type: ignore[misc]
    async def _call_tool(
        name: str, arguments: dict | None
    ) -> list[TextContent]:  # pragma: no cover - requires sdk
        try:
            result = dispatch(name, arguments, root=root)
        except KeyError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]
        return [
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False),
            )
        ]

    async def _main() -> None:  # pragma: no cover - requires sdk
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_main())
    return 0

def main(argv: list[str] | None = None) -> int:
    """Module entry point: ``python -m weld.mcp_server``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        sys.stdout.write(_HELP)
        return 0
    root = Path(args[0]) if args else Path(".")
    return run_stdio(root)

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
