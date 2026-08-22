"""Purge a membership-anchored package node once its last member is gone.

``python_package`` and ``csharp_package`` each mint a ``package`` node whose
only proof of existence is >=1 outgoing ``contains`` edge to a member
``file:`` node -- ``_has_anchoring_member``'s contract
(``weld/strategies/python_package.py``), ADR 0041 Layer 3
"file-anchor-symmetry". Neither strategy's node carries ``props.file``:
``python_package`` uses ``props.dir``, and ``csharp_package``'s namespace
node carries neither ``dir`` nor ``file`` at all -- its own docstring: "the
node here is a namespace and carries no dir prop -- its members can sit in
any directory". :func:`weld.discovery_state.purge_stale_nodes` matches nodes
by ``props.file`` alone, so deleting every member file purges the member
``file:`` nodes (correctly) and, since ``contains`` edges carry no
provenance, purges the now-dangling ``contains`` edges too (correctly, via
the endpoint-membership floor) -- but never the package node itself, which
lingers as a zero-edge orphan (bd g7rs).

The two strategies are identified by ``props.roles`` containing
``"package"`` -- a marker unique to them among every other strategy that
also mints ``type: "package"`` nodes (``_cmake_packages``, ``cpp_conan``,
``cpp_vcpkg``, the C# tree-sitter using-import sentinel, ...). Those are
external-dependency leaves: files point AT them via inbound ``depends_on``
edges, and having zero *outgoing* edges is their normal steady state, not a
defect. Keying on ``roles`` rather than bare ``type`` is what keeps this
purge from eating them, and it is also the one discriminator that covers
both python_package's ``props.dir`` shape and csharp_package's
neither-dir-nor-file shape with a single rule -- a ``props.dir`` presence
check would not reach csharp_package at all.

The caller (:func:`weld.discovery_state.purge_stale_nodes`) applies this
once, not in a fixed-point loop, and that is not a heuristic bound: neither
``python_package`` nor ``csharp_package`` emits a ``contains`` edge whose
source is itself a membership-anchored node -- a package's members are always
``file:`` nodes, never other packages -- so removing one membership-anchored
node can never change any *other* membership-anchored node's ``contains``
out-edge count. The set this function returns over the first pass's result is
therefore already the complete answer; a second pass over the same input
would return the same (now vacuous, since the caller has already removed
those ids) set. If a future strategy ever nests one membership-anchored
package inside another via ``contains``, this argument -- and the
single-application call site -- must be revisited together.
"""

from __future__ import annotations

_MEMBERSHIP_ROLE = "package"
_MEMBER_EDGE_TYPE = "contains"


def _is_membership_anchored(node: dict) -> bool:
    """Return True when *node*'s only proof of life is a ``contains`` out-edge.

    Defensive the same way ``weld._test_paths.is_test_node`` reads
    ``roles``: props arrive from strategy plugins, including project-local
    ones, so a missing/non-dict ``props`` or a non-list ``roles`` reads as
    "not membership-anchored" rather than raising.
    """
    props = node.get("props") or {}
    if not isinstance(props, dict):
        return False
    roles = props.get("roles") or []
    return isinstance(roles, list) and _MEMBERSHIP_ROLE in roles


def emptied_membership_node_ids(
    nodes: dict[str, dict], edges: list[dict],
) -> set[str]:
    """Return ids of membership-anchored nodes with zero ``contains`` out-edges.

    Call after the ordinary ``props.file`` purge and its edge purge, over
    their *result*: a node found here already lost every ``contains`` edge
    to its member files (each dropped by the ordinary rule when the member
    file's own node was purged), so nothing here re-derives *why* -- it only
    names the node whose anchoring contract that leaves broken. A full
    discover never emits such a node (``_has_anchoring_member`` and the
    analogous csharp_package invariant both refuse it at the source), so
    purging it here keeps incremental discovery's output equal to a fresh
    full run's.

    A node with surviving members (>=1 ``contains`` out-edge) is never
    returned, regardless of how many members it lost -- partial deletion
    keeps the node, matching what a full run over the same partially-emptied
    directory/namespace would still emit.
    """
    have_contains_edge: set[str] = set()
    for edge in edges:
        if edge.get("type") != _MEMBER_EDGE_TYPE:
            continue
        frm = edge.get("from")
        if isinstance(frm, str):
            have_contains_edge.add(frm)

    return {
        nid
        for nid, node in nodes.items()
        if _is_membership_anchored(node) and nid not in have_contains_edge
    }


__all__ = ["emptied_membership_node_ids"]
