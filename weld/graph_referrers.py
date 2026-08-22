"""Reverse-edge traversal for :class:`weld.graph.Graph` -- callers, references.

Carved out of :mod:`weld.graph` for the reason :mod:`weld._graph_match` was
(bd jkir): fixing the ``references`` node-id defect below pushed that module
past the 400-line cap, and the cheapest local fix -- deleting the reasoning
that explains the defect -- is worse than the right one. These two functions
are the pair that answers "what points at this node", they were adjacent
already, and the dependency runs one way: :class:`Graph` imports this module
and nothing here imports :class:`Graph`.

``Graph.callers`` / ``Graph.references`` stay as thin delegates because the
CLI, the MCP surface, the federation layer and the test suite all address
them through the class, and turning a line-count carve into a rename of a
pinned surface is how a refactor acquires a blast radius it did not need.
"""

from __future__ import annotations

from weld._alias_index import resolve_id


def _canonical_id(graph, node_id: str) -> str | None:
    """Return the canonical node id for *node_id*, or ``None`` if unknown.

    The ADR 0041 alias rewrite, so all four node-id read paths resolve a legacy
    spelling identically. They did not: a transcript pasting a pre-rename id
    kept working through ``wd context`` and ``wd path`` and reported "node not
    found" through ``wd callers`` and ``wd references`` (bd m4er). Alias
    resolution exists precisely so pasted historical ids keep resolving, so
    honouring it on half the paths that take a node id is the same class of
    defect as bd nywd -- two read paths disagreeing about one node.

    Delegates to :func:`weld._alias_index.resolve_id` rather than repeating the
    inline ``nodes -> _alias_index`` lookup ``context`` and ``path`` spell out,
    because that function already owns this contract *and* its lookup-side
    security guard: an alias may never shadow a canonical id, and a stale index
    whose target was removed resolves to ``None`` rather than a dangling
    pointer. Re-deriving the two-line form here would have silently opted out of
    both.

    ``None`` (not the input) on a miss, so callers can fall through to bare-name
    resolution -- the branch ``context`` does not have.
    """
    return resolve_id(node_id, graph._data["nodes"], getattr(graph, "_alias_index", {}))


def callers(graph, symbol_id: str, depth: int = 1) -> dict:
    """Return the symbols that call *symbol_id*, up to *depth*.

    Walks ``calls`` edges in reverse. ``symbol_id`` accepts a fully-qualified
    node id (``symbol:py:weld.discover:_load_strategy``), a legacy ADR 0041
    alias for one (resolved via :func:`_canonical_id`), or a bare name
    (``_load_strategy``); bare names use the same resolution rule as
    :func:`references`, with callers aggregated and deduplicated across
    matches. An unknown name surfaces an ``error`` so a caller can tell "no
    match" from "no callers".

    A bare name can resolve to *several* seeds (two same-named symbols, or a
    resolved symbol plus its unresolved sentinel) -- the same ambiguity bd
    nyoks fixed for :func:`references`'s ``matches``. ``callers()`` has no
    ``matches`` field to attribute a caller to, so the fix here is additive
    instead: every response carries a top-level ``seeds`` list naming every
    resolved id, regardless of depth -- the honesty signal that resolution
    was plural even where nothing else in the envelope says so. At
    ``depth=1`` every edge in the BFS terminates at one unambiguous seed, so
    each caller entry also carries a ``targets`` list -- the seed id(s) it
    was found calling directly, same field name and shape :func:`references`
    uses for the identical reason (a caller of two seeds gets one row with
    two target ids, not two rows). Beyond depth 1 a caller may be reachable
    through more than one seed's chain and the BFS does not track which, so
    attributing it would be a fabricated claim rather than a recorded fact:
    ``targets`` is simply never stamped when ``depth != 1`` (bd jz65r; split
    out of, and scoped the same way as, bd nyoks's own non-goals list).
    """
    if depth < 1:
        depth = 1
    canonical = _canonical_id(graph, symbol_id)
    if canonical is not None:
        seeds = [canonical]
    else:
        matches = graph._resolve_symbol_name(symbol_id)
        if not matches:
            return {
                "symbol": symbol_id, "depth": depth,
                "callers": [], "edges": [], "seeds": [],
                "error": f"node not found: {symbol_id}",
            }
        seeds = [m["id"] for m in matches]
    # Reverse adjacency for calls edges only.
    rev: dict[str, list[dict]] = {}
    for edge in graph._data["edges"]:
        if edge.get("type") == "calls":
            rev.setdefault(edge["to"], []).append(edge)
    seen: set[str] = set(seeds)
    frontier: list[str] = list(seeds)
    out_callers: list[dict] = []
    out_edges: list[dict] = []
    # Only depth=1 gets attribution (see docstring); tracked unconditionally
    # of the ``seen`` dedup below -- like references()'s caller_targets, a
    # caller of two seeds must not lose the second seed to "already emitted".
    attribute_targets = depth == 1
    caller_targets: dict[str, list[str]] = {}
    for _ in range(depth):
        next_frontier: list[str] = []
        for node_id in frontier:
            for edge in rev.get(node_id, []):
                src = edge["from"]
                out_edges.append(edge)
                if attribute_targets:
                    targets = caller_targets.setdefault(src, [])
                    if node_id not in targets:
                        targets.append(node_id)
                if src in seen:
                    continue
                seen.add(src)
                found = graph.get_node(src)
                if found is not None:
                    out_callers.append(found)
                next_frontier.append(src)
        frontier = next_frontier
        if not frontier:
            break
    if attribute_targets:
        out_callers = [
            {**c, "targets": caller_targets.get(c["id"], [])}
            for c in out_callers
        ]
    return {
        "symbol": symbol_id,
        "depth": depth,
        "seeds": seeds,
        "callers": out_callers,
        "edges": out_edges,
    }


