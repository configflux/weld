"""Circular-dependency detection via Tarjan's SCC algorithm.

Provides the ``no-circular-deps`` rule for ``weld.arch_lint``.  Each
non-trivial strongly connected component (size >= 2, or a self-loop) is
reported as a single violation anchored on the SCC's lowest-sorted node
id.  This keeps output stable and deterministic across runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from weld.arch_lint import Violation


def find_cycles(data: dict) -> list[list[str]]:
    """Return non-trivial SCCs using Tarjan's algorithm.

    Each returned list is a strongly connected component with >= 2 members,
    or a single-node self-loop.  Components are sorted internally by node
    id; the outer list is sorted by lowest member.
    """
    nodes: dict = data.get("nodes", {}) or {}
    edges: list = data.get("edges", []) or []

    # Build adjacency list restricted to known node ids.
    node_ids = set(nodes)
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    self_loops: set[str] = set()

    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        if src not in node_ids or dst not in node_ids:
            continue
        if src == dst:
            self_loops.add(src)
        else:
            adj[src].append(dst)

    # Tarjan's SCC -- iterative to avoid Python recursion limits on
    # large graphs.
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    sccs: list[list[str]] = []

    def _strongconnect(v: str) -> None:
        # Use an explicit work-stack to avoid deep recursion.
        work: list[tuple[str, int]] = [(v, 0)]
        index[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        while work:
            node, ei = work[-1]
            neighbors = adj[node]

            if ei < len(neighbors):
                work[-1] = (node, ei + 1)
                w = neighbors[ei]
                if w not in index:
                    index[w] = lowlink[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, 0))
                elif w in on_stack:
                    lowlink[node] = min(lowlink[node], index[w])
            else:
                # All neighbors processed -- check for SCC root.
                if lowlink[node] == index[node]:
                    component: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == node:
                            break
                    if len(component) > 1:
                        sccs.append(sorted(component))

                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(
                        lowlink[parent], lowlink[node]
                    )

    for nid in sorted(node_ids):
        if nid not in index:
            _strongconnect(nid)

    # Self-loops are trivial SCCs that Tarjan skips (size-1 without a
    # back-edge in the adjacency list).  Add them explicitly.
    for nid in sorted(self_loops):
        # Only add if not already part of a larger SCC.
        already = any(nid in scc for scc in sccs)
        if not already:
            sccs.append([nid])

    return sorted(sccs, key=lambda scc: scc[0])


def rule_no_circular_deps(data: dict) -> Iterable[Violation]:
    """Yield one violation per non-trivial SCC in the graph."""
    from weld.arch_lint import Violation  # late import to break cycle

    for scc in find_cycles(data):
        anchor = scc[0]  # lowest-sorted node id
        members = ", ".join(scc)
        yield Violation(
            rule="no-circular-deps",
            node_id=anchor,
            message=(
                f"circular dependency detected: {{{members}}}"
            ),
        )
