"""Pure core for blast-radius analysis (no argparse, no git, no IO).

The :func:`impact` helper here walks the graph in reverse from one or more
seed nodes (or from a single ``target`` resolved to seeds) and summarises
which public surfaces are affected. Output is graph-shaped so the engine
stays language- and build-system-agnostic.

Surface bucketing lives in :mod:`weld.impact_surfaces`, human rendering in
:mod:`weld.impact_format` (re-exported here, since ``weld.impact`` and
``weld.impact_cli`` both address ``format_human`` through this module). The CLI
wrapper lives in :mod:`weld.impact_cli` and calls into this module.
"""

from __future__ import annotations

import json
from collections import deque

from weld.capabilities import compute_capabilities_for_graph as _capabilities_for_graph
from weld.graph import Graph
from weld.impact_format import format_human
from weld.impact_surfaces import (
    _collect_surfaces,
    _empty_surfaces,
    _normalize_path,
    _risk_level,
)

__all__ = ["IMPACT_VERSION", "format_human", "impact"]

IMPACT_VERSION = 2

_LOW_CAPABILITY_EDGE_TYPES = frozenset(["calls", "tests", "depends_on"])

# ADR 0107: node types the reverse walk records but does not expand through.
# A ``package`` node is not a participant in the dependency graph -- across this
# repo's graph its inbound edges are 843 ``depends_on`` and nothing else, and its
# outbound edges are 650 ``contains`` and nothing else. So it is a pure
# aggregation junction, and *every* path through one composes "this file imports
# from that namespace" (which is what a ``depends_on`` into a package means --
# discovery could not resolve which member) with "that namespace holds these
# files", manufacturing a dependency nobody recorded. Measured: it made 65
# unrelated ``tools/*`` siblings dependents of one release script (bd arge).
#
# Keyed on node type, not on the ``contains`` edge type, and the distinction is
# load-bearing: a build target's inbound ``depends_on`` is *declared* at target
# granularity, so "A depends on B" really does mean A depends on all of B's
# sources. Cutting the edge type instead severs those declared build chains and
# loses 17 of weld/graph.py's 26 affected test-targets -- see the rejected
# alternatives in ADR 0107.
_AGGREGATION_NODE_TYPES = frozenset(["package"])


def _edge_key(edge: dict) -> str:
    props = json.dumps(edge.get("props", {}), sort_keys=True, ensure_ascii=True)
    return f"{edge['from']}|{edge['to']}|{edge['type']}|{props}"


def _resolve_target_nodes(graph: Graph, target: str) -> tuple[str, list[str]]:
    """Resolve a single positional *target* (node-id or file path) to seed ids."""
    data = graph.dump()
    nodes: dict[str, dict] = data.get("nodes", {})
    if target in nodes:
        return "node", [target]

    normalized = _normalize_path(target)
    matches: set[str] = set()
    file_node_id = f"file:{normalized}"
    if file_node_id in nodes:
        matches.add(file_node_id)

    for node_id, node in nodes.items():
        props = node.get("props") or {}
        if _normalize_path(str(props.get("file", ""))) == normalized:
            matches.add(node_id)

    return "path", sorted(matches)


def _resolve_paths_to_seeds(
    graph: Graph,
    paths: list[str],
) -> tuple[list[str], list[str]]:
    """Resolve a list of file paths to seed node ids.

    Returns ``(seed_ids, unresolved_inputs)`` -- both sorted. An input path
    that does not match a ``file:*`` node and does not match any node's
    ``props.file`` is preserved in ``unresolved_inputs``; agents rely on
    this for the determinism guarantee.
    """
    data = graph.dump()
    nodes: dict[str, dict] = data.get("nodes", {})
    by_file: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        props = node.get("props") or {}
        file_attr = _normalize_path(str(props.get("file", "")))
        if file_attr:
            by_file.setdefault(file_attr, []).append(node_id)

    seeds: set[str] = set()
    unresolved: list[str] = []
    for raw in paths:
        if not raw:
            continue
        normalized = _normalize_path(raw)
        matched = False
        file_node_id = f"file:{normalized}"
        if file_node_id in nodes:
            seeds.add(file_node_id)
            matched = True
        for node_id in by_file.get(normalized, []):
            seeds.add(node_id)
            matched = True
        if not matched:
            unresolved.append(raw)
    return sorted(seeds), sorted(set(unresolved))


