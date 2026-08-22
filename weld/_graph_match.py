"""Node-level matching primitives for the graph read path.

Carved out of :mod:`weld.graph`, which the ADR 0105 ``props.keywords``
channel pushed to exactly 400 lines -- compliant with the line-count policy
but with zero headroom, so the next edit to that module would have failed
``tools/lint_line_counts.py`` on somebody else's change, and the cheapest
local fix (delete a comment) is worse than the right one (bd jkir).

These three functions are what came out, because they are the part of
``Graph`` that never touches ``self``: two pure token matchers and a bare-name
resolver that only reads a node mapping. They sit beside
:mod:`weld.graph_query` (the query dispatcher) for the same reason it does --
the dependency runs one way, from ``Graph`` into here, and nothing here
imports ``Graph``.

``Graph`` keeps ``_match_tokens`` / ``_match_token_groups`` as thin
staticmethod delegates: :mod:`weld.graph_query`, :mod:`weld.federation` and
four test modules address the matchers through the class, and turning a pure
refactor into a rename of a surface four tests pin is how a refactor acquires
a blast radius it did not need.
"""

from __future__ import annotations

from weld._match_surface import count_group_hits


def match_token_groups(
    token_groups: list[list[str]], nid: str, node: dict,
) -> int:
    """Match synonym-expanded token groups; 0 if any group misses.

    Every group must hit at least one field (strict AND across groups, OR
    within a group). The queryable field set itself lives in
    :mod:`weld._match_surface` -- it used to be written out here and in four
    other matchers, and a channel that reached only some of them made a node a
    candidate that then failed to match, which presents as a ranking bug rather
    than a missing channel. ``weld/tests/weld_keywords_query_channel_test.py``
    and ``weld/tests/weld_match_surface_test.py`` are the anti-drift pins.
    """
    return count_group_hits(token_groups, nid, node, short_circuit=True)


def match_tokens(tokens: list[str], nid: str, node: dict) -> int:
    """Count matched tokens; returns 0 if any token misses all fields.

    The degenerate case of :func:`match_token_groups` -- one token per group,
    so no synonym expansion. Tokens are expected pre-lowered; ``Graph.query``
    lowers them.
    """
    return match_token_groups([[t] for t in tokens], nid, node)


def resolve_symbol_name(nodes: dict[str, dict], symbol_name: str) -> list[dict]:
    """Resolve a bare symbol *name* against *nodes*.

    Matches symbol nodes on ``props.qualname`` (with a trailing ``.<name>``
    suffix check) and additionally includes the ``symbol:unresolved:<name>``
    sentinel. Shared by ``Graph.callers`` and ``Graph.references`` so both use
    one bare-name rule.
    """
    matches: list[dict] = []
    for nid, n in nodes.items():
        if n.get("type") != "symbol":
            continue
        qual = (n.get("props") or {}).get("qualname") or n.get("label", "")
        if qual == symbol_name or qual.endswith("." + symbol_name):
            matches.append({"id": nid, **n})
        elif nid == f"symbol:unresolved:{symbol_name}":
            matches.append({"id": nid, **n})
    return matches


__all__ = ["match_token_groups", "match_tokens", "resolve_symbol_name"]
