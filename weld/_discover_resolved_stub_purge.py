"""Purge a resolved cross-glob call-target stub once every edge that
referenced it is gone (bd n4nvt; bd 5038-q4t3d corrected "inbound edge" to
"edge in either direction" -- see below).

:func:`weld.strategies._python_origin.make_resolved_target_node` mints a
speculative stub for a call/inherits/references/scope-call/decorates target
that resolves -- via import-table syntax, not by finding an already-walked
definition -- to a real ``symbol:py:<module>:<qual>`` id the current batch did
not itself walk: a cross-glob target not yet visited, or a module outside
every configured glob. Its own docstring says so: "props.file is
intentionally absent: a cross-glob target's defining file is unknown at
single-glob mint time." That is the identical no-``props.file`` shape that
made ``symbol:unresolved:*`` sentinels (bd oao53) and ``graph_closure``'s
external package placeholders (bd pkz2s) invisible to
``purge_stale_nodes``'s ordinary ``props.file`` match -- on a fourth,
disjoint placeholder shape.

Empirically confirmed (not just read) via a real incremental-vs-full
``discover()`` repro: a single-glob fixture where a project file's import
resolves to a module entirely outside every configured glob mints the stub
on a full discover; deleting that sole referencing file leaves the stub
behind in the incremental graph with zero inbound edges, which a fresh full
discover of the same post-delete tree never mints (nothing calls it, nothing
walks the target module either).

Unlike oao53's sentinel, whose ``symbol:unresolved:<name>`` id is disjoint
from every real id by construction, THIS id shape
(``symbol:py:<module>:<qual>``) is the exact shape a genuinely-walked, real,
``definite`` symbol node uses -- oao53's own id-prefix guard is deliberately
scoped to never reach it (see that module's docstring). A rule keyed on id
shape alone would purge real symbols with zero inbound ``calls`` edges,
which is extremely common and entirely legitimate (library exports,
constructors, dead code, anything nobody happens to call yet). So this rule
keys on the node's own props instead -- the exact fingerprint
:func:`~weld.strategies._python_origin.make_resolved_target_node` stamps,
regardless of which of its five callers (``python_callgraph``,
``_python_inherits``, ``_python_references``, ``_python_scope_calls``,
``_python_decorates``) minted it:

* ``type == "symbol"``
* ``props.file`` absent or falsy
* ``props.confidence == "speculative"``
* ``props.authority == "derived"``
* ``props.source_strategy == "python_callgraph"`` (hardcoded in
  ``make_resolved_target_node`` regardless of the calling module)
* the id does NOT start with ``symbol:unresolved:`` (keeps this rule
  disjoint from oao53's -- ``make_sentinel_node`` stamps the identical props
  shape under that prefix, and oao53 already owns it)

A real, definite ``python_callgraph``-sourced symbol node always sets
``props.file`` (unconditional direct assignment at mint time -- never a
``setdefault``), so "no ``props.file`` plus ``speculative`` confidence" can
never describe an actually-walked symbol; "no ``props.file``" alone is
already a safe, exhaustive proxy for "this run never walked it."

Counts edges of EVERY type, the same widened scope oao53's rule needs and
for the identical reason: the stub id is shared across whichever of the five
python_* modules references the same target symbol (a call, an inherits
base, a bare-name reference, a module-scope call, a decorator target can all
point at the same id), so a node with a surviving edge of any kind is
exactly what a fresh full discover over the same partially-emptied tree
would still emit.

And counts them in EITHER direction (bd 5038-q4t3d). The list above already
named the decorator case, but the rule was written as "zero INBOUND edges",
and ADR 0122 emits ``decorates`` decorator -> decorated -- so the one entry
in that list whose semantics run the other way was the one the accumulator
could not see. Both this rule and oao53's now test membership in
:func:`weld._discover_placeholder_anchor.edge_anchored_node_ids`; see that
module for why a direction-agnostic anchor is the derived answer here rather
than an allowlist of reversed edge types.

Safe by construction for the same reason pkz2s's and oao53's rules are:
``python_callgraph`` (and its four sibling modules) mint this node lazily
via ``nodes.setdefault`` inside a fresh, per-``extract()``-call local
``nodes`` dict, but any surviving or newly-dirty file that still references
the same target re-parses through the normal dirty-source loop in
:mod:`weld._discover_incremental_merge` and re-mints the identical id before
this purge's result is ever read downstream -- so a purge here can only ever
be "too early", never wrong. In particular this never fights ADR 0103's
stub -> real upgrade path
(:func:`weld._discover_node_merge.incremental_claim_wins`): this rule only
runs inside :func:`weld.discovery_state.purge_stale_nodes`, which itself
no-ops whenever nothing is stale, so a pure file-addition round (the shape
that drives an upgrade) never reaches this rule at all; and in a mixed round
where some unrelated file is also stale, the rule can only remove a stub
that has *already* lost every edge in the very same pass's provenance purge
-- if the file that still references the target survives clean, its edge
(and so the stub's anchor) survives with it.

Folded into
:func:`weld._discover_external_package_purge.emptied_placeholder_node_ids`
as a fourth, disjoint rule -- disjoint because this one keys on the node's
own props (``authority``/``confidence``/``source_strategy``, guarded by the
id NOT matching oao53's prefix) rather than on ``props.roles`` (g7rs),
``props.type == "package"`` (pkz2s/ukt95), the ``symbol:unresolved:`` id
prefix (oao53), or ``props.source_strategy == "tree_sitter"`` (bd 5ouuf's
later fifth rule), so a given node can only ever match one of the rules
that entry point unions.
"""