def _reverse_bfs(
    graph: Graph,
    seed_ids: list[str],
    depth: int,
) -> tuple[dict[str, int], list[dict]]:
    data = graph.dump()
    nodes: dict[str, dict] = data.get("nodes", {})
    reverse_adj: dict[str, list[dict]] = {}
    for edge in data.get("edges", []):
        reverse_adj.setdefault(edge["to"], []).append(edge)

    seen: set[str] = set(seed_ids)
    dependents: dict[str, int] = {}
    edges: list[dict] = []
    seen_edges: set[str] = set()
    queue: deque[tuple[str, int]] = deque((node_id, 0) for node_id in seed_ids)

    while queue:
        current, hop = queue.popleft()
        if hop >= depth:
            continue
        for edge in reverse_adj.get(current, []):
            edge_id = _edge_key(edge)
            if edge_id not in seen_edges:
                seen_edges.add(edge_id)
                edges.append(edge)
            src = edge["from"]
            if src in seen:
                continue
            next_hop = hop + 1
            seen.add(src)
            dependents[src] = next_hop
            # ADR 0107: aggregation nodes are recorded, never expanded through.
            if (nodes.get(src) or {}).get("type") in _AGGREGATION_NODE_TYPES:
                continue
            queue.append((src, next_hop))

    return dependents, edges


def _node_with_hop(graph: Graph, node_id: str, hop: int) -> dict:
    node = graph.get_node(node_id)
    if node is None:
        return {"id": node_id, "hop": hop}
    return {**node, "hop": hop}


def _validated_depth(depth: int) -> int:
    if depth < 0:
        raise ValueError("depth must be >= 0")
    return depth


def _seed_nodes(graph: Graph, seed_ids: list[str]) -> list[dict]:
    seeds: list[dict] = []
    for seed_id in seed_ids:
        node = graph.get_node(seed_id)
        if node is not None:
            seeds.append({**node, "hop": 0})
    return seeds


def _low_capability_inputs(
    graph: Graph,
    seed_ids: list[str],
    input_paths: list[str],
) -> list[str]:
    """Return *input_paths* whose seeds have only file-level evidence.

    "Low capability" means none of the resolved seeds for the input has any
    ``calls`` / ``tests`` / ``depends_on`` edges incident to it (in either
    direction). Sorted, deduplicated.
    """
    if not input_paths or not seed_ids:
        return []
    data = graph.dump()
    edges = data.get("edges", [])
    nodes: dict[str, dict] = data.get("nodes", {})
    seed_set = set(seed_ids)
    incident: dict[str, bool] = {sid: False for sid in seed_ids}
    for edge in edges:
        if edge.get("type") not in _LOW_CAPABILITY_EDGE_TYPES:
            continue
        src = edge.get("from")
        dst = edge.get("to")
        if src in seed_set:
            incident[src] = True
        if dst in seed_set:
            incident[dst] = True

    low: list[str] = []
    for path in input_paths:
        normalized = _normalize_path(path)
        candidates: list[str] = []
        file_node_id = f"file:{normalized}"
        if file_node_id in seed_set:
            candidates.append(file_node_id)
        for sid in seed_ids:
            node = nodes.get(sid) or {}
            props = node.get("props") or {}
            if _normalize_path(str(props.get("file", ""))) == normalized:
                candidates.append(sid)
        if not candidates:
            continue
        if not any(incident.get(sid, False) for sid in candidates):
            low.append(path)
    return sorted(set(low))


def _count_unresolved_callsites(seed_ids: list[str], dependents: dict[str, int]) -> int:
    """Count ``symbol:unresolved:*`` nodes touched by the BFS frontier.

    Includes seeds and dependents -- agents care about the union, not just
    the dependents.
    """
    touched = set(seed_ids) | set(dependents.keys())
    return sum(1 for nid in touched if nid.startswith("symbol:unresolved:"))


def _count_speculative_edges(edges: list[dict]) -> int:
    return sum(
        1
        for edge in edges
        if (edge.get("props") or {}).get("confidence") == "speculative"
    )


def _empty_envelope(
    *,
    target_input: str | list[str],
    target_kind: str,
    seed_ids: list[str],
    unresolved_inputs: list[str],
    depth: int,
    stale_graph: bool | None,
    extra_messages: list[str],
    capabilities: dict | None = None,
) -> dict:
    return {
        "impact_version": IMPACT_VERSION,
        "target": {
            "input": target_input,
            "kind": target_kind,
            "resolved_nodes": list(seed_ids),
            "unresolved_inputs": list(unresolved_inputs),
        },
        "depth": depth,
        "direct_dependents": [],
        "transitive_dependents": [],
        "affected_surfaces": _empty_surfaces(),
        "risk_level": "LOW",
        "edges": [],
        "capabilities": capabilities or {"languages": {}, "frameworks": {}},
        "warnings": {
            "unresolved_callsites": 0,
            "speculative_edges": 0,
            "stale_graph": stale_graph,
            "out_of_scope_inputs": list(unresolved_inputs),
            "low_capability_inputs": [],
            "messages": list(extra_messages),
        },
    }


