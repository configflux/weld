"""Stdio MCP server exposing cortex query helpers as structured tools.

(``docs/adrs/0015-kg-mcp-server-exposure.md``).

Design notes
------------

The server is a **thin adapter** over the existing query helpers in
:mod:`cortex.graph`, :mod:`cortex.brief`, and :mod:`cortex.file_index`. It does NOT
duplicate any query logic: every tool handler here loads a fresh
:class:`cortex.graph.Graph` and calls the same helper the CLI already calls.
This keeps the CLI and the MCP surface behaviourally locked together --
if the CLI output changes, the MCP output changes in lockstep.

The ``mcp`` Python SDK is an **optional** dependency. Importing this module
must succeed without the SDK installed so the CLI keeps working in
environments that do not ship ``mcp``. Only :func:`run_stdio` (and the
``python -m cortex.mcp_server`` entry point) require the SDK, and they import
it lazily with a helpful error message.

Tool surface (initial)
----------------------

- ``cortex_query(term, limit?)`` -- ranked tokenized graph search
- ``cortex_find(term, limit?)`` -- file-index substring search
- ``cortex_context(node_id)`` -- node plus 1-hop neighborhood
- ``cortex_path(from_id, to_id)`` -- shortest path between nodes
- ``cortex_brief(area)`` -- stable brief JSON (``BRIEF_VERSION == 2``, includes interfaces)
- ``cortex_stale()`` -- graph freshness check vs git HEAD
- ``cortex_callers(symbol_id, depth?)`` -- direct/transitive callers of a symbol
- ``cortex_references(symbol_name)`` -- callers + file-index references for a name
- ``cortex_trace(term?, node_id?)`` -- cross-boundary protocol-aware slice

``cortex_callers`` and ``cortex_references`` were added by the call-graph
follow-up (project-dkw, ADR ``cortex/docs/adr/0004-call-graph-schema-extension.md``).
``cortex_trace`` was added for interaction-aware retrieval (project-xoq.2.3).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cortex.brief import brief as _brief
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
    """File-index substring search. Delegates to ``cortex.file_index.find_files``.

    ``limit`` is accepted for symmetry with ``cortex_query`` but applies after
    the underlying helper returns so the shared result shape is preserved.
    """
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
    symbol_id: str,
    depth: int = 1,
    *,
    root: Path | str = ".",
) -> dict:
    """Return direct (and optionally transitive) callers of *symbol_id*.

    Delegates to ``Graph.callers``. ``depth`` defaults to 1.
    """
    g = _load_graph(Path(root))
    return g.callers(symbol_id, depth=depth)

def cortex_references(symbol_name: str, *, root: Path | str = ".") -> dict:
    """Return callers + file-index references for a bare symbol *name*.

    Combines ``Graph.references`` with ``cortex.file_index.find_files`` so the
    return shape matches the ``cortex references`` CLI command.
    """
    g = _load_graph(Path(root))
    refs = g.references(symbol_name)
    index = _load_file_index(Path(root))
    refs["files"] = _find_files(index, symbol_name).get("files", [])
    return refs

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
    ]
    _td = _build_trace_tool()
    tools.append(Tool(name=_td["name"], description=_td["description"],
                       input_schema=_td["input_schema"], handler=_td["handler"]))
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
