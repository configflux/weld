"""Canonical origin classifier for graph nodes (ADR 0042).

A single ``classify_node`` predicate maps every ``symbol`` / ``file`` /
``module`` node to exactly one of four origin values: ``project``,
``stdlib``, ``external``, or ``unresolved``. Strategies that have
shipped per-language origin tagging set ``props.origin`` directly;
``classify_node`` reads that field when present and falls back to a
deterministic derivation from existing signals (``authority``,
``resolved``, the ``symbol:unresolved:`` ID prefix, and edge-side
``props.resolution``) for legacy graphs.

The fallback is a transitional path; once every strategy emits
``props.origin``, the derivation may be removed. The function is pure:
no I/O, no graph traversal, no logging.

See ``docs/adrs/0042-graph-node-origin.md`` for the full taxonomy,
per-language detection rules, and the legacy-fallback pseudocode this
module implements.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Literal, Optional

#: Origin literal type alias for callers (ADR 0042 §Decision).
Origin = Literal["project", "stdlib", "external", "unresolved"]

#: The exhaustive, mutually exclusive set of origin values. Adding a
#: fifth value requires amending ADR 0042.
ORIGINS: tuple[Origin, ...] = ("project", "stdlib", "external", "unresolved")

_ORIGIN_SET: frozenset[str] = frozenset(ORIGINS)


def classify_node(
    node: Dict[str, Any],
    *,
    incoming_edges: Optional[Iterable[Dict[str, Any]]] = None,
) -> Origin:
    """Return the origin of *node* per ADR 0042.

    Reads ``node["props"]["origin"]`` directly when present and valid
    (post-ADR-0042 strategies). Falls back to the deterministic legacy
    derivation otherwise. The function is total: every input produces
    exactly one of the four :data:`ORIGINS` values.

    *incoming_edges* is consulted only by the legacy fallback's two
    ``props.resolution`` checks; modern nodes never need it. ``None``
    and an empty iterable are treated identically.
    """
    props = node.get("props") or {}

    explicit = props.get("origin")
    if isinstance(explicit, str) and explicit in _ORIGIN_SET:
        return explicit  # type: ignore[return-value]

    node_id = node.get("id", "")
    if isinstance(node_id, str) and node_id.startswith("symbol:unresolved:"):
        return "unresolved"

    if props.get("resolved") is False:
        return "unresolved"

    if incoming_edges is not None:
        edges = list(incoming_edges)
        for edge in edges:
            edge_props = edge.get("props") or {}
            if edge_props.get("resolution") == "builtin":
                return "stdlib"
        for edge in edges:
            edge_props = edge.get("props") or {}
            if edge_props.get("resolution") == "stdlib":
                return "stdlib"

    if props.get("authority") == "external":
        return "external"

    return "project"


__all__ = [
    "ORIGINS",
    "Origin",
    "classify_node",
]
