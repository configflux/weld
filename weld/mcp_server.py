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
    :func:`_attach_children_status` for the federated-only extra field."""
    g = _load_graph(Path(root))
    return _attach_children_status(g, g.query(term, limit=limit))

def weld_find(term: str, limit: int | None = None, *, root: Path | str = ".") -> dict:
    """File-index substring search. Delegates to ``weld.file_index.find_files``."""
    index = _load_file_index(Path(root))
    result = _find_files(index, term)
    if limit is not None and limit >= 0:
        result = {
            "query": result.get("query", term),
            "files": result.get("files", [])[:limit],
        }
    return result

def weld_context(node_id: str, *, root: Path | str = ".") -> dict:
    """Node + 1-hop neighborhood. Delegates to ``Graph.context``; see
    :func:`_attach_children_status` for the federated-only extra field."""
    g = _load_graph(Path(root))
    return _attach_children_status(g, g.context(node_id))

def weld_path(from_id: str, to_id: str, *, root: Path | str = ".") -> dict:
    """Shortest path between two nodes. Delegates to ``Graph.path``; see
    :func:`_attach_children_status` for the federated-only extra field."""
    g = _load_graph(Path(root))
    return _attach_children_status(g, g.path(from_id, to_id))

def weld_brief(area: str, limit: int = 20, *, root: Path | str = ".") -> dict:
    """Stable brief JSON for *area*. Delegates to ``weld.brief.brief``.

    In a federated workspace the underlying graph is a
    :class:`~weld.federation.FederatedGraph` whose ``query`` and ``dump``
    methods span child repos, so the brief transparently includes child
    matches.
    """
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
    are resolved within the named child graph.
    """
    g = _load_graph(Path(root))
    if isinstance(g, _FederatedGraph):
        return _federated_callers(g, symbol_id, depth=depth)
    return g.callers(symbol_id, depth=depth)

def weld_export(
    format: str, node_id: str | None = None, depth: int = 1,
    *, root: Path | str = ".",
) -> dict:
    """Export graph to a visualization format. Delegates to ``weld.export``."""
    from weld.export import export
    try:
        output = export(format, node_id=node_id, depth=depth, root=root)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"format": format, "output": output}


def weld_references(symbol_name: str, *, root: Path | str = ".") -> dict:
    """Return callers + file-index references for a bare symbol *name*.

    In a federated workspace, references fan out across all present
    children with prefixed IDs.
    """
    g = _load_graph(Path(root))
    if isinstance(g, _FederatedGraph):
        refs = _federated_references(g, symbol_name)
    else:
        refs = g.references(symbol_name)
    index = _load_file_index(Path(root))
    refs["files"] = _find_files(index, symbol_name).get("files", [])
    return refs

def weld_diff(*, root: Path | str = ".") -> dict:
    """Return the graph diff between previous and current discovery run."""
    return _load_and_diff(Path(root))


weld_impact = _weld_impact
weld_enrich = _weld_enrich

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
        tool_cls=Tool,
    )

def dispatch(
    tool_name: str,
    arguments: dict | None,
    *,
    root: Path | str = ".",
) -> dict:
    """Dispatch a tool call by name. Used by both tests and ``run_stdio``.

    Raises ``KeyError`` if *tool_name* is not registered.
    """
    args = dict(arguments or {})
    for tool in build_tools():
        if tool.name == tool_name:
            return tool.handler(**args, root=root)
    raise KeyError(f"unknown weld MCP tool: {tool_name}")

# ---------------------------------------------------------------------------
# Stdio entry point (optional; requires the ``mcp`` SDK)
# ---------------------------------------------------------------------------

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
            "Install the optional extra (e.g. 'pip install mcp') to run the "
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
    root = Path(args[0]) if args else Path(".")
    return run_stdio(root)

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
