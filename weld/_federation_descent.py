"""Read-time root->child descent edges for :class:`weld.federation.FederatedGraph`.

ADR 0081: a federated root's ``repo:<name>`` meta-node carries no persisted
edges (determinism contract, ADR 0012). To let ``context``/``path`` navigate
*down* from a repo node into its child, the federation synthesizes
``repo:<name> --contains--> <child anchor>`` edges at read time -- never
writing them to the root ``graph.json``. Anchors are the child's descent
roots: containment roots (node ids never the ``to`` of a ``contains`` edge)
plus one representative for each root-less containment *cycle* (ADR 0091), so
every child node -- including one stranded in a pure ``A contains B``,
``B contains A`` cycle -- stays reachable through child-internal edges.

Kept in its own module so ``weld/federation.py`` stays within the 400-line
cap; ``descent_edges_for`` takes the federation object, mirroring the
``weld._federation_query.query_federated(federation, ...)`` seam.
"""

from __future__ import annotations

from weld._sqlite_reader import SqliteBackedGraph
from weld.federation_support import prefix_node_id
from weld.graph import Graph

__all__ = ["child_containment_roots", "descent_edges_for"]

#: Meta-node id prefix for a federated child repo (``repo:<name>``).
_REPO_PREFIX = "repo:"


def _contains_adjacency(
    child: Graph | SqliteBackedGraph,
) -> tuple[list[str], dict[str, list[str]]]:
    """Return ``(node_ids, succ)`` for the child's ``contains`` graph.

    ``node_ids`` is every child node id; ``succ`` maps a node id to its
    sorted ``contains`` successors. The sqlite-backed path streams via
    iterators (ADR 0058 laziness) and this pulls only node ids and
    ``contains`` edges -- never full child props. Successor lists are sorted so
    downstream traversal is a pure function of node ids (ADR 0012).
    """
    if isinstance(child, SqliteBackedGraph):
        node_ids: list[str] = [node["id"] for node in child.iter_nodes()]
        edges = child.iter_edges()
    else:
        data = child.dump()
        node_ids = list(data.get("nodes", {}).keys())
        edges = data.get("edges", [])
    succ: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("type") != "contains":
            continue
        succ.setdefault(edge["from"], []).append(edge["to"])
    for src in succ:
        succ[src].sort()
    return node_ids, succ


def _forward_closure(roots: list[str], succ: dict[str, list[str]]) -> set[str]:
    """Return every node reachable from ``roots`` over ``contains`` succ.

    Iterative breadth/depth walk (no recursion): a deep or cyclic child cannot
    overflow the call stack, and the ``seen`` guard makes cycles terminate.
    """
    seen: set[str] = set(roots)
    frontier: list[str] = list(seen)
    while frontier:
        node = frontier.pop()
        for nxt in succ.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


