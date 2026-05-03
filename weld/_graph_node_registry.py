"""Unified ``ensure_node`` primitive for weld discovery (ADR 0041, Layer 2).

A single ``ensure_node`` function replaces the twelve ad-hoc
``_ensure_*`` helpers spread across ``agent_graph_materialize.py``,
``graph_closure.py``, and the ROS2 strategies. Strategies and
reference-creators call ``ensure_node`` instead of ``nodes.setdefault``
so that two writes for the same canonical ID merge deterministically
rather than silently dropping the second claim.

This PR ships the primitive plus its unit tests. The wiring into
``agent_graph_materialize`` lands in PR 2; the wiring into the ROS2,
gRPC, and tree-sitter clusters lands in PR 4. ``python_module`` and
``python_callgraph`` keep their existing ``nodes[id] = {...}`` pattern
in PR 1 because each file is processed exactly once per strategy and
no merge is required at that layer.

See ``docs/adrs/0041-graph-closure-determinism.md`` for the full merge
contract, the order-independence proof sketch, and the migration plan.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Optional

#: Authority levels for graph nodes (ADR 0021's vocabulary; ADR 0041
#: defines the precedence rule for merge). Higher index = higher
#: precedence.
_AUTHORITY_ORDER: tuple[str, ...] = ("referenced", "derived", "external", "canonical")
_AUTHORITY_RANK: Dict[str, int] = {
    name: rank for rank, name in enumerate(_AUTHORITY_ORDER)
}

#: Authority literal type alias for callers.
Authority = Literal["canonical", "external", "derived", "referenced"]

#: List-typed property fields that merge as sorted set-unions. The set
#: is taken from ADR 0041 § Layer 2; future fields can be added here
#: without changing call sites.
_LIST_UNION_FIELDS: frozenset[str] = frozenset(
    {
        "sources",
        "aliases",
        "roles",
        "imports_from",
        "exports",
        "tags",
        "path_globs",
        "applies_to",
        "constants",
    }
)


def _rank(authority: str) -> int:
    """Return the numeric rank for *authority*; unknown values rank lowest."""
    return _AUTHORITY_RANK.get(authority, -1)


def _max_authority(a: str, b: str) -> str:
    """Return the higher-ranked of two authority strings (``a`` wins ties)."""
    return a if _rank(a) >= _rank(b) else b


def _sorted_unique(items: Iterable[Any]) -> List[Any]:
    """Sort and deduplicate *items*, preserving the deterministic order."""
    return sorted({item for item in items if item is not None})


def _format_source(strategy: str, path: Optional[str]) -> str:
    """Compose the ``strategy:path`` provenance string used by ``sources``."""
    return f"{strategy}:{path}" if path else strategy


def _merge_scalar(
    existing: Any,
    incoming: Any,
    *,
    existing_authority_rank: int,
    incoming_authority_rank: int,
) -> Any:
    """Merge two scalar values using ADR 0041's precedence rules.

    Higher authority always wins. Equal authority falls back to the
    lexicographic minimum, which is the only choice that is associative
    and commutative across an arbitrary number of equal-authority
    claims (and therefore the only choice that preserves
    determinism).
    """
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    if existing == incoming:
        return existing
    if incoming_authority_rank > existing_authority_rank:
        return incoming
    if existing_authority_rank > incoming_authority_rank:
        return existing
    # Equal authority -- compare values. Sorting on str(...) keeps the
    # rule total even when the two sides have different types.
    return min(existing, incoming, key=lambda v: (str(type(v)), str(v)))


def _merge_props(
    existing: Dict[str, Any],
    incoming: Dict[str, Any],
    *,
    existing_authority_rank: int,
    incoming_authority_rank: int,
) -> Dict[str, Any]:
    """Recursively merge two ``props`` dicts under ADR 0041's rules.

    - List-typed fields in :data:`_LIST_UNION_FIELDS` (or any list-typed
      field) merge as sorted set-unions.
    - Dict-typed fields recurse with the same authority context.
    - Scalar fields merge via :func:`_merge_scalar`.
    """
    keys = set(existing.keys()) | set(incoming.keys())
    merged: Dict[str, Any] = {}
    for key in keys:
        if key in existing and key not in incoming:
            merged[key] = existing[key]
            continue
        if key in incoming and key not in existing:
            merged[key] = incoming[key]
            continue
        ev = existing[key]
        iv = incoming[key]
        if isinstance(ev, list) or isinstance(iv, list):
            ev_list = ev if isinstance(ev, list) else [ev]
            iv_list = iv if isinstance(iv, list) else [iv]
            merged[key] = _sorted_unique(ev_list + iv_list)
            continue
        if isinstance(ev, dict) and isinstance(iv, dict):
            merged[key] = _merge_props(
                ev,
                iv,
                existing_authority_rank=existing_authority_rank,
                incoming_authority_rank=incoming_authority_rank,
            )
            continue
        merged[key] = _merge_scalar(
            ev,
            iv,
            existing_authority_rank=existing_authority_rank,
            incoming_authority_rank=incoming_authority_rank,
        )
    return merged


def ensure_node(
    nodes: Dict[str, Dict[str, Any]],
    node_id: str,
    node_type: str,
    *,
    source_strategy: str,
    source_path: Optional[str],
    authority: Authority,
    props: Optional[Dict[str, Any]] = None,
    legacy_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert or merge *node_id* into *nodes* per ADR 0041.

    Insert: when *node_id* is absent, a new node dict is created with
    ``type``, ``label`` (defaulting to ``props["name"]`` or the trailing
    segment of *node_id*), and a ``props`` dict that records the
    incoming claim's ``authority``, ``source_strategy``, and a one-entry
    ``sources`` list. ``aliases`` defaults to an empty sorted list.

    Merge: when *node_id* exists, the function reconciles the existing
    node and the incoming claim. Authority is the maximum of the two
    sides under :data:`_AUTHORITY_ORDER`. List-typed fields take a
    sorted set-union; dict-typed fields deep-merge with the same
    rules; scalar fields obey authority precedence with a
    lexicographic-min tie-break.

    The merge is provably commutative, associative, and idempotent (see
    ADR 0041 § Layer 2 proof sketch), so the final state of
    ``nodes[node_id]`` depends only on the *set* of ``ensure_node``
    calls, not their order.

    *legacy_id* (optional) records the pre-ADR-0041 form of *node_id*
    on the merged node's ``aliases`` list (sorted, deduped) so the
    alias-aware lookup in :mod:`weld.graph_query` and
    :mod:`weld.graph_context` can resolve old IDs transparently for one
    minor version. Two safety rules apply:

    1. *legacy_id* is silently ignored when it equals *node_id* — the
       canonical form did not actually change for this entity, so there
       is nothing to alias.
    2. *legacy_id* is rejected (``ValueError``) when it equals any
       *other* canonical key already present in *nodes*. Allowing an
       attacker-controlled ``legacy_id`` to shadow an unrelated
       canonical node would let strategy inputs steal inbound edges via
       alias resolution. This guard fires deterministically regardless
       of strategy ordering.

    Returns the merged node dict (also stored in ``nodes`` in place).
    """
    incoming_props = dict(props) if props else {}
    incoming_props.setdefault("authority", authority)
    incoming_props.setdefault("source_strategy", source_strategy)
    incoming_props.setdefault("sources", [_format_source(source_strategy, source_path)])
    incoming_props.setdefault("aliases", [])
    if legacy_id and legacy_id != node_id:
        if legacy_id in nodes:
            # An attacker-controlled legacy_id must never shadow an
            # unrelated canonical node. The canonical key wins
            # unconditionally; the caller must not have supplied this
            # legacy form for this entity.
            raise ValueError(
                f"legacy_id {legacy_id!r} collides with an existing "
                f"canonical node id; refusing to alias into it"
            )
        existing_aliases = list(incoming_props.get("aliases") or [])
        if legacy_id not in existing_aliases:
            existing_aliases.append(legacy_id)
        incoming_props["aliases"] = existing_aliases

    if node_id not in nodes:
        label = incoming_props.get("name") or node_id.rsplit(":", 1)[-1]
        nodes[node_id] = {
            "type": node_type,
            "label": str(label),
            "props": {
                **incoming_props,
                "sources": _sorted_unique(incoming_props["sources"]),
                "aliases": _sorted_unique(incoming_props["aliases"]),
            },
        }
        return nodes[node_id]

    existing = nodes[node_id]
    existing_props = existing.get("props", {}) or {}

    existing_authority = existing_props.get("authority", "referenced")
    incoming_authority = authority
    merged_authority = _max_authority(existing_authority, incoming_authority)

    existing_rank = _rank(existing_authority)
    incoming_rank = _rank(incoming_authority)

    merged_props = _merge_props(
        existing_props,
        incoming_props,
        existing_authority_rank=existing_rank,
        incoming_authority_rank=incoming_rank,
    )
    merged_props["authority"] = merged_authority

    # Type and label resolve under the same rule as scalar props, so
    # the higher-authority side wins.
    merged_type = _merge_scalar(
        existing.get("type"),
        node_type,
        existing_authority_rank=existing_rank,
        incoming_authority_rank=incoming_rank,
    )
    incoming_label = incoming_props.get("name") or node_id.rsplit(":", 1)[-1]
    merged_label = _merge_scalar(
        existing.get("label"),
        str(incoming_label),
        existing_authority_rank=existing_rank,
        incoming_authority_rank=incoming_rank,
    )

    nodes[node_id] = {
        "type": merged_type,
        "label": merged_label,
        "props": merged_props,
    }
    return nodes[node_id]


__all__ = [
    "Authority",
    "ensure_node",
]
