"""Build a :class:`FederationIdIndex` for a workspace root (ADR 0137 ss3).

Validation, ``wd doctor`` and the discovery merge all ask the same question of
a federated root: *does this edge endpoint name something a reader could look
up?* Answering it needs the node ids of the root meta-graph and of every
**registered** child -- including the children that are not there, which is
what separates a wrong endpoint from an unverifiable one.

This module builds exactly that and nothing more. It deliberately does not go
through :func:`weld._federation_flatten.flatten_federation`, which unions the
whole workspace -- nodes, edges and an inverted index -- into one
:class:`~weld.graph.Graph`. Reference checking costs a set of strings per
child; paying for a second copy of the workspace to get it would make the
check expensive enough to be worth skipping, which is how the shape-only
bypass survived in the first place.
"""

from __future__ import annotations

from pathlib import Path

from weld._federation_validate import FederationIdIndex, UNKNOWN_CHILD_STATE
from weld._sqlite_reader import SqliteBackedGraph
from weld.graph import Graph

__all__ = ["federation_id_index", "federation_id_index_for_root"]


def _child_node_ids(child: object) -> frozenset[str] | None:
    """Return *child*'s node ids, or ``None`` when its graph is unreadable.

    ``None`` is the ``unverifiable`` signal: the child is registered, but the
    loader handed back a missing / uninitialized / corrupt sentinel instead of
    a graph, so nothing about its ids can be asserted either way.
    """
    if isinstance(child, SqliteBackedGraph):
        # Ids only: ``dump()`` would also materialise every edge, and
        # ``iter_nodes`` streams rather than building a second node list.
        return frozenset(str(node["id"]) for node in child.iter_nodes())
    if isinstance(child, Graph):
        # Already parsed in memory -- the keys are the ids.
        return frozenset(child.dump().get("nodes", {}))
    return None


def federation_id_index(fg) -> FederationIdIndex:
    """Return the id index for an open :class:`~weld.federation.FederatedGraph`.

    Every registered child is visited, not only the present ones: a child that
    cannot be read still gets an entry, mapped to ``None``, so
    :meth:`~weld._federation_validate.FederationIdIndex.classify_endpoint` can
    tell "no such node" from "cannot say".
    """
    root_ids = frozenset(fg._root_graph.dump().get("nodes", {}))
    child_ids: dict[str, frozenset[str] | None] = {}
    child_states: dict[str, str] = {}
    for name in sorted(fg._children):
        child = fg._load_child(name)
        ids = _child_node_ids(child)
        child_ids[name] = ids
        if ids is None:
            child_states[name] = str(
                getattr(child, "status", UNKNOWN_CHILD_STATE)
            )
    return FederationIdIndex(
        root_ids=root_ids, child_ids=child_ids, child_states=child_states,
    )


def federation_id_index_for_root(root: Path | str) -> FederationIdIndex | None:
    """Return the index for *root*, or ``None`` when it is not a workspace root.

    ``None`` is the honest answer for a single repo -- there are no children to
    resolve into, so the caller keeps the shape-only check that is correct
    there. Imports are function-local because the contract library, which
    consumes the index type, must not pull the graph runtime in behind it.
    """
    from weld.federation import FederatedGraph
    from weld.workspace_state import load_workspace_config

    if load_workspace_config(root) is None:
        return None
    # eager_index=False: the aggregated inverted index (ADR 0063) serves
    # ``query``, and nothing here queries. The context manager closes the
    # sqlite handles the child loads may have opened.
    with FederatedGraph(Path(root), eager_index=False) as fg:
        return federation_id_index(fg)
