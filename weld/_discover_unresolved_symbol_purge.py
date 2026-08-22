"""Purge a call/inherits/implements unresolved-symbol sentinel once every
inbound edge that referenced it is gone (bd oao53).

``python_callgraph``, ``_go_inherits``, ``_rust_inherits``,
``_typescript_inherits``, ``_java_inherits``, ``_cpp_inherits``,
``cpp_resolver``'s layer-1 predecessors, and ``_ts_call_graph`` each mint a
``symbol:unresolved:<name>`` node lazily via ``nodes.setdefault`` -- mirroring
``graph_closure._ensure_package_node``'s own setdefault-gated minting shape
-- as the edge target for a call, inherits, or implements reference whose
name does not resolve to any known symbol. The node carries no
``props.file`` (ADR 0042's speculative-node shape: it is not "owned" by the
one file that happened to reference it first), so
:func:`weld.discovery_state.purge_stale_nodes`'s ordinary ``props.file``
match never purges it directly -- exactly the blind spot bd pkz2s found for
``graph_closure``'s external package placeholders
(:mod:`weld._discover_external_package_purge`), on a third, disjoint
placeholder shape.

Empirically confirmed (not just read) via a real incremental-vs-full
``discover()`` repro for python (``calls``), go (``inherits``, struct
embedding), rust (``implements``, trait impl), typescript (``inherits``,
``extends``), and java (``inherits``, ``extends``): deleting the sole file
that references an unresolvable name leaves the sentinel behind in the
incremental graph with zero inbound edges, which a fresh full discover of
the same post-delete tree never mints.

Unlike pkz2s's node -- referenced by exactly one edge type (``depends_on``)
from one relationship family, so its purge signal can safely count only
``depends_on`` in-edges -- this sentinel id is a SHARED namespace keyed on
the bare unresolved name alone: a Python call to ``Base()``, a Go struct
embedding ``Base``, and a Java class ``extends Base`` all collide on the
identical id ``symbol:unresolved:Base`` if a repo happens to use that name
in more than one language or edge kind. So the "is this node still
referenced" signal here cannot be scoped to one edge type or one producing
strategy the way pkz2s's ``depends_on``-only check is: it must count EVERY
inbound edge, of any type, from any strategy, that still targets the id. A
node with at least one surviving inbound edge -- regardless of which
language or edge kind produced it -- is exactly what a fresh full discover
over the same tree would still mint (the surviving referencer re-mints the
identical id via the same ``nodes.setdefault`` gate), so zero inbound edges
of ANY kind is the correct -- and only correct -- purge signal here.

Safe by construction for the same reason pkz2s's rule is: ``graph_closure``
does not re-mint this node (only the strategy that walks the referencing
file does), but any surviving or newly-dirty file that still
calls/inherits/implements the unresolved name re-parses through the normal
dirty-source loop in :mod:`weld._discover_incremental_merge` and re-mints
the identical id via the same ``nodes.setdefault`` gate before this purge's
result is ever read downstream -- so a purge here can only ever be "too
early", never wrong.

Folded into
:func:`weld._discover_external_package_purge.emptied_placeholder_node_ids`
as a third, disjoint rule -- disjoint because this one keys on the node
id's ``symbol:unresolved:`` prefix (the one signal every minting strategy
stamps identically) rather than on ``props.roles`` or
``props.source_strategy``/``authority``, so a given node can only ever
match one of the three rules that entry point unions.

Also called directly from
:func:`weld.strategies.cpp_resolver.resolve_includes_pass` (bd
5038-t6mzx), a second, complementary call site rather than a
duplicate: that one fires mid-pass, immediately after cpp_resolver
rewrites its own unresolved edges, over a single discover() call's
local nodes/edges -- before anything is persisted, and before this
module's other caller (:func:`weld.discovery_state.purge_stale_nodes`)
ever runs, since that one is reachable only from the incremental-merge
path. A full discover never calls ``purge_stale_nodes`` at all, so
cpp_resolver's call is the only cleanup a same-pass fully-resolved
sentinel ever gets there; removing it would leave stray sentinel nodes
in full-discover output. Both callers share this module's predicate
precisely so a future change to what counts as "still referenced"
(e.g. widening the inbound-edge check) is made once, not twice.

Deliberately NOT extended to :func:`weld.strategies._python_origin.make_resolved_target_node`'s
output (a resolved cross-glob call target, e.g. ``symbol:py:some.mod:func``,
which also carries no ``props.file``): that is a structurally different id
shape (no ``symbol:unresolved:`` prefix) and a different minting condition
(a real, resolvable symbol merely not walked by this batch's glob), so this
rule's id-prefix guard cannot reach it, deliberately -- see bd oao53's
tracked non-goal.
"""

from __future__ import annotations

_UNRESOLVED_SYMBOL_TYPE = "symbol"
_UNRESOLVED_SYMBOL_PREFIX = "symbol:unresolved:"


def _is_unresolved_symbol_sentinel(nid: str, node: dict) -> bool:
    """Return True iff *nid*/*node* is a lazily-minted unresolved sentinel.

    Keyed on the id prefix -- the one signal every minting strategy stamps
    identically (props shape varies: some strategies add ``kind:
    "unresolved"``, others add ``resolution``/``resolved``, none of that is
    universal) -- guarded by ``type == "symbol"`` defensively, the same way
    :func:`weld._discover_external_package_purge._is_edge_anchored_external_package`
    guards on ``type == "package"``: no minting strategy has ever emitted a
    non-``symbol`` node under this prefix, but a hand-edited or
    project-local-strategy graph is untrusted shape, so the guard costs
    nothing and closes that door structurally rather than by convention.
    """
    if not isinstance(nid, str) or not nid.startswith(_UNRESOLVED_SYMBOL_PREFIX):
        return False
    return node.get("type") == _UNRESOLVED_SYMBOL_TYPE


def emptied_unresolved_symbol_node_ids(
    nodes: dict[str, dict], edges: list[dict],
) -> set[str]:
    """Return ids of unresolved-symbol sentinels with zero inbound edges.

    Call after the ordinary ``props.file`` purge and its edge purge, over
    their *result*: a node found here already lost every inbound edge that
    referenced it (each dropped by the endpoint-membership floor when the
    referencing file's own symbol node was purged -- the referencing file is
    always the edge's ``from`` endpoint here, so this holds regardless of
    whether that edge carried usable provenance), so nothing here re-derives
    *why*, it only names the node whose sole reason to exist just went with
    it. A full discover never mints such a node unless something currently
    references it, so purging it here keeps incremental discovery's output
    equal to a fresh full run's.

    Counts inbound edges of EVERY type (not just ``calls``/``inherits``/
    ``implements``): the sentinel id is shared across every strategy that
    fails to resolve the same bare name, so a node with a surviving inbound
    edge of any kind, from any language or strategy, is never returned --
    matching what a full run over the same partially-emptied tree would
    still emit.
    """
    have_inbound: set[str] = set()
    for edge in edges:
        to_id = edge.get("to")
        if isinstance(to_id, str):
            have_inbound.add(to_id)

    return {
        nid
        for nid, node in nodes.items()
        if _is_unresolved_symbol_sentinel(nid, node) and nid not in have_inbound
    }


__all__ = ["emptied_unresolved_symbol_node_ids"]
