"""Edge surgery for the re-export retarget, and the undo that keeps it honest.

:mod:`weld._graph_closure_reexport` decides *where* a call reached through a
first-party facade should point; this module is what moves it there, and --
more importantly -- what moves it back.

Why the move has to be undoable
-------------------------------
Every other rule in ``close_graph`` re-derives its output from node props each
round and is self-correcting for free: delete a module and ``_link_imports``
re-resolves the importer's retained ``imports_from`` against the new index.
Retargeting mutates a retained *edge*, which nothing re-derives, so on the
incremental path an edge moved in an earlier round is simply inherited -- no
matter what has happened to the facade or the definition since.

Deleting the *definition* turns out to be the harmless half: the retained edge
dangles, ADR 0074's widen-and-retry re-runs the caller as an orphaned producer,
and the stub comes back on its own (measured, by disabling this undo and
watching that round still pass). Deleting the *facade* is the case that needs
this, and it needs it precisely because nothing looks broken: the edge still
points at a symbol that still exists, so nothing dangles, nothing re-runs, and
the caller is never re-walked. The facade's stub is then simply absent where a
full discover of the same tree mints it -- and since that stub is what the
module index binds the facade's name to once its file node is gone, the
caller's own ``depends_on`` resolves to an external package on one path and to
that stub on the other.

So a retargeted edge records the stub it replaced, in ``props.reexport_to`` or
``props.reexport_from`` -- two keys rather than one with a direction field,
because the side is the whole of what has to be remembered and an edge only
ever carries one resolved target. Every round restores those endpoints,
re-minting the stub node when the chain no longer resolves, before the walk
re-derives from scratch. Stripping its own prior output is the same discipline
``link_producers_consumers`` takes in the same post-processing run, and it buys
the same incremental == full contract.
"""

from __future__ import annotations

import json
from typing import Callable

from weld.strategies._python_origin import (
    is_stdlib_module,
    make_resolved_target_node,
    module_from_symbol_id,
)

#: Where each endpoint records the stub it replaced.
STUB_PROPS = (("from", "reexport_from"), ("to", "reexport_to"))
SYMBOL_PREFIX = "symbol:py:"


def restore_previous_rewrites(
    nodes: dict[str, dict], edges: list[dict], module_index: dict[str, str],
) -> None:
    """Put every previously retargeted endpoint back on the stub it replaced.

    The recorded id is always dropped, even when it is not one this pass could
    have written. These props are read back off ``.weld/graph.json``, which is a
    plain file on disk; an id that is not a well-formed ``symbol:py:`` one is
    either corruption or a bug, and restoring an endpoint onto it would mint a
    node with an arbitrary id. Dropping the bookkeeping and leaving the edge
    where it is degrades to "no undo available for this edge", which the next
    full discover repairs.
    """
    for edge in edges:
        props = edge.get("props")
        if not isinstance(props, dict):
            continue
        for side, key in STUB_PROPS:
            stub_id = props.get(key)
            if not isinstance(stub_id, str) or not stub_id:
                continue
            del props[key]
            if not _is_python_symbol_id(stub_id):
                continue
            edge[side] = stub_id
            _ensure_stub_node(nodes, stub_id, module_index)


def _is_python_symbol_id(node_id: str) -> bool:
    """True for a well-formed ``symbol:py:<module>:<qualname>`` id."""
    if not node_id.startswith(SYMBOL_PREFIX):
        return False
    return bool(module_from_symbol_id(node_id)) and bool(
        node_id[len(SYMBOL_PREFIX):].partition(":")[2]
    )


def _ensure_stub_node(
    nodes: dict[str, dict], stub_id: str, module_index: dict[str, str],
) -> None:
    """Re-mint *stub_id* if the graph no longer holds it.

    Reproduces what ``python_callgraph`` would emit for the same call site on a
    full discover, ``make_resolved_target_node`` and all, so a chain that has
    stopped resolving leaves the graph in the state a full run would produce
    rather than in a third state of its own. A node already present is left
    alone: it may since have been walked for real, and a definite symbol
    outranks anything this module would mint.
    """
    if stub_id in nodes:
        return
    origin = _origin_for(module_from_symbol_id(stub_id), module_index)
    nodes[stub_id] = make_resolved_target_node(stub_id, origin)


