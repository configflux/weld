"""Circular-dependency detection via Tarjan's SCC algorithm.

Provides the ``no-circular-deps`` rule for ``weld.arch_lint``.  Each
non-trivial strongly connected component (size >= 2, or a self-loop) is
reported as a single violation anchored on the SCC's lowest-sorted node
id.  This keeps output stable and deterministic across runs.

``rule_no_circular_deps`` excludes :data:`NON_STRUCTURAL_EDGE_TYPES` from
the SCC walk (bd 5038-ojg27) -- each entry is a measured false-positive
source on this repo's own graph, not a speculative exclusion:

* ``relates_to`` -- two docs that cross-reference each other in prose
  (``[text](other.md)``) mint a mutual edge by construction; that is
  ordinary hypertext, not a dependency. Confirmed on this repo: every
  ``relates_to``-only SCC found was a pair or cluster of ADRs/guides
  linking each other in their own bodies.
* ``documents`` -- a doc citing a code file in its body (a backtick-quoted
  path or dotted module) is a citation, not something the doc *needs* to
  function.
* ``validates`` -- a lint/governance script asserting it checks a doc is
  an authority relationship, not a build/runtime dependency. Combined
  with ``documents`` (the doc's own body citing that same script) this
  bridge alone was gluing unrelated real cycles onto whole documentation
  clusters: excluding both once each is enough, no node-type check needed.
* ``calls`` -- a symbol calling another symbol is the function call graph.
  Self-loops and mutual recursion are normal control flow (recursive
  descent parsers, tree walkers, recursive merges), never a layering
  violation. Every ``calls``-only SCC measured on this repo was exactly
  that shape.
* ``decorates`` -- decorator attribution (ADR 0122) is metaprogramming
  between two symbols, not a coupling one depends on to build or run.
* ``references`` -- a same-module bare-name value reference (ADR 0127) is
  weaker than a call; excluded for the same reason as ``calls``.

Every other edge type -- ``depends_on``, ``contains``, ``implements``,
``inherits``, and the rest of the vocabulary -- still contributes to the
walk, so a cycle that mixes doc/symbol nodes with a genuine structural
edge (e.g. a real ``doc --documents--> file --depends_on--> ... --> doc``
chain, if one existed) still gets reported in full: the exclusion is
edge-type-scoped, not a blanket doc/symbol node exemption, so it cannot
hide a cycle just because a doc or symbol node happens to sit on it.
``find_cycles`` itself stays a type-agnostic SCC primitive -- the
exclusion is applied by its caller, not baked into the algorithm.
"""

from __future__ import annotations

from typing import Iterable

from weld._arch_lint_types import Violation

#: Edge types that connect nodes without implying a structural, load-bearing
#: dependency between them -- see the module docstring for the measured
#: false-positive evidence behind each entry. ``rule_no_circular_deps``
#: passes this to :func:`find_cycles`; direct callers of ``find_cycles``
#: (e.g. its own unit tests) are unaffected since the default is "exclude
#: nothing".
NON_STRUCTURAL_EDGE_TYPES: frozenset[str] = frozenset({
    "relates_to", "documents", "validates", "calls", "decorates", "references",
})


def find_cycles(
    data: dict, *, exclude_edge_types: frozenset[str] = frozenset()
) -> list[list[str]]:
    """Return non-trivial SCCs using Tarjan's algorithm.

    Each returned list is a strongly connected component with >= 2 members,
    or a single-node self-loop.  Components are sorted internally by node
    id; the outer list is sorted by lowest member.

    *exclude_edge_types* drops matching edges before the walk so they can
    never contribute to a reported cycle.  Defaults to the empty set (walk
    every edge) -- a pure, type-agnostic SCC primitive with no rule-level
    opinion baked in; ``rule_no_circular_deps`` is the caller that supplies
    :data:`NON_STRUCTURAL_EDGE_TYPES`.
    """
    nodes: dict = data.get("nodes", {}) or {}
    edges: list = data.get("edges", []) or []

    # Build adjacency list restricted to known node ids.
    node_ids = set(nodes)
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    self_loops: set[str] = set()

    for edge in edges:
        if edge.get("type") in exclude_edge_types:
            continue
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
    """Yield one violation per non-trivial structural-edge SCC in the graph.

    Walks every edge type except :data:`NON_STRUCTURAL_EDGE_TYPES` -- see
    the module docstring for why each of those is excluded.
    """
    for scc in find_cycles(data, exclude_edge_types=NON_STRUCTURAL_EDGE_TYPES):
        anchor = scc[0]  # lowest-sorted node id
        members = ", ".join(scc)
        yield Violation(
            rule="no-circular-deps",
            node_id=anchor,
            message=(
                f"circular dependency detected: {{{members}}}"
            ),
        )
