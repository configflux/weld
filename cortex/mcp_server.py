"""Stdio MCP server exposing cortex query helpers as structured tools.

Thin adapter over :mod:`cortex.graph`, :mod:`cortex.brief`, and
:mod:`cortex.file_index` (ADR 0015). Each tool handler loads a fresh
:class:`cortex.graph.Graph` and delegates to the same helper the CLI uses.
The ``mcp`` SDK is optional -- only :func:`run_stdio` requires it.

Tools: cortex_query, cortex_find, cortex_context, cortex_path, cortex_brief,
cortex_stale, cortex_callers, cortex_references, cortex_trace, cortex_export.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cortex.brief import brief as _brief
from cortex.diff import load_and_diff as _load_and_diff
from cortex.file_index import find_files as _find_files
from cortex.file_index import load_file_index as _load_file_index
from cortex.graph import Graph as _Graph
from cortex.mcp_helpers import build_trace_tool as _build_trace_tool

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

def _load_graph(root: Path) -> _Graph:
    g = _Graph(root)
    g.load()
    return g

# ---------------------------------------------------------------------------
# Tool implementations (pure adapters)
# ---------------------------------------------------------------------------

def cortex_query(term: str, limit: int = 20, *, root: Path | str = ".") -> dict:
    """Tokenized ranked graph search. Delegates to ``Graph.query``."""
    g = _load_graph(Path(root))
    return g.query(term, limit=limit)

def cortex_find(term: str, limit: int | None = None, *, root: Path | str = ".") -> dict:
    """File-index substring search. Delegates to ``cortex.file_index.find_files``."""
    index = _load_file_index(Path(root))
    result = _find_files(index, term)
    if limit is not None and limit >= 0:
        result = {
            "query": result.get("query", term),
            "files": result.get("files", [])[:limit],
        }
    return result

def cortex_context(node_id: str, *, root: Path | str = ".") -> dict:
    """Node plus 1-hop neighborhood. Delegates to ``Graph.context``."""
    g = _load_graph(Path(root))
    return g.context(node_id)

def cortex_path(from_id: str, to_id: str, *, root: Path | str = ".") -> dict:
    """Shortest path between two nodes. Delegates to ``Graph.path``."""
    g = _load_graph(Path(root))
    return g.path(from_id, to_id)

def cortex_brief(area: str, limit: int = 20, *, root: Path | str = ".") -> dict:
    """Stable brief JSON for *area*. Delegates to ``cortex.brief.brief``."""
    g = _load_graph(Path(root))
    return _brief(g, area, limit=limit)

def cortex_stale(*, root: Path | str = ".") -> dict:
    """Graph freshness vs git HEAD. Delegates to ``Graph.stale``."""
    g = _load_graph(Path(root))
    return g.stale()

def cortex_callers(
    symbol_id: str, depth: int = 1, *, root: Path | str = ".",
) -> dict:
    """Return direct (and optionally transitive) callers of *symbol_id*."""
    g = _load_graph(Path(root))
    return g.callers(symbol_id, depth=depth)

def cortex_export(
    format: str, node_id: str | None = None, depth: int = 1,
    *, root: Path | str = ".",
) -> dict:
    """Export graph to a visualization format. Delegates to ``cortex.export``."""
    from cortex.export import export
    try:
        output = export(format, node_id=node_id, depth=depth, root=root)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"format": format, "output": output}


def cortex_references(symbol_name: str, *, root: Path | str = ".") -> dict:
    """Return callers + file-index references for a bare symbol *name*."""
    g = _load_graph(Path(root))
    refs = g.references(symbol_name)
    index = _load_file_index(Path(root))
    refs["files"] = _find_files(index, symbol_name).get("files", [])
    return refs

def cortex_diff(*, root: Path | str = ".") -> dict:
    """Return the graph diff between previous and current discovery run."""
    return _load_and_diff(Path(root))

# ---------------------------------------------------------------------------
# Registry + dispatch
# ---------------------------------------------------------------------------

def build_tools() -> list[Tool]:
    """Return the list of registered MCP tools.

    The order is stable to make test pinning easy.
    """
    tools = [
        Tool(
            name="cortex_query",
            description=(
                "Tokenized ranked search over the knowledge graph. Returns "
                "matches, neighbors, and edges for the given term."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "term": {
                        "type": "string",
                        "description": "Search term (multi-word is tokenized).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matches to return.",
                        "default": 20,
                        "minimum": 1,
                    },
                },
                "required": ["term"],
                "additionalProperties": False,
            },
            handler=cortex_query,
        ),
        Tool(
            name="cortex_find",
            description=(
                "Substring search over the .cortex/file-index.json file keyword "
                "index. Returns ranked file hits with matching tokens."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                    },
                },
                "required": ["term"],
                "additionalProperties": False,
            },
            handler=cortex_find,
        ),
        Tool(
            name="cortex_context",
            description=(
                "Return a node plus its immediate (1-hop) neighborhood from "
                "the knowledge graph."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "Full node id, e.g. 'entity:Store'.",
                    },
                },
                "required": ["node_id"],
                "additionalProperties": False,
            },
            handler=cortex_context,
        ),
        Tool(
            name="cortex_path",
            description=(
                "Return the shortest path between two nodes in the knowledge "
                "graph, including the visited nodes and connecting edges."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "from_id": {"type": "string"},
                    "to_id": {"type": "string"},
                },
                "required": ["from_id", "to_id"],
                "additionalProperties": False,
            },
            handler=cortex_path,
        ),
        Tool(
            name="cortex_brief",
            description=(
                "Stable agent-facing brief (BRIEF_VERSION=2) for a task area. "
                "Returns a versioned envelope with primary matches, interfaces, "
                "authoritative docs, build surfaces, and boundaries."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "description": "Task or area keyword (same tokenization as cortex_query).",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 20,
                    },
                },
                "required": ["area"],
                "additionalProperties": False,
            },
            handler=cortex_brief,
        ),
        Tool(
            name="cortex_stale",
            description=(
                "Report whether the on-disk knowledge graph is stale relative "
                "to the current git HEAD. Advisory; does not mutate the graph."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=cortex_stale,
        ),
        Tool(
            name="cortex_callers",
            description=(
                "Return direct (and optionally transitive) callers of a "
                "symbol. Walks `calls` edges in reverse from `symbol_id` up "
                "to `depth` levels (default 1)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_id": {
                        "type": "string",
                        "description": "Full symbol id, e.g. symbol:py:cortex.discover:_load_strategy",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Caller traversal depth (default 1).",
                        "default": 1,
                        "minimum": 1,
                    },
                },
                "required": ["symbol_id"],
                "additionalProperties": False,
            },
            handler=cortex_callers,
        ),
        Tool(
            name="cortex_references",
            description=(
                "Return callers and file-index references for a bare symbol "
                "name. Combines call-graph callers (across resolved + "
                "unresolved targets) with cortex_find textual hits."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_name": {
                        "type": "string",
                        "description": "Bare symbol name, e.g. _load_strategy",
                    },
                },
                "required": ["symbol_name"],
                "additionalProperties": False,
            },
            handler=cortex_references,
        ),
        Tool(name="cortex_export",
             description="Export graph (or subgraph) to Mermaid, DOT, or D2.",
             input_schema={"type": "object", "properties": {
                 "format": {"type": "string", "enum": ["mermaid", "dot", "d2"],
                            "description": "Output format."},
                 "node_id": {"type": "string",
                             "description": "Center node for subgraph (optional)."},
                 "depth": {"type": "integer", "default": 1, "minimum": 0,
                           "description": "BFS depth for subgraph (default 1)."},
             }, "required": ["format"], "additionalProperties": False},
             handler=cortex_export),
    ]
    _td = _build_trace_tool()
    tools.append(Tool(name=_td["name"], description=_td["description"],
                       input_schema=_td["input_schema"], handler=_td["handler"]))
    tools.append(
        Tool(
            name="cortex_diff",
            description=(
                "Return the graph diff between the previous and current "
                "discovery run. Shows added, removed, and modified nodes "
                "and edges. No parameters required."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=cortex_diff,
        ),
    )
    return tools

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
    raise KeyError(f"unknown cortex MCP tool: {tool_name}")

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
            "cortex.mcp_server: the 'mcp' Python SDK is not installed. "
            "Install the optional extra (e.g. 'pip install mcp') to run the "
            f"stdio server. Original error: {exc}\n"
        )
        return 2

    import asyncio

    server: Server = Server("cortex")
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
    """Module entry point: ``python -m cortex.mcp_server``."""
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else Path(".")
    return run_stdio(root)

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
