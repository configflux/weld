"""Shared MCP tool-schema properties: ``root``, ``full_size``, ``ROOTLESS_TOOLS``.

Split out of :mod:`weld._mcp_tools`, which sits at the 400-line cap
(AGENTS.md / CLAUDE.md line-count policy) -- same reason and same pattern as
the existing :mod:`weld._cli_render_seeds` split: these are the schema
fragments every graph-backed read tool reuses verbatim, so they need exactly
one definition each rather than a copy per tool description.

:data:`ROOTLESS_TOOLS` keeps its original dotted path at
``weld._mcp_tools.ROOTLESS_TOOLS`` (re-exported there) because
:mod:`weld._mcp_guard` and several tests already reference it by that name;
moving its *definition* here does not move where callers import it from.
"""

from __future__ import annotations

#: Shared ``full_size`` schema property for the bounded read tools (ADR 0082).
#: Skips the byte budget; the diet/edge-de-dangle still apply. Kept identical
#: across every bounded read -- weld_query / weld_context / weld_brief and the
#: traversal reads weld_impact / weld_callers / weld_references / weld_trace --
#: so the surface stays symmetric.
_FULL_SIZE_PROPERTY: dict = {
    "type": "boolean",
    "description": (
        "Skip the read byte budget (ADR 0082). Default false: the shaped "
        "envelope is pruned to fit the agent tool cap and reports what it "
        "dropped -- omitted_neighbors.size_capped (query/context), a "
        "size_capped object (impact/callers/references), or a warnings entry "
        "(brief/trace). Set true to keep every dieted/de-dangled item."
    ),
    "default": False,
}

#: Shared ``root`` schema property for the graph-backed **read** tools. It
#: re-exposes what ``wd --root`` already gives an operator, which is the only
#: thing MCP is allowed to do (ADR 0083), and it is deliberately absent from
#: ``weld_enrich`` / ``weld_review``: those write, and ``additionalProperties:
#: False`` is what turns that omission into a refusal rather than a
#: convention. The wording stays free of internal references because this
#: string is shipped to clients as the tool's own documentation.
#:
#: The trailing sentence closes bd thau (weld dogfood gap): the served root
#: may not be the caller's own checkout, and the only signal that says so
#: previously lived solely in response *data* (``freshness.branch``, ADR 0096
#: §3) with nothing in the *schema* pointing a client at it. A new envelope
#: field was considered and rejected -- ``freshness`` is deliberately "three
#: scalars, still no paths or SHAs" (weld/_mcp_read.py), and a root path would
#: reopen that boundary. Naming the existing field here is the same fix at
#: zero cost to that contract.
_ROOT_PROPERTY: dict = {
    "type": "string",
    "description": (
        "Answer from this checkout instead of the one the server was "
        "launched in -- the same capability as the wd --root flag. Must be "
        "an existing directory in the same repository as the server's root "
        "(a linked worktree, the main checkout, or a subdirectory of "
        "either); anything else is refused with error_code "
        "root_out_of_bounds. Omit to use the server's own root, which may "
        "not be the checkout you are editing. When a response carries a "
        "freshness object, freshness.branch names the branch actually "
        "served -- compare it to your own when in doubt (weld_stale reports "
        "the same signal directly as branch)."
    ),
}


#: Tools that do **not** accept a request-supplied ``root``: the two that
#: write. Schemas are validated by the client, not by this server, so the
#: omission below is advisory on its own -- dispatch reads this set to refuse
#: a redirected write outright (see
#: :func:`weld._mcp_guard.resolve_dispatch_root`). Kept beside the schemas it
#: describes, and pinned against them by ``weld_mcp_request_root_test`` so the
#: two cannot drift apart.
ROOTLESS_TOOLS: frozenset[str] = frozenset({"weld_enrich", "weld_review"})


def with_shared(schema: dict, **props: dict) -> dict:
    """Return *schema* with shared properties (``root`` / ``full_size``) added.

    Used for the descriptors that :mod:`weld.mcp_helpers` builds
    (``weld_trace`` / ``weld_impact``). Their handlers already accept both
    arguments, so only the advertised schema is missing them -- and adding them
    here rather than there keeps one definition of each property.
    """
    return {**schema, "properties": {**schema["properties"], **props}}
