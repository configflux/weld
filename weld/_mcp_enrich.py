"""The ``weld_enrich`` MCP tool: its handler, its graph load, and its schema.

Split out of :mod:`weld.mcp_helpers` for the same reason the dispatch loop
lives in :mod:`weld._mcp_dispatch` and the transport in
:mod:`weld._mcp_stdio` -- the 400-line cap, which ``mcp_helpers`` reached.
What lands here is one tool's whole surface, so the handler, the argument
contract it enforces, and the schema that advertises it sit together and are
read as one thing.

Nothing in ``mcp_helpers`` imports this module back, so a convenience
re-export there would be a cycle. The two callers --
:mod:`weld.mcp_server` (handler) and :mod:`weld._mcp_tools` (descriptor)
-- import from here directly.

``weld_enrich`` is the mutating tool, but it has a read-only half: with
``agent_direct`` it emits the work plan an agent follows to write enrichment
itself (ADR 0098) and touches nothing. ADR 0083 is why that half is here at
all -- MCP re-exposes CLI capability, and a caller with no provider
configured is more likely to be on this surface than on the command line.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from weld.contract import VALID_NODE_TYPES
from weld.enrich import enrich as _enrich
from weld.graph import Graph as _Graph


def _load_graph(root: Path) -> _Graph:
    """Uncached graph load for ``weld_enrich``.

    The provider-backed path mutates the graph and persists it; it must own a
    fresh in-memory object rather than the process-wide read cache (a persist
    must never write back from a shared instance). The agent-direct path
    writes nothing, but loads the same way so that the plan it emits is the
    plan ``wd enrich --agent-direct`` would emit from the same bytes -- the
    CLI loads a bare ``Graph`` too. The read helpers use
    :func:`weld._mcp_read.load_graph_for_read` instead, so they inherit the
    auto-refresh + sha-keyed cache (bd 85tb.3).
    """
    g = _Graph(root)
    g.load()
    return g


def weld_enrich(
    *,
    node_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    force: bool = False,
    max_tokens: int | None = None,
    max_cost: float | None = None,
    agent_direct: bool = False,
    node_type: str | None = None,
    limit: int | None = None,
    root: Path | str = ".",
) -> dict:
    """Semantic enrichment: the provider-backed loop, or the agent-direct plan.

    ``agent_direct=True`` returns the work plan -- the nodes still pending,
    the record contract a write must satisfy, and the ``wd add-node`` command
    that lands one -- instead of calling a provider (ADR 0098). The mode is
    read-only by construction: no provider is resolved, no socket is opened,
    and the graph is not written, which is why it takes no write lock. Taking
    one would queue a read behind every writer *and* misdescribe what the
    call does.

    ``node_type`` / ``limit`` shape that plan (``wd enrich --type`` /
    ``--limit``) and mean nothing without the mode; ``provider`` / ``model``
    / ``max_tokens`` / ``max_cost`` drive the loop and mean nothing with it.
    Either mistake is refused by
    :func:`weld._enrich_agent_direct.mode_flag_error` -- the oracle the CLI
    consults, so the two surfaces refuse the same combinations -- rather than
    ignored. A silently dropped ``provider`` is the one that matters: the
    caller would read the returned plan as evidence that an unattended
    provider run had already happened.

    A legacy *node_id* resolves per ADR 0041 on both paths, because
    :mod:`weld._enrich_selection` resolves it -- the oracle the CLI reaches
    through too. This handler used to rewrite the id itself, which made the
    behaviour look like an MCP property when it is a product one, and left
    ``wd enrich --node <legacy-id>`` reporting "node not found".
    """
    from weld._enrich_agent_direct import agent_direct_payload, mode_flag_error
    from weld._graph_write_lock import graph_write_lock

    conflict = mode_flag_error(SimpleNamespace(
        agent_direct=agent_direct, provider=provider, model=model,
        max_tokens=max_tokens, max_cost=max_cost, node_type=node_type,
        limit=limit,
    ))
    if conflict is not None:
        return {"error": conflict}
    try:
        if agent_direct:
            return agent_direct_payload(
                _load_graph(Path(root)),
                node_id=node_id, node_type=node_type, limit=limit, force=force,
            )
        # ADR 0094: same lock span as the wd enrich CLI (load -> mutate ->
        # save) so MCP and CLI writers serialize with each other.
        with graph_write_lock(Path(root)):
            g = _load_graph(Path(root))
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


#: ``agent_direct`` and the two arguments that shape its plan. Wording stays
#: free of internal references: these strings are shipped to clients as the
#: tool's own documentation. The ``node_type`` enum is *derived* from the
#: graph contract rather than retyped, so it cannot advertise a type the
#: server would reject (or omit one it accepts).
_AGENT_DIRECT_PROPERTIES: dict = {
    "agent_direct": {
        "type": "boolean",
        "description": (
            "Return the self-serve enrichment work plan -- the nodes still "
            "pending, the record contract a write must satisfy, and the "
            "wd add-node command that lands one -- instead of calling a "
            "provider. Needs no API key, no optional extra, and no network "
            "access: the calling agent is the provider. Writes nothing. "
            "Cannot be combined with provider, model, max_tokens, or "
            "max_cost."
        ),
        "default": False,
    },
    "node_type": {
        "type": "string",
        "enum": sorted(VALID_NODE_TYPES),
        "description": (
            "List only nodes of this type in the plan. Requires agent_direct."
        ),
    },
    "limit": {
        "type": "integer",
        "description": (
            "Cap how many pending nodes the plan lists. The counts still "
            "report the full pending total and the remainder, so a batched "
            "caller can tell progress from completion. Requires agent_direct."
        ),
        "minimum": 0,
    },
}


def build_enrich_tool() -> dict:
    """Return the MCP tool descriptor for ``weld_enrich``.

    A plain dict rather than a ``Tool``: the dataclass lives in
    ``mcp_server``, which imports this module.
    """
    return {
        "name": "weld_enrich",
        "description": (
            "LLM-assisted semantic enrichment for a node or the full graph. "
            "Returns enriched, skipped, and error lists in a stable envelope. "
            "Set agent_direct for the no-credentials work plan instead: the "
            "pending nodes and the record contract for writing enrichment "
            "yourself, with no provider call."
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
                    "description": (
                        "Rewrite existing matching enrichment (with "
                        "agent_direct: list already-enriched nodes too)."
                    ),
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
                **_AGENT_DIRECT_PROPERTIES,
            },
            "required": [],
            "additionalProperties": False,
        },
        "handler": weld_enrich,
    }


__all__ = ["build_enrich_tool", "weld_enrich"]
