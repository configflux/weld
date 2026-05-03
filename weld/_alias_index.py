"""Alias-aware ID resolution for ADR 0041 lookup compatibility.

Background
----------
ADR 0041 renamed several node ID forms to a deterministic canonical
shape (see ``weld/_node_ids.py``). Discovery still records the
pre-rename form on the merged node's ``props.aliases`` list so MCP
transcripts, debugging notes, and external links pasted with the old
ID continue to resolve for one minor version.

This module owns the *lookup* side of that contract:

- :func:`build_alias_index` walks ``nodes`` once and returns an
  immutable ``alias -> canonical_id`` mapping.
- :func:`resolve_id` returns the canonical ID for either a canonical
  or alias query, or ``None`` when neither matches.

The pure-function shape lets callers (``graph_query``, ``graph_context``,
``mcp_server``) cache the index alongside the BM25 / inverted index
in ``_query_sidecar`` and invalidate it on the same graph-hash
boundary.

Security guard
--------------
The collision rule is the same as the one enforced at *write* time
in :func:`weld._graph_node_registry.ensure_node` (see ADR 0041
§ Migration aliases for the ID rename and the PR 2/4 follow-up brief):

  An alias must NEVER shadow a real canonical node ID.

If two source-of-truth invariants both hold (population guard +
lookup guard) the attack surface is symmetrically closed regardless
of which path a future caller takes. The lookup-side guard exists so
that a graph loaded from disk with a poisoned ``aliases`` list (e.g.,
from an older release that lacked the population-side guard, or from
an attacker who hand-edited ``graph.json``) cannot fool the resolver
into routing queries for canonical ID ``X`` to some other node ``Y``.

The guard is *silent* (warning + skip) at lookup time rather than
fatal, because:

1. The canonical-id table is the source of truth and remains intact.
2. Refusing to *load* a graph because of a bad alias would be a
   denial-of-service vector itself.
3. The skipped alias loses its lookup magic but the canonical node
   is still reachable by its real ID.

A poisoned alias that targets a *missing* canonical (no shadow) is
allowed -- it just becomes another alias that resolves to whatever
the alias index recorded first; the *first* writer wins. This
matches the discover-time order-independence guarantee in ADR 0041
because the alias list per node is sorted-deduped at write time.
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional

__all__ = ["build_alias_index", "resolve_id"]

_LOG = logging.getLogger(__name__)


def build_alias_index(nodes: Mapping[str, dict]) -> dict[str, str]:
    """Return an ``alias -> canonical_id`` dict built from ``nodes``.

    For each node ``N`` with ``props.aliases = [a1, a2, ...]`` write
    ``{a1: N.id, a2: N.id, ...}`` into the result.

    Skipped (with a warning) when the alias would shadow a canonical
    node id that already exists in ``nodes`` -- the canonical wins
    unconditionally. Also skipped when two different canonical nodes
    both claim the same alias; first writer wins (stable across
    discovery runs because the merged ``aliases`` list is sorted at
    write time).

    The function is total: malformed ``aliases`` entries (non-str,
    None, missing ``props``) are tolerated and skipped silently --
    a sidecar build must never fail because of one corrupt node.
    """
    index: dict[str, str] = {}
    for canonical_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        aliases = props.get("aliases")
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            if not isinstance(alias, str) or not alias:
                continue
            if alias == canonical_id:
                # Self-alias is a no-op; ``ensure_node`` already
                # collapses ``legacy_id == node_id`` at write time
                # but the lookup side stays defensive.
                continue
            if alias in nodes:
                _LOG.warning(
                    "alias %r on node %r would shadow canonical node %r; "
                    "skipping alias to preserve canonical lookup",
                    alias, canonical_id, alias,
                )
                continue
            existing = index.get(alias)
            if existing is not None and existing != canonical_id:
                _LOG.warning(
                    "alias %r already mapped to canonical %r; ignoring "
                    "duplicate claim from %r",
                    alias, existing, canonical_id,
                )
                continue
            index[alias] = canonical_id
    return index


def resolve_id(
    query_id: str,
    nodes: Mapping[str, dict],
    alias_index: Mapping[str, str],
) -> Optional[str]:
    """Return the canonical ID for ``query_id`` or ``None`` on miss.

    Resolution order:
      1. ``query_id`` is itself a canonical node id -> return it.
      2. ``query_id`` is registered in ``alias_index`` and the target
         canonical id still exists -> return the canonical id.
      3. Otherwise -> ``None``.

    Step 2's "target still exists" check defends against a stale
    ``alias_index`` whose canonical target was removed between index
    build and the lookup -- the resolver returns ``None`` rather than
    a dangling pointer.
    """
    if not isinstance(query_id, str) or not query_id:
        return None
    if query_id in nodes:
        return query_id
    canonical = alias_index.get(query_id)
    if canonical is not None and canonical in nodes:
        return canonical
    return None
