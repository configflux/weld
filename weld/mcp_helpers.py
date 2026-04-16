"""Helper adapters for the weld MCP server.

Factored out of ``weld.mcp_server`` to stay within the 400-line default
cap. Each function here is a thin adapter that
loads a fresh ``Graph`` and delegates to the underlying helper -- same
pattern as the adapters in ``mcp_server.py``.

"""

from __future__ import annotations

from pathlib import Path

from weld.enrich import enrich as _enrich
from weld.graph import Graph as _Graph
from weld.impact import impact as _impact
from weld.trace import trace as _trace

def _load_graph(root: Path) -> _Graph:
    g = _Graph(root)
    g.load()
    return g

def weld_trace(
    *,
    term: str | None = None,
    node_id: str | None = None,
    depth: int = 2,
    seed_limit: int = 5,
    root: Path | str = ".",
) -> dict:
    """Protocol-aware cross-boundary slice. Delegates to ``weld.trace.trace``.

    Exactly one of *term* or *node_id* must be supplied. Returns the
    stable trace envelope (``TRACE_VERSION``).
    """
    g = _load_graph(Path(root))
    return _trace(
        g, term=term, node_id=node_id, depth=depth, seed_limit=seed_limit,
    )


def weld_impact(
    target: str,
    depth: int = 3,
    *,
    root: Path | str = ".",
) -> dict:
    """Reverse-dependency blast radius for a node id or file path."""
    g = _load_graph(Path(root))
    try:
        return _impact(g, target=target, depth=depth)
    except ValueError as exc:
        return {"error": str(exc)}


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
    """LLM-assisted semantic enrichment for one node or the whole graph."""
    g = _load_graph(Path(root))
    try:
        return _enrich(
            g,
            provider_name=provider,
            model=model,
            node_id=node_id,
            force=force,
            max_tokens=max_tokens,
            max_cost=max_cost,
            persist=True,
        )
    except (RuntimeError, ValueError) as exc:
        return {"error": str(exc)}

def build_trace_tool() -> dict:
    """Return ``(name, description, input_schema, handler)`` for weld_trace.

    Returning a plain dict avoids a circular import with the ``Tool``
    dataclass defined in ``mcp_server``.
    """
    return {
        "name": "weld_trace",
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
                        "Search term (tokenized like weld_query). "
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
        "handler": weld_trace,
    }


def build_impact_tool() -> dict:
    """Return the MCP tool descriptor for ``weld_impact``."""
    return {
        "name": "weld_impact",
        "description": (
            "Reverse-dependency blast radius for a node id or file path. "
            "Returns direct/transitive dependents, affected surfaces, and risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Node id or repo-relative file path to analyze.",
                },
                "depth": {
                    "type": "integer",
                    "description": "Maximum reverse traversal depth (default 3).",
                    "default": 3,
                    "minimum": 0,
                },
            },
            "required": ["target"],
            "additionalProperties": False,
        },
        "handler": weld_impact,
    }


def build_enrich_tool() -> dict:
    """Return the MCP tool descriptor for ``weld_enrich``."""
    return {
        "name": "weld_enrich",
        "description": (
            "LLM-assisted semantic enrichment for a node or the full graph. "
            "Returns enriched, skipped, and error lists in a stable envelope."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "Limit enrichment to one node id.",
                },
                "provider": {
                    "type": "string",
                    "description": "Provider name or env-configured default.",
                },
                "model": {
                    "type": "string",
                    "description": "Override the provider's default model.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Rewrite existing matching enrichment.",
                    "default": False,
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Stop after this many tracked tokens.",
                    "minimum": 0,
                },
                "max_cost": {
                    "type": "number",
                    "description": "Stop after this much tracked cost.",
                    "minimum": 0,
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        "handler": weld_enrich,
    }
