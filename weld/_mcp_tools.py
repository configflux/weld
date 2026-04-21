"""MCP tool descriptors for the weld stdio server.

Factored out of ``weld.mcp_server`` so the server module stays within
the 400-line default cap.  Each entry in :func:`build_tools` is a
lightweight :class:`weld.mcp_server.Tool` that pairs a JSON Schema with
the matching adapter function.
"""

from __future__ import annotations

from weld.mcp_helpers import (
    build_enrich_tool as _build_enrich_tool,
    build_impact_tool as _build_impact_tool,
    build_trace_tool as _build_trace_tool,
)


def build_tools(
    *,
    weld_query,
    weld_find,
    weld_context,
    weld_path,
    weld_brief,
    weld_stale,
    weld_callers,
    weld_references,
    weld_export,
    weld_diff,
    tool_cls,
) -> list:
    """Return the ordered list of MCP tool descriptors.

    The caller passes the adapter functions and the ``Tool`` class so this
    module has no circular import on ``weld.mcp_server``.
    """
    tools = [
        tool_cls(
            name="weld_query",
            description=(
                "Tokenized ranked search over the connected structure. Returns "
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
            handler=weld_query,
        ),
        tool_cls(
            name="weld_find",
            description=(
                "Substring search over the .weld/file-index.json file keyword "
                "index. Returns ranked file hits with matching tokens and an "
                "integer score (= number of matching tokens, same signal used "
                "for ordering)."
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
            handler=weld_find,
        ),
        tool_cls(
            name="weld_context",
            description=(
                "Return a node plus its immediate (1-hop) neighborhood from "
                "the connected structure."
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
            handler=weld_context,
        ),
        tool_cls(
            name="weld_path",
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
            handler=weld_path,
        ),
        tool_cls(
            name="weld_brief",
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
                        "description": "Task or area keyword (same tokenization as weld_query).",
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
            handler=weld_brief,
        ),
        tool_cls(
            name="weld_stale",
            description=(
                "Report whether the on-disk connected structure is stale relative "
                "to the current git HEAD. Advisory; does not mutate the graph."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=weld_stale,
        ),
        tool_cls(
            name="weld_callers",
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
                        "description": "Full symbol id, e.g. symbol:py:weld.discover:_load_strategy",
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
            handler=weld_callers,
        ),
        tool_cls(
            name="weld_references",
            description=(
                "Return callers and file-index references for a bare symbol "
                "name. Combines call-graph callers (across resolved + "
                "unresolved targets) with weld_find textual hits."
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
            handler=weld_references,
        ),
        tool_cls(
            name="weld_export",
            description="Export graph (or subgraph) to Mermaid, DOT, or D2.",
            input_schema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["mermaid", "dot", "d2"],
                        "description": "Output format.",
                    },
                    "node_id": {
                        "type": "string",
                        "description": "Center node for subgraph (optional).",
                    },
                    "depth": {
                        "type": "integer",
                        "default": 1,
                        "minimum": 0,
                        "description": "BFS depth for subgraph (default 1).",
                    },
                },
                "required": ["format"],
                "additionalProperties": False,
            },
            handler=weld_export,
        ),
    ]

    _td = _build_trace_tool()
    tools.append(tool_cls(
        name=_td["name"], description=_td["description"],
        input_schema=_td["input_schema"], handler=_td["handler"],
    ))
    _id = _build_impact_tool()
    tools.append(tool_cls(
        name=_id["name"], description=_id["description"],
        input_schema=_id["input_schema"], handler=_id["handler"],
    ))
    _ed = _build_enrich_tool()
    tools.append(tool_cls(
        name=_ed["name"], description=_ed["description"],
        input_schema=_ed["input_schema"], handler=_ed["handler"],
    ))
    tools.append(
        tool_cls(
            name="weld_diff",
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
            handler=weld_diff,
        ),
    )
    return tools