from __future__ import annotations

from weld._discover_placeholder_anchor import edge_anchored_node_ids
from weld.strategies._python_origin import is_resolved_target_stub

# The fingerprint itself lives beside the function that stamps it
# (``weld.strategies._python_origin.is_resolved_target_stub``), because a
# second reader now needs it: ``weld._graph_closure_reexport`` retargets the
# edges pointing at one of these stubs when the module it names merely
# re-exports the symbol. Keyed on props, never on id shape -- see that
# function for why, and this module's own docstring for what the rule below
# does with the answer.


def emptied_resolved_stub_node_ids(
    nodes: dict[str, dict], edges: list[dict],
) -> set[str]:
    """Return ids of resolved cross-glob call-target stubs no surviving edge names.

    Call after the ordinary ``props.file`` purge and its edge purge, over
    their *result*: a node found here already lost every edge that referenced
    it (each dropped by the endpoint-membership floor when the referencing
    file's own symbol node was purged, or by the provenance rule when the
    referencing edge carried one), so nothing here re-derives *why*, it only
    names the node whose sole reason to exist just went with it. A full
    discover never mints such a node unless something currently references
    it, so purging it here keeps incremental discovery's output equal to a
    fresh full run's.

    Counts edges of EVERY type (``calls``, ``inherits``, ``references``,
    ``decorates``, or a module/class-scope call) -- see the module docstring
    for why a single edge type would under-purge here, the same way it would
    for oao53's sentinel.

    Counts them in EITHER direction, via
    :func:`weld._discover_placeholder_anchor.edge_anchored_node_ids` (bd
    5038-q4t3d). ``decorates`` was already in the list above when this rule
    was written, but as an INBOUND type -- and ADR 0122 emits it decorator ->
    decorated, so for a stub referenced only as a decorator the sentinel is
    the edge's ``from`` endpoint and the inbound accumulator never saw it.
    On this repo's own graph that was ``symbol:py:{abc:abstractmethod,
    dataclasses:dataclass, typing:runtime_checkable, unittest:skipIf}``,
    122 live ``decorates`` edges between them, every one read as dead --
    ``dataclasses:dataclass`` alone at ``in=0, out=118``. See that module for
    why the correction is derived from the stub's no-``props.file`` shape
    rather than from a list of edge types.
    """
    anchored = edge_anchored_node_ids(edges)

    return {
        nid
        for nid, node in nodes.items()
        if is_resolved_target_stub(nid, node) and nid not in anchored
    }


__all__ = ["emptied_resolved_stub_node_ids"]