def _origin_for(module: str, module_index: dict[str, str]) -> str:
    """ADR 0042's resolved-target origin, judged from the closure's index.

    Mirrors ``origin_for_resolved``, whose project test is membership of the
    run's project module set; the closure's stand-in for that set is "the graph
    holds a Python ``file:`` node under this module name".
    ``reconcile_intra_repo_origins`` runs immediately after the closure and
    heals anything this reads differently.
    """
    if not module:
        return "external"
    if is_stdlib_module(module):
        return "stdlib"
    return "project" if module in module_index else "external"


def retarget(edges: list[dict], replacement: dict[str, str]) -> None:
    """Move every endpoint naming a retargetable stub onto its definition."""
    for edge in edges:
        for side, key in STUB_PROPS:
            old = edge.get(side)
            if not isinstance(old, str) or old not in replacement:
                continue
            edge[side] = replacement[old]
            props = edge.get("props")
            if not isinstance(props, dict):
                props = {}
                edge["props"] = props
            props[key] = old


def collapse_collisions(
    edges: list[dict],
    moved: Callable[[dict], bool] | None = None,
) -> None:
    """Resolve, on content, duplicates a retarget just created.

    A caller that reaches the same function both through the facade and
    directly ends up with two identical ``(from, to, type)`` triples carrying
    different provenance. The generic dedup downstream keeps whichever comes
    first in list order, and the full and incremental paths do not agree on
    that order, so the choice is made here instead -- on the edge's own
    content, which no ordering can move.

    A retargeted edge wins over one that was already there, so the bookkeeping
    that lets the next round undo the retarget is never the member that gets
    dropped. Groups the retarget did not touch are not even collected: this is a
    repair for a collision the calling pass created, not a second dedup pass,
    and on a graph of this repo's size collecting all of them costs more than
    everything else here put together.

    *moved* recognises an edge the calling pass can move, and defaults to this
    module's own re-export bookkeeping.
    :mod:`weld._graph_closure_import_attr` passes its own, because the hazard
    is the shape of the operation -- moving an endpoint onto a node other edges
    may already name -- not anything specific to facades, and a second
    hand-rolled copy of this tie-break would be a second chance to order it
    differently.
    """
    predicate = _records_a_stub if moved is None else moved
    touched = {_endpoints(e) for e in edges if predicate(e)}
    if not touched:
        return
    groups: dict[tuple[str, str, str], list[int]] = {}
    for index, edge in enumerate(edges):
        key = _endpoints(edge)
        if key in touched:
            groups.setdefault(key, []).append(index)
    dropped: set[int] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        keeper = min(group, key=lambda i: _collapse_key(edges[i], predicate))
        dropped.update(i for i in group if i != keeper)
    if dropped:
        edges[:] = [e for i, e in enumerate(edges) if i not in dropped]


def _endpoints(edge: dict) -> tuple[str, str, str]:
    """The ``(from, to, type)`` triple the downstream dedup keys on."""
    return (str(edge.get("from")), str(edge.get("to")), str(edge.get("type")))


def _records_a_stub(edge: dict) -> bool:
    props = edge.get("props")
    if not isinstance(props, dict):
        return False
    return any(props.get(key) for _side, key in STUB_PROPS)


def _collapse_key(
    edge: dict, moved: Callable[[dict], bool],
) -> tuple[int, str]:
    """Order a collision group: retargeted first, then by canonical content."""
    return (0 if moved(edge) else 1, _canonical_props(edge))


def _canonical_props(edge: dict) -> str:
    """A stable string form of an edge's props, for an order-free comparison."""
    props = edge.get("props")
    if not isinstance(props, dict):
        return ""
    return json.dumps(props, sort_keys=True, default=str)


__all__ = [
    "STUB_PROPS",
    "SYMBOL_PREFIX",
    "collapse_collisions",
    "restore_previous_rewrites",
    "retarget",
]
