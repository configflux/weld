"""What still anchors a never-walked placeholder node (bd 5038-q4t3d).

The two zero-inbound purge rules over ``symbol`` placeholders --
:mod:`weld._discover_unresolved_symbol_purge` (bd oao53) and
:mod:`weld._discover_resolved_stub_purge` (bd n4nvt) -- each asked "does any
surviving edge still reference this node?" and each answered it by
accumulating ``edge["to"]``. That answer is only correct for an edge type
whose semantics run *referencer -> referenced*, which is what ``calls``,
``inherits``, ``implements`` and ``references`` all do.

``decorates`` does not. ADR 0122 emits it decorator -> decorated
(``weld.strategies._python_decorates.emit_decorates_edges``), because that is
the honest direction: a decorator is applied to the symbol it decorates, it
does not call it. So a symbol referenced ONLY as a decorator is the edge's
``from`` endpoint, never its ``to``, and both rules read a fully-anchored node
as dead. Measured on this repo's own full-discover graph (10714 nodes / 48937
edges): of the 962 nodes matching those two predicates, seven are anchored
only this way and were returned as emptied --
``symbol:unresolved:{classmethod,property,staticmethod}`` and
``symbol:py:{abc:abstractmethod, dataclasses:dataclass,
typing:runtime_checkable, unittest:skipIf}`` -- between them holding 203 live
``decorates`` edges. ``symbol:py:dataclasses:dataclass`` alone: ``in=0``,
``out=118``, every one of them ``decorates``.

The fix is this module rather than an edge-type allowlist in each rule,
because the correct signal is derived from the node shape both rules already
require, not from an enumeration anyone has to maintain:

    A placeholder matched by either rule carries no ``props.file`` -- ADR
    0042's speculative-node shape, meaning no source walk ever produced it as
    a definition. It therefore never authored an edge on its own behalf.
    Every edge naming it was minted by the strategy walking the *other*
    endpoint's file, so every edge naming it is that file's live reference,
    whichever endpoint the edge type's semantics put the placeholder on.

Direction is thus an artifact of what an edge type *means*, exactly as edge
*type* was: both of those rules already count inbound edges of EVERY type,
for the same reason spelled out in their own docstrings (the id is a shared
namespace, so scoping to one type would drop a node another strategy still
references). This module finishes that thought on the other axis, and a
future edge kind emitted in the reversed direction is anchored without
touching either rule.

The one shape that argument does not cover is an edge between TWO
placeholders, which would anchor each other forever with no live file behind
either. It does not occur: no strategy mints such an edge today, and
``decorates`` -- the only reversed-direction type there is -- always names an
already-walked symbol of the file being parsed as its ``to`` endpoint
(``_python_decorates.emit_decorates_edges`` builds it from the qualname
``_record_symbol`` just registered). Should some future producer emit one,
this is the assumption to revisit, not the rules above.

Deliberately NOT used by the three ``package``-node rules
(:mod:`weld._discover_external_package_purge`'s own rule,
:mod:`weld._discover_membership_purge`, and
:mod:`weld._discover_tree_sitter_package_purge`). Each of those keys on one
edge type in one direction on purpose -- g7rs's producer-side rule purges on
zero OUTGOING ``contains`` and would be inverted by a direction-agnostic
anchor, and the two consumer-side package rules are kept disjoint from it by
exactly that asymmetry. Nothing measured here applies to them: the survey
above found ``decorates`` to be the only outbound edge type any placeholder
matched by the two symbol rules carries, and a ``package`` node is not one of
them.

Whose edge counts (bd 5038-rwi34)
---------------------------------
The argument above turns on *who authored the edge*, and it holds for every
strategy-authored one: a strategy reads a file and writes down what that file
references, so the edge is the other endpoint's live reference. It does not
hold for an edge :mod:`weld.graph_closure` authored. The closure reads no
source. ``_link_imports`` re-derives a ``depends_on`` per round by looking an
importer's ``imports_from`` name up in ``_module_index`` -- an index the
placeholder is itself a member of, because ``make_resolved_target_node``
stamps ``props.module`` and the index keys every node's module name. So when
no ``file:`` node claims that module, the closure hands a *clean* consumer an
edge onto the placeholder, and that edge says nothing about the consumer's
source: it is an echo of the placeholder, not evidence for it.

That is a cycle, and it is how bd rwi34 escaped. A never-walked stub minted
for one file's ``from alpha import fn_alpha`` survived the deletion of that
sole importer, anchored by the closure ``depends_on`` of an unrelated clean
file -- which ``close_graph`` then re-derived off the same index entry the
stub was still in. A full discover of the post-delete tree mints neither and
resolves that import externally. Nothing dangled, so ADR 0074's widen-and-retry
never fired; the stub simply stayed, one inbound edge deep, past every
zero-anchor rule.

So an edge whose ``props.source_strategy`` is the closure's own does not
anchor. Keyed on authorship rather than on edge type, direction, or
``props.resolution``, for the same reason the direction correction above was
derived rather than enumerated: authorship is exactly the property that makes
an edge evidence. Every placeholder in a graph was put there by a strategy
walking a reference site, and that same walk emits the edge naming it -- so a
full discover holds a placeholder only where a strategy-authored edge names
it, and one left with closure edges alone is one a full discover would not
have.

The closure does mint a placeholder in one place, and it is not a counterexample:
:func:`weld._graph_closure_reexport_edges._ensure_stub_node` re-mints the stub a
previous round's re-export retarget replaced. It does so while moving a
*retained* edge's endpoint back onto it, and that edge is the ``calls`` edge
``python_callgraph`` authored at the call site -- so the restored stub is anchored
by a strategy edge, exactly as the argument requires. The ordering makes it moot
either way: ``purge_stale_nodes`` runs inside the incremental merge and
``close_graph`` in the post-process after it, so nothing the closure mints is
ever judged by this predicate in the round that minted it.

Measured on this repository's own full-discover graph (10815 nodes / 49428
edges): 983 nodes match the two rules -- 571 sentinels, 412 resolved stubs --
and every one of them is edge-anchored. Of the 2113 closure edges that name a
placeholder at all, **all 2113 name a resolved stub and none names a
sentinel** (a sentinel's ``props.module`` is ``""``, which ``_module_index``
skips, so the closure has no way to target one), and **not one placeholder
loses its anchor**: 914 are anchored by ``python_callgraph`` edges alone and
69 by a mix, none by closure edges alone. The narrowing takes nothing live on
a real tree; what it takes is the self-perpetuating incremental echo.

This is the one direction q4t3d's correction did not have to defend. That
change only ever *widened* the anchor set, so it could retain a node and never
purge a new one; this one narrows it, and a wrongly-purged node has no repair
at :func:`weld.strategies.cpp_resolver.resolve_includes_pass`, which deletes
on the sentinel rule mid-pass during a **full** discover where ADR 0074's
widen-and-retry (:mod:`weld._discover_orphan_edges`) never runs. Two
independent reasons that call site is untouched: it runs inside the C++
strategy's own post-pass, before ``close_graph`` has authored any edge at all,
and it scopes its candidates to sentinels -- the shape the measurement above
found no closure edge can name. On the incremental path the surviving argument
is each rule's own: ``purge_stale_nodes`` re-mints anything a live file still
references, so a purge there can be too early, never wrong.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Edge authorship that does not anchor a never-walked placeholder.
#:
#: One member: :mod:`weld.graph_closure` is the only producer in the tree that
#: derives an edge's endpoint from the node set it was handed rather than from
#: a file it read (``_STRATEGY`` there is the constant it stamps, on both edge
#: kinds it authors -- ``_link_source_backed_nodes``' ``contains`` and
#: ``_link_imports``' ``depends_on``). Spelled here as a literal rather than
#: imported from
#: ``graph_closure``, matching
#: :data:`weld._discover_external_package_purge._EDGE_ANCHORED_STRATEGIES`,
#: which names the same producer the same way: the purge modules read a
#: persisted graph's props and must not depend on the closure package to say
#: what a string in that file means.
_DERIVED_EDGE_STRATEGIES = frozenset({"graph_closure"})


def _is_derived_edge(edge: dict) -> bool:
    """True for an edge the graph closure authored from the node set.

    Defensive about ``props`` the same way every purge module here is: a
    missing or non-dict ``props`` reads as "not a derived edge", so an edge of
    unknown authorship keeps anchoring. That is the safe verdict -- it retains
    a node rather than purging one -- and it is what a project-local strategy
    under ``.weld/strategies/`` gets, since it cannot author the closure's own
    ``source_strategy``.

    The ``isinstance`` on the *value* is load-bearing, not ceremony: these
    props are read back off ``.weld/graph.json``, which ADR 0115 treats as
    unvetted repo text, and an unhashable value there (``"source_strategy":
    []``) would make the membership test raise rather than answer. Non-string
    reads as unknown authorship, which lands on the same safe side.
    """
    props = edge.get("props")
    if not isinstance(props, dict):
        return False
    strategy = props.get("source_strategy")
    return isinstance(strategy, str) and strategy in _DERIVED_EDGE_STRATEGIES


def edge_anchored_node_ids(edges: Iterable[dict]) -> set[str]:
    """Return every node id a strategy-authored edge in *edges* names.

    The anchor set both symbol placeholder rules test membership in. Reads
    both endpoints of every such edge regardless of ``edge["type"]``: see the
    module docstring for why direction is no more meaningful here than type
    already was, and for why an edge the closure derived from the node set is
    not evidence of anything (bd 5038-rwi34).

    Non-string endpoints are skipped rather than raising, matching the
    defensive posture the purge modules take toward strategy-authored shape
    (a project-local strategy under ``.weld/strategies/`` can hand back
    anything). A malformed endpoint anchors nothing, which leaves the node it
    failed to name purgeable -- the same verdict the pre-existing inbound-only
    accumulator gave it.
    """
    anchored: set[str] = set()
    for edge in edges:
        if _is_derived_edge(edge):
            continue
        for endpoint in (edge.get("from"), edge.get("to")):
            if isinstance(endpoint, str):
                anchored.add(endpoint)
    return anchored


__all__ = ["edge_anchored_node_ids"]
