"""ADR 0062: C++ amalgamation file rank boost.

Split out of :mod:`weld.ranking`, which the summary-only-match demotion
(bd ek4y, :mod:`weld._summary_match`) pushed over the 400-line cap.
Re-exported from :mod:`weld.ranking` so existing callers and tests keep
addressing it at that path -- the same shape
:mod:`weld._coverage_admission` already uses to re-export
:mod:`weld._rank_subject`.
"""

from __future__ import annotations


def is_amalgamation_file_node(node: dict) -> bool:
    """Return True when *node* is a C++ amalgamation file node.

    Per ADR 0062 the discovery side stamps ``props.amalgamation = True``
    on file nodes whose path matches a single-include / single-header
    convention. The ranker uses this signal as a coarse tiebreak so the
    "import surface" wins against same-score modular peers on
    single-token navigation queries.

    The check is intentionally narrow:

    * the node's ``type`` MUST be ``"file"`` (we never boost a symbol
      node, even if a downstream pass mistakenly inherits the marker);
    * ``props.amalgamation`` must be truthy.
    """
    if node.get("type") != "file":
        return False
    props = node.get("props") or {}
    return bool(props.get("amalgamation"))


def amalgamation_boost(node: dict, token_groups: list[list[str]]) -> int:
    """Return 0 for amalgamation files on a single-token query, else 1.

    Per ADR 0062 the boost only fires when:

    * the query is a single-token navigation query (one token group of
      length 1 -- multi-token queries already provide enough BM25
      signal that the boost would only swap deterministic ordering);
    * the node is a C++ amalgamation file node (see
      :func:`is_amalgamation_file_node`).

    A return value of 0 sorts ahead of 1, so the boost is additive
    inside the existing tiebreak chain in
    :func:`weld.ranking.rank_query_matches`.
    """
    if len(token_groups) != 1 or len(token_groups[0]) != 1:
        return 1
    if not is_amalgamation_file_node(node):
        return 1
    return 0


__all__ = ["amalgamation_boost", "is_amalgamation_file_node"]
