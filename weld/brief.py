"""Agent-facing briefing surface for the connected structure.

Returns a compact, LLM-friendly context packet instead of making agents
assemble low-level queries manually. Ranks authoritative, high-confidence,
and interaction-relevant context ahead of generic matches.

"""

from __future__ import annotations

from typing import Any

from weld._brief_rank import (
    add_relevance as _add_relevance,
)
from weld._brief_rank import (
    classify_node as _classify_node,
)
from weld._brief_rank import (
    primary_relevance as _primary_relevance,
)
from weld._brief_rank import (
    query_is_interaction_relevant as _query_is_interaction_relevant,
)
from weld._brief_rank import (
    sort_key as _sort_key,
)
from weld.synonyms import expand_token_groups
from weld.warnings import check_confidence_gaps, check_freshness, check_partial_coverage

# -- Stable JSON output contract -------------------------------------------
#
# The brief output is a versioned JSON envelope. v2 adds the ``interfaces``
# bucket and interaction-aware ranking per ADR 0086 and tracked project. Keys:
# ``brief_version``, ``query``, ``primary`` (implementation/domain),
# ``interfaces`` (rpc/channel/ros_* protocol surfaces), ``docs``, ``build``,
# ``boundaries``, ``edges``, ``provenance`` (``graph_sha`` / ``updated_at``),
# ``warnings``.
#
# Contract rules:
#   - brief_version is 2 (bumped from 1; bump again on breaking changes).
#   - All list fields default to [] (never null/absent).
#   - provenance is always present (fields may be null if unavailable).
#   - warnings is always present (empty list means no issues).
#   - Node entries include a discriminating ``relevance`` field: ``exact
#     match`` (identifier equals the query), ``token match`` (other direct
#     hit), the interaction/doc/build reasons below, or ``related ...`` for
#     neighbours -- so callers can re-rank without re-querying (Finding 08).
#   - Exact-identifier matches sort to the top of their bucket (the same
#     ``exact_symbol_match_rank`` preference ``Graph.query`` applies).
#   - When the query is interaction-relevant, interfaces and boundaries are
#     emitted before generic primary in the envelope field order, and each
#     interaction node carries ``interaction_boost`` in its ``relevance``
#     text so agents can see why it ranked first.

BRIEF_VERSION: int = 2

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

    # Token groups, derived the same way ``Graph.query`` derives them, so the
    # exact-identifier preference (``exact_symbol_match_rank``) agrees with
    # query on which node the caller named (Finding 08). Empty query -> empty
    # groups, which leaves the exact boost inert.
    token_groups = expand_token_groups(term.lower().split())

    # Run the same tokenized query as ``wd query``.
    # ``Graph.query`` itself performs an OR-fallback when strict-AND
    # zeroes on a multi-token query, tagging the envelope with
    # ``degraded_match: 'or_fallback'``. Brief surfaces that flag and
    # adds the user-facing warning copy; if the query layer added no
    # flag (single-token or AND-success), no fallback is needed here.
    query_result = graph.query(term, limit=limit * 3)  # over-fetch
    matches = query_result.get("matches", [])
    neighbors = query_result.get("neighbors", [])
    edges = query_result.get("edges", [])

    if query_result.get("degraded_match") == "or_fallback":
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
            primary.append(
                _add_relevance(node, _primary_relevance(node, token_groups))
            )

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
    def _key(node: dict) -> tuple[int, int, int, int, int, str]:
        return _sort_key(
            node,
            interaction_relevant=interaction_relevant,
            token_groups=token_groups,
        )

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

# ``wd brief`` CLI wiring (argparse + federation-aware graph loader) lives in
# :mod:`weld._brief_cli`; re-exported here so ``from weld.brief import main``
# and the ``weld.cli`` dispatch stay unchanged after the split (400-line cap).
from weld._brief_cli import _load_brief_graph, main  # noqa: E402,F401