def impact(
    graph: Graph,
    *,
    target: str | None = None,
    depth: int = 3,
    seeds: list[str] | None = None,
    unresolved_inputs: list[str] | None = None,
    seed_kind: str | None = None,
    target_input: str | list[str] | None = None,
    input_paths: list[str] | None = None,
    low_capability_inputs: list[str] | None = None,
    stale_graph: bool | None = None,
) -> dict:
    """Return the reverse-dependency blast radius for a target or seed set.

    Two seed-input modes are supported:

    - ``target`` (str) -- existing positional input. Resolved internally to
      one or more seed node ids.
    - ``seeds`` (list[str]) -- pre-resolved seed node ids supplied by the
      CLI for ``--from-diff`` / ``--files`` / ``--working-tree``.

    Exactly one of *target* or *seeds* must be provided. *unresolved_inputs*
    feeds ``warnings.out_of_scope_inputs``; *input_paths* (the original
    input paths, pre-resolution) feeds ``warnings.low_capability_inputs``.
    *low_capability_inputs* overrides that warning verbatim when not ``None``;
    the federated fan-out precomputes it per child because a flattened child's
    ``props.file`` stays child-relative and a union match here would miss it.
    *stale_graph* is recorded in ``warnings.stale_graph`` and is set by the
    CLI after the staleness gate runs.
    """
    depth = _validated_depth(depth)
    if (target is None) == (seeds is None):
        raise ValueError("impact() requires exactly one of 'target' or 'seeds'")

    extra_messages: list[str] = []
    if target is not None:
        resolved_kind, resolved_seeds = _resolve_target_nodes(graph, target)
        seed_kind = resolved_kind
        seed_ids = resolved_seeds
        target_input_value: str | list[str] = target
        if not seed_ids:
            extra_messages.append(f"no nodes matched target: {target}")
    else:
        seed_ids = list(seeds or [])
        if seed_kind is None:
            seed_kind = "seeds"
        target_input_value = (
            target_input if target_input is not None else list(seed_ids)
        )

    unresolved = list(unresolved_inputs or [])
    if seeds is not None and not seed_ids:
        if unresolved:
            extra_messages.append("no nodes matched any of the provided inputs")
        else:
            extra_messages.append("no seeds provided")

    result = _empty_envelope(
        target_input=target_input_value,
        target_kind=seed_kind or "node",
        seed_ids=seed_ids,
        unresolved_inputs=unresolved,
        depth=depth,
        stale_graph=stale_graph,
        extra_messages=extra_messages,
        capabilities=_capabilities_for_graph(graph),
    )
    if not seed_ids:
        return result

    # ADR 0134 Finding 06: a repo node whose dependents are structurally
    # uncomputable is cannot-answer, not a measured zero. Surface UNKNOWN with
    # the reason before any BFS fabricates a LOW/0 verdict.
    from weld._impact_cannot_answer import (
        cannot_answer_marker,
        cross_repo_measured_by,
        uncomputable_repo_reason,
    )

    unknown_reason = uncomputable_repo_reason(graph, seed_ids)
    if unknown_reason is not None:
        result["risk_level"] = "UNKNOWN"
        result["cannot_answer"] = cannot_answer_marker(unknown_reason)
        return result

    # ADR 0137 ss5: past that gate a repo result is a measurement, so it names
    # what measured it -- which is why a zero here reads as a finding and not a
    # shrug. Absent unless discovery recorded a pass (see the helper).
    measured_by = cross_repo_measured_by(graph, seed_ids)
    if measured_by is not None:
        result["measured_by"] = measured_by

    dependents, edges = _reverse_bfs(graph, seed_ids, depth)
    nodes = [
        _node_with_hop(graph, node_id, hop)
        for node_id, hop in sorted(dependents.items(), key=lambda item: (item[1], item[0]))
    ]
    direct = [node for node in nodes if node["hop"] == 1]
    transitive = [node for node in nodes if node["hop"] > 1]
    surfaces = _collect_surfaces([*_seed_nodes(graph, seed_ids), *nodes])

    result["direct_dependents"] = direct
    result["transitive_dependents"] = transitive
    result["affected_surfaces"] = surfaces
    result["risk_level"] = _risk_level(surfaces)
    result["edges"] = edges
    result["warnings"]["unresolved_callsites"] = _count_unresolved_callsites(
        seed_ids, dependents,
    )
    result["warnings"]["speculative_edges"] = _count_speculative_edges(edges)
    if low_capability_inputs is not None:  # precomputed per child (see docstring)
        result["warnings"]["low_capability_inputs"] = sorted(set(low_capability_inputs))
    elif input_paths:
        result["warnings"]["low_capability_inputs"] = _low_capability_inputs(
            graph, seed_ids, list(input_paths),
        )
    return result