def references(graph, symbol_name: str) -> dict:
    """Return what points at *symbol_name* (``files`` is attached by callers).

    Accepts a bare identifier (``_load_strategy``), a full node id
    (``build-target://weld:runtime``) **or** a legacy ADR 0041 alias for one
    -- the same rule :func:`callers` has documented all along, and the absence
    of which was a correctness bug rather than a missing convenience (bd nywd,
    and bd m4er for the alias half; see :func:`_canonical_id`).
    ``weld._graph_match.resolve_symbol_name`` skips every node whose type is
    not ``symbol``, so a node id for any other type resolved to nothing and
    this returned an empty, *error-free* envelope: ``wd references
    build-target://weld:runtime`` printed "no references" for a node
    ``wd context`` reported 47 neighbours for, 36 of them inbound. Two read
    paths disagreeing about one node is bad; the wrong half being spelled
    identically to "this node genuinely has none" is what made it a defect
    rather than a gap.

    What counts as pointing at it depends on the node, and the split is the
    honest one rather than a special case. For a ``symbol`` it is ``calls``
    edges: that is what referring to a function means, and it is what
    :func:`callers` has always returned, so symbol behaviour is unchanged
    here. For every other type it is **all** inbound edges, because nothing
    *calls* a build target, a tool or a doc -- filtering those to ``calls``
    is precisely how the answer came back empty for a node with 335 inbound
    ``depends_on``.

    An unresolvable name now carries an ``error``, matching :func:`callers`,
    so "weld does not know this" and "weld knows this and nothing points at
    it" stop sharing a spelling.

    A bare name can resolve to *several* matches (two same-named symbols in
    different modules, or a resolved symbol plus its unresolved sentinel),
    and each match is walked separately below. Merging those results into
    one flat ``callers`` list used to drop which match a given caller was
    found under -- so two unrelated ``Tool`` classes each called by a
    different caller came back as one undifferentiated list, unreadable
    without cross-referencing ``edges`` by hand (bd nyoks). Every caller
    entry therefore carries a ``targets`` list: the id(s) of the match(es)
    it was found calling/pointing at. Deduplication stays keyed on the
    caller's own id -- a caller of two matches gets one row with two target
    ids, not two rows -- so cardinality and order are unchanged from before
    this field existed.
    """
    canonical = _canonical_id(graph, symbol_name)
    if canonical is not None:
        matches = [{"id": canonical, **graph._data["nodes"][canonical]}]
    else:
        matches = graph._resolve_symbol_name(symbol_name)
    if not matches:
        return {
            "symbol": symbol_name, "matches": [],
            "callers": [], "edges": [],
            "error": f"node not found: {symbol_name}",
        }
    all_callers: dict[str, dict] = {}
    caller_targets: dict[str, list[str]] = {}
    all_edges: list[dict] = []
    for match in matches:
        found = (
            callers(graph, match["id"], depth=1)
            if match.get("type") == "symbol"
            else inbound_referrers(graph, match["id"])
        )
        for referrer in found["callers"]:
            rid = referrer["id"]
            all_callers.setdefault(rid, referrer)
            targets = caller_targets.setdefault(rid, [])
            if match["id"] not in targets:
                targets.append(match["id"])
        all_edges.extend(found["edges"])
    callers_out = [
        {**referrer, "targets": caller_targets[rid]}
        for rid, referrer in all_callers.items()
    ]
    return {
        "symbol": symbol_name,
        "matches": matches,
        "callers": callers_out,
        "edges": all_edges,
    }


def inbound_referrers(graph, node_id: str) -> dict:
    """Return every node with an edge *into* ``node_id``, callers-shaped.

    The non-symbol half of :func:`references`. Deliberately edge-type
    agnostic: the vocabulary that points at a build target (``depends_on``,
    ``tests``), a tool (``invokes``) or a doc (``documents``, ``validates``)
    is open and grows with the strategies, so enumerating it here would
    reintroduce the closed list that made this answer "none" for a node with
    335 referrers. Self-edges are skipped -- a node is not its own referrer.
    """
    referrers: dict[str, dict] = {}
    edges: list[dict] = []
    for edge in graph._data["edges"]:
        if edge.get("to") != node_id or edge.get("from") == node_id:
            continue
        edges.append(edge)
        src = edge["from"]
        if src in referrers:
            continue
        found = graph.get_node(src)
        if found is not None:
            referrers[src] = found
    return {"callers": list(referrers.values()), "edges": edges}


__all__ = ["callers", "inbound_referrers", "references"]
