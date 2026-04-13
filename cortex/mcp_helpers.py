"""Helper adapters for the cortex MCP server.

Factored out of ``cortex.mcp_server`` to stay within the 430-line cap on
that grandfathered module. Each function here is a thin adapter that
loads a fresh ``Graph`` and delegates to the underlying helper -- same
pattern as the adapters in ``mcp_server.py``.

"""

from __future__ import annotations

from pathlib import Path

from cortex.graph import Graph as _Graph
from cortex.trace import trace as _trace

def _load_graph(root: Path) -> _Graph:
    g = _Graph(root)
    g.load()
    return g

def cortex_trace(
    *,
    term: str | None = None,
    node_id: str | None = None,
    depth: int = 2,
    seed_limit: int = 5,
    root: Path | str = ".",
) -> dict:
    """Protocol-aware cross-boundary slice. Delegates to ``cortex.trace.trace``.

    Exactly one of *term* or *node_id* must be supplied. Returns the
    stable trace envelope (``TRACE_VERSION``).
    """
    g = _load_graph(Path(root))
    return _trace(
        g, term=term, node_id=node_id, depth=depth, seed_limit=seed_limit,
    )

def build_trace_tool() -> dict:
    """Return ``(name, description, input_schema, handler)`` for cortex_trace.

    Returning a plain dict avoids a circular import with the ``Tool``
    dataclass defined in ``mcp_server``.
    """
    return {
        "name": "cortex_trace",
        "description": (
            "Protocol-aware cross-boundary slice for a task area or "
            "known node. Returns a service / interface / contract / "
            "boundary / verification slice as a stable JSON envelope."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": (
                        "Search term (tokenized like cortex_query). "
                        "Supply either term or node_id, not both."
                    ),
                },
                "node_id": {
                    "type": "string",
                    "description": "Anchor by node id instead of a term.",
                },
                "depth": {
                    "type": "integer",
                    "description": "BFS depth from anchor seeds (default 2).",
                    "default": 2,
                    "minimum": 1,
                },
                "seed_limit": {
                    "type": "integer",
                    "description": "Max anchor seeds for term queries (default 5).",
                    "default": 5,
                    "minimum": 1,
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        "handler": cortex_trace,
    }
