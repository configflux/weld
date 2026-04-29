"""Agent-facing briefing surface for the connected structure.

Returns a compact, LLM-friendly context packet instead of making agents
assemble low-level queries manually. Ranks authoritative, high-confidence,
and interaction-relevant context ahead of generic matches.

"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from weld.contract import (
    BOUNDARY_KIND_VALUES,
    PROTOCOL_VALUES,
    SURFACE_KIND_VALUES,
)
from weld.graph_query import query_or_fallback
from weld.ranking import rank_key as _rank_key
from weld.warnings import check_confidence_gaps, check_freshness, check_partial_coverage

# -- Stable JSON output contract -------------------------------------------
#
# The brief output is a versioned JSON envelope. v2 adds the ``interfaces``
# bucket and interaction-aware ranking per ADR 0018 and tracked project:
#
#   {
#     "brief_version": 2,
#     "query": "<original term>",
#     "primary": [ ... ranked nodes (implementation/domain) ... ],
#     "interfaces": [ ... rpc/channel/ros_* surfaces carrying protocol ... ],
#     "docs": [ ... authoritative docs and policies ... ],
#     "build": [ ... build/test/gate surfaces ... ],
#     "boundaries": [ ... boundary/entrypoint nodes ... ],
#     "edges": [ ... connecting edges ... ],
#     "provenance": { "graph_sha": "...", "updated_at": "..." },
#     "warnings": [ ... diagnostic strings ... ]
#   }
#
# Contract rules:
#   - brief_version is 2 (bumped from 1; bump again on breaking changes).
#   - All list fields default to [] (never null/absent).
#   - provenance is always present (fields may be null if unavailable).
#   - warnings is always present (empty list means no issues).
#   - Node entries include a ``relevance`` field explaining why they ranked.
#   - When the query is interaction-relevant, interfaces and boundaries are
#     emitted before generic primary in the envelope field order, and each
#     interaction node carries ``interaction_boost`` in its ``relevance``
#     text so agents can see why it ranked first.

BRIEF_VERSION: int = 2

# Node types that count as authoritative docs/policies.
_DOC_TYPES: frozenset[str] = frozenset(["doc", "policy", "runbook"])

# Node types that count as build/verification surfaces.
_BUILD_TYPES: frozenset[str] = frozenset([
    "build-target", "test-target", "test-suite", "gate",
])

# Node types that count as boundaries/entrypoints.
_BOUNDARY_TYPES: frozenset[str] = frozenset(["boundary", "entrypoint"])

# Node types that count as interaction surfaces -- ``rpc``/``channel`` are
# the generalized Phase 7 vocabulary (ADR 0018); ROS2 interaction nodes are
# their domain-specific counterparts and belong alongside them in the
# interfaces bucket.
_INTERFACE_TYPES: frozenset[str] = frozenset([
    "rpc", "channel",
    "ros_service", "ros_action", "ros_topic", "ros_interface",
])

# Roles that signal doc-like content.
_DOC_ROLES: frozenset[str] = frozenset(["doc", "policy"])

# Roles that signal build/verification content.
_BUILD_ROLES: frozenset[str] = frozenset(["build", "test", "gate"])

# Query tokens that indicate the caller is asking about interaction surfaces.
# Hitting any of these flips interaction-aware ranking on so that interfaces
# and boundaries surface ahead of generic primary matches.
_INTERACTION_QUERY_TOKENS: frozenset[str] = frozenset([
    "interface", "interfaces", "boundary", "boundaries", "protocol",
    "protocols", "rpc", "grpc", "http", "api", "endpoint", "endpoints",
    "route", "routes", "channel", "channels", "topic", "topics", "event",
    "events", "stream", "streams", "pubsub", "pub_sub", "publish",
    "subscribe", "consumer", "producer", "handler", "handlers",
    "request", "response", "call", "calls", "invoke", "invokes",
    "ros2",
])

def _has_interaction_metadata(node: dict) -> bool:
    """Return True if *node* carries any interaction-surface metadata.

    Per ADR 0018, ``protocol``, ``surface_kind``, ``transport``, and
    ``boundary_kind`` are optional props that can ride on any node type.
    A node is interaction-relevant when any of them is set to a recognized
    vocabulary value.
    """
    props = node.get("props") or {}
    protocol = props.get("protocol")
    if isinstance(protocol, str) and protocol in PROTOCOL_VALUES:
        return True
    surface_kind = props.get("surface_kind")
    if isinstance(surface_kind, str) and surface_kind in SURFACE_KIND_VALUES:
        return True
    boundary_kind = props.get("boundary_kind")
    if isinstance(boundary_kind, str) and boundary_kind in BOUNDARY_KIND_VALUES:
        return True
    # ``transport`` alone is not a reliable signal -- it is usually paired
    # with ``protocol``. Requiring at least one of the primary three props
    # avoids boosting nodes that just happen to mention a port.
    return False

def _query_is_interaction_relevant(term: str) -> bool:
    """Return True if the query term mentions interaction concepts.

    Uses the same lower-cased whitespace tokenization as ``Graph.query``
    so the signal is consistent with how matches are found in the first
    place. The check is permissive: a single hit flips the flag.
    """
    tokens = term.lower().split()
    return any(tok in _INTERACTION_QUERY_TOKENS for tok in tokens)

def _classify_node(node: dict) -> str:
    """Classify a node into one of:
    'doc', 'build', 'interface', 'boundary', 'primary'.

    Uses both node type and roles metadata for classification. Interfaces
    take precedence over ``primary`` but not over more specific buckets
    (docs/build/boundary) so a boundary that also declares a protocol
    stays in ``boundaries``.
    """
    ntype = node.get("type", "")
    props = node.get("props") or {}
    roles = set(props.get("roles", []))
    doc_kind = props.get("doc_kind", "")

    if (
        ntype in _DOC_TYPES
        or roles & _DOC_ROLES
        or doc_kind in ("adr", "policy", "runbook", "guide")
    ):
        return "doc"
    if (
        ntype in _BUILD_TYPES
        or roles & _BUILD_ROLES
        or doc_kind in ("gate", "verification")
    ):
        return "build"
    if ntype in _BOUNDARY_TYPES:
        return "boundary"
    if ntype in _INTERFACE_TYPES:
        return "interface"
    # Any other node that statically declares interaction-surface metadata
    # is promoted to the interfaces bucket even if its primary type is
    # something else (e.g. a ``route`` stamped with ``protocol=http``).
    if _has_interaction_metadata(node):
        return "interface"
    return "primary"

def _sort_key(
    node: dict,
    *,
    interaction_relevant: bool = False,
) -> tuple[int, int, int, int, str]:
    """Sort key for brief buckets.

    Adds an interaction boost on top of the shared ranking composite so
    that interfaces/boundaries/interaction-annotated nodes rank ahead of
    generic peers when the query is interaction-relevant. The composite
    layout is ``(interaction_boost, role_boost, authority, confidence,
    id)``. A value of 0 sorts first; 1 sorts after.

    When *interaction_relevant* is False the boost is a constant 0 so
    this function stays drop-in-compatible with v1 sort ordering within
    a single bucket.
    """
    role, authority, confidence, node_id = _rank_key(node)
    if interaction_relevant and _has_interaction_metadata(node):
        boost = 0
    elif interaction_relevant:
        boost = 1
    else:
        boost = 0
    return (boost, role, authority, confidence, node_id)

def _add_relevance(node: dict, reason: str) -> dict:
    """Return a copy of the node dict with a ``relevance`` field."""
    result = dict(node)
    result["relevance"] = reason
    return result

def brief(graph: Any, term: str, limit: int = 20) -> dict:
    """Build a brief context packet for *term* from *graph*.

    Parameters
    ----------
    graph : weld.graph.Graph
        A loaded Graph instance.
    term : str
        The query term (tokenized search, same as ``wd query``).
    limit : int
        Maximum number of nodes per section.

    Returns
    -------
    dict
        The brief JSON envelope (see module docstring for contract).
    """
    warnings: list[str] = []
    interaction_relevant = _query_is_interaction_relevant(term)
    degraded_match: str | None = None

    # Run the same tokenized query as ``wd query``.
    query_result = graph.query(term, limit=limit * 3)  # over-fetch
    matches = query_result.get("matches", [])
    neighbors = query_result.get("neighbors", [])
    edges = query_result.get("edges", [])

    # OR-fallback (Bug-3): when strict-AND zeroes on a multi-token query,
    # retry via a softer per-group union. Tag the result so consumers see
    # they did not get strict-AND. Single-token queries skip this path
    # because OR == AND for one group -- the retry would be identical.
    if not matches and len(term.split()) > 1:
        fallback = query_or_fallback(graph, term, limit=limit * 3)
        fallback_matches = fallback.get("matches", [])
        if fallback_matches:
            matches = fallback_matches
            neighbors = fallback.get("neighbors", [])
            edges = fallback.get("edges", [])
            degraded_match = "or_fallback"
            warnings.append(
                f"Strict AND returned no matches for {term!r}; "
                f"retried with OR fallback (degraded_match=or_fallback)."
            )

    if not matches:
        warnings.append(f"No matches found for query: {term!r}")

    # Classify and bucket matches.
    primary: list[dict] = []
    interfaces: list[dict] = []
    docs: list[dict] = []
    build: list[dict] = []
    boundaries: list[dict] = []

    for node in matches:
        category = _classify_node(node)
        if category == "doc":
            docs.append(_add_relevance(node, "authoritative doc/policy"))
        elif category == "build":
            build.append(_add_relevance(node, "build/verification surface"))
        elif category == "boundary":
            reason = (
                "boundary/entrypoint (interaction_boost)"
                if interaction_relevant
                else "boundary/entrypoint"
            )
            boundaries.append(_add_relevance(node, reason))
        elif category == "interface":
            reason = (
                "interaction surface (interaction_boost)"
                if interaction_relevant
                else "interaction surface"
            )
            interfaces.append(_add_relevance(node, reason))
        else:
            primary.append(_add_relevance(node, "direct match"))

    # Also scan neighbors for doc/build/boundary/interface nodes not in
    # matches so the brief surfaces related authoritative context.
    match_ids = {m["id"] for m in matches}
    for node in neighbors:
        if node["id"] in match_ids:
            continue
        category = _classify_node(node)
        if category == "doc":
            docs.append(_add_relevance(node, "related doc/policy"))
        elif category == "build":
            build.append(
                _add_relevance(node, "related build/verification surface")
            )
        elif category == "boundary":
            boundaries.append(
                _add_relevance(node, "related boundary/entrypoint")
            )
        elif category == "interface":
            interfaces.append(
                _add_relevance(node, "related interaction surface")
            )

    # Sort all buckets. Interfaces and boundaries get the interaction boost
    # so they land in the order expected by agents reading the brief.
    def _key(node: dict) -> tuple[int, int, int, int, str]:
        return _sort_key(node, interaction_relevant=interaction_relevant)

    primary.sort(key=_key)
    interfaces.sort(key=_key)
    docs.sort(key=_key)
    build.sort(key=_key)
    boundaries.sort(key=_key)

    # Apply limits.
    primary = primary[:limit]
    interfaces = interfaces[:limit]
    docs = docs[:limit]
    build = build[:limit]
    boundaries = boundaries[:limit]

    # Build provenance.
    meta = graph.dump().get("meta", {})
    provenance = {
        "graph_sha": meta.get("git_sha"),
        "updated_at": meta.get("updated_at"),
    }

    # -- Interaction-retrieval warnings (tracked project) --
    # Emit freshness and partial-coverage warnings so consuming agents
    # can judge confidence in the interaction data.
    warnings.extend(check_freshness(graph))
    warnings.extend(check_partial_coverage(interfaces, boundaries))
    warnings.extend(check_confidence_gaps(interfaces + boundaries))

    # Envelope field order: when the query is interaction-relevant, emit
    # interfaces and boundaries before primary so agents consuming the
    # packet see the interaction slice first. The contract guarantees the
    # set of keys, not their order, but Python preserves insertion order
    # and agents often rely on it for readability.
    if interaction_relevant:
        envelope = {
            "brief_version": BRIEF_VERSION,
            "query": term,
            "interfaces": interfaces,
            "boundaries": boundaries,
            "primary": primary,
            "docs": docs,
            "build": build,
            "edges": edges,
            "provenance": provenance,
            "warnings": warnings,
        }
    else:
        envelope = {
            "brief_version": BRIEF_VERSION,
            "query": term,
            "primary": primary,
            "interfaces": interfaces,
            "docs": docs,
            "build": build,
            "boundaries": boundaries,
            "edges": edges,
            "provenance": provenance,
            "warnings": warnings,
        }
    if degraded_match is not None:
        envelope["degraded_match"] = degraded_match
    return envelope

def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``wd brief``."""
    parser = argparse.ArgumentParser(
        prog="wd brief",
        description="Agent-facing context briefing with stable JSON contract",
    )
    parser.add_argument(
        "term", help="Search term (same tokenization as query)"
    )
    parser.add_argument(
        "--root", type=Path, default=Path("."),
        help="Project root directory",
    )
    parser.add_argument(
        "--limit", type=int, default=20,
        help="Max nodes per section",
    )
    args = parser.parse_args(argv)

    from weld._graph_cli import _build_retry_hint, ensure_graph_exists
    from weld.graph import Graph

    # Surface a friendly first-run message when the graph has not been
    # built yet; mirrors the behaviour of read commands in _graph_cli
    # (tracked issue).
    ensure_graph_exists(args.root, _build_retry_hint("brief", args.term))
    g = Graph(args.root)
    g.load()
    result = brief(g, args.term, limit=args.limit)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
