"""The one enumeration of what a query token may match a node on (bd ph1g).

A node's queryable surface -- which props a substring test is allowed to look
at -- used to be written out **five** times: the strict-AND matcher for each of
the three query impls (:mod:`weld._graph_match`, :mod:`weld._sqlite_query`,
:mod:`weld._federation_eager_index`) and both counters in
:mod:`weld._coverage_admission`. A sixth list, the inverted index in
:mod:`weld.query_index`, decides which nodes are even offered to those five.

Every one of those copies carried a comment asking the next author to keep it in
step, and the copies had drifted anyway -- each drift discovered as a *ranking*
bug, because that is how this failure presents:

* ``props.headings`` reached the eager-federation matcher only at 8rm0.4, after
  it had been silently disagreeing with the lazy path on heading-only doc
  matches;
* ``props.keywords`` -- the channel ADR 0105 added precisely so a strategy could
  make a fact queryable -- reached one matcher of the five, so a build target
  was matchable through the JSON path and not through sqlite or federation;
* ``props.summary`` (bd ph1g) landed in the index first, which made the
  serializer a *candidate* for ``graph.json`` that every matcher then rejected.

The last one is the shape that names the cost. A field wired into the index but
not the match surface does not fail loudly: the node is retrieved, tested,
dropped, and the query returns whatever else it found. Nothing is missing, so
nothing looks broken.

So the enumeration lives here, once. Adding a channel is one edit to
:data:`_HAYSTACK_LIST_PROPS` (or one line in :func:`match_haystacks`) plus its
peer in ``query_index.node_tokens``, and all five callers gain it together.

The one deliberate difference
-----------------------------
``props.constants`` is excluded from the OR-fallback counter, and only there.
That is ADR 0075's decision, not drift: the relaxation tier keeps the field set
the OR fallback shipped with so relaxing a query cannot silently re-rank results
that never went through it. It is expressed as an argument rather than a sixth
copy, so it stays visible as a decision.
"""

from __future__ import annotations

#: List-valued props whose every element is a haystack. These are "bag" fields
#: (ADR 0075's term): a hit says the token was mentioned somewhere in a
#: collection, not that it names the node. ``constants`` is here but is dropped
#: by ``include_constants=False`` for the OR-fallback counter.
_HAYSTACK_LIST_PROPS = ("exports", "constants", "headings", "keywords")


def match_haystacks(
    nid: str, node: dict, *, include_constants: bool = True,
) -> list[str]:
    """Return every lowercased string a query token may be tested against.

    Order is irrelevant -- callers ask "is this token a substring of any of
    these?" -- so this is a flat list rather than a per-field mapping, which is
    what lets a new channel be added without touching a single caller.

    Non-string entries in a list-valued prop are skipped rather than coerced: a
    strategy that files an ``int`` in a bag field has a bug, and matching
    ``"7"`` against it would hide that instead of ignoring it. ``qualname`` is
    the one scalar that IS coerced, preserving the long-standing ``str()`` call
    on it in every copy this replaces.
    """
    props = node.get("props") or {}
    haystacks = [
        nid.lower(),
        node.get("label", "").lower(),
        (props.get("file") or "").lower(),
        str(props.get("qualname") or "").lower(),
        (props.get("description") or "").lower(),
        # bd ph1g: the module's own opening docstring line. Structural input
        # read from source every discover, unlike ``description`` above it,
        # which is enrichment output.
        str(props.get("summary") or "").lower(),
    ]
    for prop_name in _HAYSTACK_LIST_PROPS:
        if prop_name == "constants" and not include_constants:
            continue
        haystacks.extend(
            value.lower()
            for value in props.get(prop_name, [])
            if isinstance(value, str)
        )
    return haystacks


def count_group_hits(
    token_groups: list[list[str]],
    nid: str,
    node: dict,
    *,
    short_circuit: bool,
    include_constants: bool = True,
) -> int:
    """Count how many of *token_groups* hit *node* (OR within, AND across).

    *short_circuit* is the whole difference between the two shapes this
    replaces. ``True`` is the strict-AND matcher: a group that hits nothing
    means the node does not match at all, so it returns ``0`` immediately and
    the caller reads any nonzero result as "matched". ``False`` is the counting
    form the admission and OR-fallback tiers need: they rank by *how many*
    concepts a node covered, so a partial hit is an answer rather than a
    rejection.

    Both forms agree on the field set by construction, which is the point --
    ADR 0075 requires admission and strict-AND to mean the same thing by
    "covered", and the three query impls have to mean the same thing by
    "matched" or one graph answers one query three ways (bd cgj3).
    """
    haystacks = match_haystacks(nid, node, include_constants=include_constants)
    hits = 0
    for group in token_groups:
        if any(token in haystack for token in group for haystack in haystacks):
            hits += 1
        elif short_circuit:
            return 0
    return hits


__all__ = ["count_group_hits", "match_haystacks"]
