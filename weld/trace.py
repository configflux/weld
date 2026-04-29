"""``wd trace`` -- protocol-aware capability path surface.

``wd trace`` returns the shortest cross-boundary slice for a capability
or known node. It is the protocol-aware companion to ``wd brief`` v2:
where ``brief`` ranks classification buckets across a tokenized search,
``trace`` follows interaction-relevant edges from an anchor and emits a
service / interface / contract / boundary / verification slice that
agents can consume directly.

Per ADR 0018 and tracked project this surface MUST:

  - reuse the existing graph semantics -- node classification is
    delegated to ``weld.brief._classify_node`` so we never invent a second
    interaction model;
  - model output around service / contract / interface / boundary /
    verification links;
  - be optimized for agent consumption (stable JSON envelope, no
    terminal-only formatting tricks).

Output envelope (stable contract)::

    {
        "trace_version": 1,
        "anchor": {"kind": "term"|"node", ...},
        "services": [...],
        "interfaces": [...],
        "contracts": [...],
        "boundaries": [...],
        "verifications": [...],
        "edges": [...],
        "provenance": {"graph_sha": ..., "updated_at": ...},
        "warnings": [...]
    }

"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

from weld.brief import _classify_node
from weld.ranking import rank_key as _rank_key
from weld.warnings import check_confidence_gaps, check_freshness, check_partial_coverage

# -- Stable JSON output contract -------------------------------------------
TRACE_VERSION: int = 1

# Default BFS depth from each anchor seed. Two hops is enough to cover
# service -> interface -> contract and service -> verification while
# keeping the slice agent-readable.
_DEFAULT_DEPTH: int = 2

# Default cap on anchor seeds when tracing by term so a noisy query
# doesn't pull the entire graph into the slice.
_DEFAULT_SEED_LIMIT: int = 5

# Edge types that carry protocol-aware capability paths. We follow these
# in *both* directions so a slice rooted at an interface still finds its
# owning service, and a slice rooted at a service still finds the tests
# that verify it. Reuses the contract's existing edge vocabulary -- no
# new edge types are introduced.
_TRACE_EDGE_TYPES: frozenset[str] = frozenset([
    "contains", "exposes", "consumes", "produces",
    "implements", "accepts", "responds_with",
    "verifies", "tests", "documents",
    "depends_on", "invokes", "feeds_into",
    "enforces",
])

# Node types that go into each bucket. ``interfaces`` is intentionally
# omitted -- interface classification is delegated to
# ``weld.brief._classify_node`` so the route-with-protocol promotion rule
# applies here too.
_SERVICE_TYPES: frozenset[str] = frozenset(["service", "package"])
_CONTRACT_TYPES: frozenset[str] = frozenset(["contract", "enum"])
_VERIFICATION_TYPES: frozenset[str] = frozenset([
    "test-target", "test-suite", "gate",
])

def _bucket_for(node: dict) -> str | None:
    """Return the trace bucket name for *node*, or ``None`` to drop it.

    Order matters: interfaces and boundaries are checked via
    :func:`weld.brief._classify_node` so a ``route`` carrying ``protocol``
    metadata still ends up in the interfaces bucket (tracked project).
    Services and contracts use their static node type. Verifications
    catch test/gate nodes that ``brief`` would put in its ``build``
    bucket -- in a trace we want them surfaced as the verification arm
    of the slice.
    """
    ntype = node.get("type", "")
    category = _classify_node(node)
    if category == "interface":
        return "interfaces"
    if category == "boundary":
        return "boundaries"
    if ntype in _SERVICE_TYPES:
        return "services"
    if ntype in _CONTRACT_TYPES:
        return "contracts"
    if ntype in _VERIFICATION_TYPES:
        return "verifications"
    # Anything else (docs, generic primaries, symbols, ...) is dropped
    # from the trace slice -- ``wd brief`` is the right surface for
    # those. ``trace`` is intentionally narrow.
    return None

def _seed_from_term(graph: Any, term: str, limit: int) -> list[str]:
    """Return seed node ids from a tokenized query, biased toward
    services and interaction surfaces."""
    query_result = graph.query(term, limit=limit * 4)
    matches = query_result.get("matches", [])
    if not matches:
        return []
    # Prefer matches that are themselves a service / interface /
    # boundary / contract -- they make better trace seeds than a random
    # primary hit. Fall back to any match if no preferred seed exists.
    preferred = [
        m for m in matches
        if _bucket_for(m) is not None
    ]
    if preferred:
        chosen = preferred[:limit]
    else:
        chosen = matches[:limit]
    return [m["id"] for m in chosen]

def _walk(
    nodes: dict[str, dict],
    adjacency: dict[str, list[tuple[str, dict]]],
    seeds: list[str],
    depth: int,
) -> tuple[set[str], list[dict]]:
    """BFS from *seeds* up to *depth* hops along trace edges.

    Returns ``(visited_node_ids, traversed_edges)``. Edges are returned
    in the order they were first crossed; duplicates are de-duplicated
    by identity (``from``/``to``/``type``).
    """
    visited: set[str] = set(s for s in seeds if s in nodes)
    edges_seen: set[tuple[str, str, str]] = set()
    out_edges: list[dict] = []
    frontier: deque[tuple[str, int]] = deque(
        (s, 0) for s in visited
    )
    while frontier:
        current, d = frontier.popleft()
        if d >= depth:
            continue
        for neighbor, edge in adjacency.get(current, []):
            key = (edge["from"], edge["to"], edge["type"])
            if key not in edges_seen:
                edges_seen.add(key)
                out_edges.append(edge)
            if neighbor not in visited and neighbor in nodes:
                visited.add(neighbor)
                frontier.append((neighbor, d + 1))
    return visited, out_edges

def _build_adjacency(
    edges: list[dict],
) -> dict[str, list[tuple[str, dict]]]:
    """Build an undirected adjacency map limited to trace edge types."""
    adj: dict[str, list[tuple[str, dict]]] = {}
    for edge in edges:
        if edge.get("type") not in _TRACE_EDGE_TYPES:
            continue
        a, b = edge["from"], edge["to"]
        adj.setdefault(a, []).append((b, edge))
        adj.setdefault(b, []).append((a, edge))
    return adj

def _sort_nodes(nodes: list[dict]) -> list[dict]:
    """Sort by the shared ranking composite for stable output."""
    return sorted(nodes, key=lambda n: _rank_key(n))

def trace(
    graph: Any,
    *,
    term: str | None = None,
    node_id: str | None = None,
    depth: int = _DEFAULT_DEPTH,
    seed_limit: int = _DEFAULT_SEED_LIMIT,
) -> dict:
    """Build a cross-boundary slice for *term* or *node_id*.

    Exactly one of *term* / *node_id* must be supplied. Returns the
    stable JSON envelope documented in the module docstring.
    """
    if (term is None) == (node_id is None):
        raise ValueError("trace() requires exactly one of term or node_id")

    warnings: list[str] = []
    data = graph.dump()
    nodes: dict[str, dict] = data.get("nodes", {})
    edges: list[dict] = data.get("edges", [])

    # -- anchor seeds --
    if node_id is not None:
        anchor: dict[str, str] = {"kind": "node", "id": node_id}
        if node_id not in nodes:
            warnings.append(f"Anchor node not found: {node_id!r}")
            seeds: list[str] = []
        else:
            seeds = [node_id]
    else:
        assert term is not None
        anchor = {"kind": "term", "term": term}
        seeds = _seed_from_term(graph, term, seed_limit)
        if not seeds:
            warnings.append(f"No anchor matches found for term: {term!r}")

    # -- BFS walk --
    adjacency = _build_adjacency(edges)
    visited, walked_edges = _walk(nodes, adjacency, seeds, depth)

    # -- bucket nodes --
    services: list[dict] = []
    interfaces: list[dict] = []
    contracts: list[dict] = []
    boundaries: list[dict] = []
    verifications: list[dict] = []
    for nid in visited:
        node = {"id": nid, **nodes[nid]}
        bucket = _bucket_for(node)
        if bucket == "services":
            services.append(node)
        elif bucket == "interfaces":
            interfaces.append(node)
        elif bucket == "contracts":
            contracts.append(node)
        elif bucket == "boundaries":
            boundaries.append(node)
        elif bucket == "verifications":
            verifications.append(node)

    services = _sort_nodes(services)
    interfaces = _sort_nodes(interfaces)
    contracts = _sort_nodes(contracts)
    boundaries = _sort_nodes(boundaries)
    verifications = _sort_nodes(verifications)

    # -- prune edges to only those whose endpoints landed in the slice --
    kept_ids = {n["id"] for n in services}
    kept_ids.update(n["id"] for n in interfaces)
    kept_ids.update(n["id"] for n in contracts)
    kept_ids.update(n["id"] for n in boundaries)
    kept_ids.update(n["id"] for n in verifications)
    pruned_edges = [
        e for e in walked_edges
        if e["from"] in kept_ids and e["to"] in kept_ids
    ]

    # -- provenance --
    meta = data.get("meta", {})
    provenance = {
        "graph_sha": meta.get("git_sha"),
        "updated_at": meta.get("updated_at"),
    }

    # -- Interaction-retrieval warnings (tracked project) --
    # Emit freshness and partial-coverage warnings so consuming agents
    # can judge confidence in the interaction data.
    warnings.extend(check_freshness(graph))
    warnings.extend(
        check_partial_coverage(interfaces, boundaries, services=services)
    )
    all_interaction = interfaces + boundaries
    warnings.extend(check_confidence_gaps(all_interaction))

    return {
        "trace_version": TRACE_VERSION,
        "anchor": anchor,
        "services": services,
        "interfaces": interfaces,
        "contracts": contracts,
        "boundaries": boundaries,
        "verifications": verifications,
        "edges": pruned_edges,
        "provenance": provenance,
        "warnings": warnings,
    }

def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``wd trace``."""
    parser = argparse.ArgumentParser(
        prog="wd trace",
        description=(
            "Protocol-aware capability path surface: returns a "
            "service / interface / contract / boundary / verification "
            "slice for an anchor (term or node id)."
        ),
    )
    parser.add_argument(
        "term", nargs="?", default=None,
        help="Search term (tokenized like `wd query`); omit when --node is set",
    )
    parser.add_argument(
        "--node", dest="node_id", default=None,
        help="Anchor by node id instead of a term",
    )
    parser.add_argument(
        "--root", type=Path, default=Path("."),
        help="Project root directory",
    )
    parser.add_argument(
        "--depth", type=int, default=_DEFAULT_DEPTH,
        help=f"BFS depth from each anchor seed (default {_DEFAULT_DEPTH})",
    )
    parser.add_argument(
        "--seed-limit", type=int, default=_DEFAULT_SEED_LIMIT,
        help=(
            "Max anchor seeds when tracing by term "
            f"(default {_DEFAULT_SEED_LIMIT})"
        ),
    )
    args = parser.parse_args(argv)

    if (args.term is None) == (args.node_id is None):
        parser.error("provide either a term or --node, not both")

    from weld._graph_cli import _build_retry_hint, ensure_graph_exists
    from weld.graph import Graph

    # Surface a friendly first-run message when the graph has not been
    # built yet; mirrors the behaviour of read commands in _graph_cli
    # (tracked issue / tracked issue).
    retry_cmd = (
        _build_retry_hint("trace", args.term)
        if args.term is not None
        else _build_retry_hint("trace", node=args.node_id)
    )
    ensure_graph_exists(args.root, retry_cmd)

    g = Graph(args.root)
    g.load()
    result = trace(
        g,
        term=args.term,
        node_id=args.node_id,
        depth=args.depth,
        seed_limit=args.seed_limit,
    )
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