def _tarjan_scc(nodes: list[str], succ: dict[str, list[str]], scope: set[str]) -> dict[str, int]:
    """Iterative Tarjan SCC over the ``scope``-restricted ``contains`` edges.

    Returns ``{node_id: component_id}``. Uses an explicit work stack (never
    recursion) so an adversarial deep or densely cyclic child cannot exhaust
    the Python call stack. Start nodes and successors are visited in sorted
    order; callers depend only on the partition and on ``min()`` ids within a
    component, not on the assigned component-id values, so the result is a pure
    function of the input (ADR 0012).
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    scc_stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    next_comp = 0
    for start in sorted(nodes):
        if start in index_of:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, resume = work[-1]
            if resume == 0:
                index_of[node] = lowlink[node] = counter
                counter += 1
                scc_stack.append(node)
                on_stack.add(node)
            children = [dst for dst in succ.get(node, ()) if dst in scope]
            advanced = False
            for i in range(resume, len(children)):
                nxt = children[i]
                if nxt not in index_of:
                    work[-1] = (node, i + 1)
                    work.append((nxt, 0))
                    advanced = True
                    break
                if nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[nxt])
            if advanced:
                continue
            if lowlink[node] == index_of[node]:
                while True:
                    popped = scc_stack.pop()
                    on_stack.discard(popped)
                    comp[popped] = next_comp
                    if popped == node:
                        break
                next_comp += 1
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
    return comp


def _source_cycle_anchors(orphans: list[str], succ: dict[str, list[str]]) -> set[str]:
    """Return one min-id anchor per source SCC among ``orphans``.

    ``orphans`` are child nodes unreachable from any containment root -- exactly
    the nodes stranded in root-less ``contains`` cycles. Their SCCs are taken
    over the orphan-restricted adjacency (a reachable node never has a
    ``contains`` edge into an orphan, so no boundary edge is lost). Each
    *source* component -- one with no incoming edge from a different orphan
    component -- contributes its lexicographically-smallest id: descending to it
    reaches the whole component, and every non-source orphan component is
    reached from a source through child-internal edges.
    """
    orphan_set = set(orphans)
    comp = _tarjan_scc(orphans, succ, orphan_set)
    non_source: set[int] = set()
    for src in orphans:
        for dst in succ.get(src, ()):
            if dst in orphan_set and comp[src] != comp[dst]:
                non_source.add(comp[dst])
    reps: dict[int, str] = {}
    for node in orphans:
        cid = comp[node]
        if cid in non_source:
            continue
        if cid not in reps or node < reps[cid]:
            reps[cid] = node
    return set(reps.values())


def child_containment_roots(child: Graph | SqliteBackedGraph) -> list[str]:
    """Return the child-local descent anchors for its ``repo:<name>`` node.

    Anchors are the roots of the child's containment forest -- node ids never
    the ``to`` of a ``contains`` edge -- plus, for any root-less containment
    *cycle*, the lexicographically-smallest id in that strongly-connected
    component (ADR 0091). Descending to this set keeps every child node
    reachable through child-internal ``contains`` edges, including nodes that a
    pure ``A contains B`` / ``B contains A`` cycle would otherwise strand.

    For an acyclic ``contains`` graph every node is reachable from an
    in-degree-0 root, the cycle path never runs, and the result is
    byte-identical to the containment-root rule of ADR 0081. Ids are returned
    sorted for deterministic edge ordering (ADR 0012).

    The sqlite-backed path streams via iterators (ADR 0058 laziness); the
    JSON-backed :class:`Graph` is already fully in memory.
    """
    node_ids, succ = _contains_adjacency(child)
    contained = {to for tos in succ.values() for to in tos}
    acyclic_roots = [node_id for node_id in node_ids if node_id not in contained]
    reachable = _forward_closure(acyclic_roots, succ)
    orphans = [node_id for node_id in node_ids if node_id not in reachable]
    if not orphans:
        return sorted(acyclic_roots)
    anchors = set(acyclic_roots) | _source_cycle_anchors(orphans, succ)
    return sorted(anchors)


def descent_edges_for(federation, node_id: str) -> list[dict]:
    """Return synthetic ``repo:<name> --contains--> <child root>`` edges.

    Returns ``[]`` for anything that is not a ``repo:<name>`` node backed by a
    present, readable child: non-repo ids, unregistered names, and
    missing/uninitialized/corrupt children all descend to nothing. The emitted
    edges are undecorated ``{"from","to","type","props"}`` dicts so the
    caller's existing edge-decoration path stamps display ids uniformly.
    """
    if not node_id.startswith(_REPO_PREFIX):
        return []
    name = node_id[len(_REPO_PREFIX):]
    if name not in federation._children:
        return []
    child = federation._load_child(name)
    if not isinstance(child, (Graph, SqliteBackedGraph)):
        return []
    return [
        {
            "from": node_id,
            "to": prefix_node_id(name, root_id),
            "type": "contains",
            "props": {},
        }
        for root_id in child_containment_roots(child)
    ]
