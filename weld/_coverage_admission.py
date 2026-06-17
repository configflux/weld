"""Coverage-aware admission and diffuse-doc demotion helpers (ADR 0075).

Pure, dependency-free helpers for the JSON-``Graph`` read path (impl #1). Split
out of :mod:`weld.graph_query` to keep that module under the line-count cap; the
logic is unchanged. Both the admission relaxation and the diffuse-doc demotion
are gated to N>=3 (the load-bearing dual gate): below it ``max(2, N-1) == N ==
full coverage`` so admission admits nothing strict-AND would not, and demotion
must NOT run (it could reorder a future N<=2 doc-vs-code result a golden
depends on).

The match surface here is identical to :meth:`weld.graph.Graph._match_token_groups`
(ADR 0075 requires admission and strict-AND to agree on what "covered" means).
"""

from __future__ import annotations

from typing import Callable

# ADR 0075: the coverage-aware admission relaxation and the diffuse-doc
# demotion BOTH fire only at this token-group count (the load-bearing dual
# gate). Below it, ``max(2, N-1) == N == full coverage`` so admission is inert
# and demotion must NOT run (it could reorder a future N<=2 doc-vs-code golden).
_COVERAGE_MIN_GROUPS = 3

# Diffuse-doc discriminator bag fields (ADR 0075 "Soundness review notes").
# Bag fields are the collection fields whose hits, on their own, signal a
# scattered mention. ``props.exports`` is intentionally excluded: it is
# effectively never populated on a ``type=doc`` node, so including it would
# only add surface area (revisit if a future doc strategy ever populates it).
_DIFFUSE_BAG_FIELDS = ("headings", "constants")


def covered_group_count(
    token_groups: list[list[str]], nid: str, node: dict
) -> int:
    """Count how many ``token_groups`` are hit, WITHOUT short-circuiting.

    The match surface is identical to :meth:`Graph._match_token_groups`
    (admission and strict-AND must agree on "covered"), so this includes
    ``props.constants`` -- unlike the OR-fallback counter
    :func:`weld.graph_query._count_groups_hit`, whose pre-existing
    ``constants`` omission ADR 0075 deliberately leaves unchanged.
    """
    nid_l, label_l = nid.lower(), node.get("label", "").lower()
    props = node.get("props") or {}
    file_l = (props.get("file") or "").lower()
    qualname_l = str(props.get("qualname") or "").lower()
    exports_l = [e.lower() for e in props.get("exports", []) if isinstance(e, str)]
    constants_l = [c.lower() for c in props.get("constants", []) if isinstance(c, str)]
    headings_l = [h.lower() for h in props.get("headings", []) if isinstance(h, str)]
    desc_l = (props.get("description") or "").lower()
    hits = 0
    for group in token_groups:
        if any(
            t in nid_l or t in label_l or t in file_l
            or t in qualname_l or t in desc_l
            or any(t in e for e in exports_l)
            or any(t in c for c in constants_l)
            or any(t in h for h in headings_l)
            for t in group
        ):
            hits += 1
    return hits


def count_groups_hit(
    token_groups: list[list[str]], nid: str, node: dict
) -> int:
    """Count how many ``token_groups`` are hit by the OR-fallback surface.

    The single, shared definition of the OR-fallback group-hit counter used by
    BOTH OR-fallback impls (the JSON ``Graph`` read path and its sqlite peer),
    so the two cannot drift on what "hit a group" means. Unlike
    :func:`covered_group_count` (the *admission*/strict-AND counter, identical
    to :meth:`Graph._match_token_groups`), this surface intentionally OMITS
    ``props.constants``: that is the pre-existing OR-fallback field set, and the
    relaxation tier deliberately retains it so the OR fallback never *under*
    counts a node strict-AND would have hit while keeping the existing
    OR-fallback behavior unchanged. Like :func:`covered_group_count`, it does
    NOT short-circuit on a missing group -- it counts partial hits so callers
    can rank by ``num_groups_hit_desc``.
    """
    nid_l = nid.lower()
    label_l = node.get("label", "").lower()
    props = node.get("props") or {}
    file_l = (props.get("file") or "").lower()
    qualname_l = str(props.get("qualname") or "").lower()
    exports_l = [e.lower() for e in props.get("exports", []) if isinstance(e, str)]
    headings_l = [h.lower() for h in props.get("headings", []) if isinstance(h, str)]
    desc_l = (props.get("description") or "").lower()
    hits = 0
    for group in token_groups:
        if any(
            t in nid_l or t in label_l or t in file_l
            or t in qualname_l or t in desc_l
            or any(t in e for e in exports_l)
            or any(t in h for h in headings_l)
            for t in group
        ):
            hits += 1
    return hits


def _group_hits_string(group: list[str], value: str) -> bool:
    """Return True if any synonym ``t`` in *group* is a substring of *value*."""
    return any(t in value for t in group)


def _identity_values(nid: str, node: dict) -> list[str]:
    """Return the lowered identity-field strings for *node* (ADR 0075).

    Identity fields are the ones that make a node *about* a concept (as
    opposed to merely mentioning it): the node id, ``label``, ``props.file``,
    ``props.qualname`` and ``props.description``. This is the exact set the
    diffuse-doc discriminator (:func:`is_diffuse_doc`) treats as "identity";
    sharing one extractor keeps the two ADR 0075 demotion signals from
    drifting on what "identity" means. Bag fields (``headings`` / ``constants``
    / ``exports``) are intentionally excluded -- a hit there is a scattered
    mention, not an identity.
    """
    props = node.get("props") or {}
    return [
        nid.lower(),
        node.get("label", "").lower(),
        (props.get("file") or "").lower(),
        str(props.get("qualname") or "").lower(),
        (props.get("description") or "").lower(),
    ]


def _subject_in_identity(
    nid: str, node: dict, token_groups: list[list[str]]
) -> bool:
    """Return True when the query *subject* lands in an identity field.

    The subject is the **leading** token-group (``token_groups[0]``); a node
    that carries it in an identity field (see :func:`_identity_values`) is
    *about* the subject, not merely a co-mention. Shared core for the two
    subject tie-breaks (``partial_coverage_subject_miss`` in the admission tier
    and ``subject_identity_miss`` in the OR-fallback tier) so they cannot drift
    on what "covers the subject" means. Empty ``token_groups`` -> True (no
    subject to miss, so never a penalty).
    """
    if not token_groups:
        return True
    subject_group = token_groups[0]
    identity = _identity_values(nid, node)
    return any(_group_hits_string(subject_group, value) for value in identity)


def partial_coverage_subject_miss(
    node: dict, token_groups: list[list[str]]
) -> int:
    """Return 1 when a coverage-admitted node misses the query *subject* (ADR 0075).

    A tie-break dimension for the bounded-coverage admission tier (the
    strict-AND + admission path in :func:`weld.graph_query.query_graph` and its
    sqlite / federation parity peers). Multiple nodes admitted by
    :func:`coverage_admissions` can share the same ``covered_group_count`` while
    covering *different* group subsets -- e.g. for
    ``"typescript discovery strategy"`` (N=3) both
    ``weld/strategies/typescript_exports`` (covers ``{typescript, strategy}``)
    and ``weld/discovery_state`` (covers ``{discovery, strategy}``) land at
    coverage 2. The pre-existing tie-break was pure BM25 ``-score``, which
    rewards whichever node carries the rarest matched token at high term
    frequency -- here ``discovery`` (higher IDF than ``typescript`` in this
    corpus), so the ``discovery_state`` node (which contains no ``typescript``
    at all) outranked the actual TypeScript strategy modules. IDF rarity is
    not query-subject relevance for an entity-shaped navigation query.

    The return is ``1`` (sorts later) only when ALL of:

    * the node is a bounded-coverage admission (internal tag
      ``partial_coverage`` truthy) -- strict-AND full-coverage matches and
      diffuse docs are untouched, so the existing N>=3 strict-AND goldens do
      not move;
    * the query has ``>= _COVERAGE_MIN_GROUPS`` token groups (the same N>=3
      dual gate admission/demotion already use -- below it the admission tier
      is empty, so this dimension is inert);
    * the subject (leading token-group) is absent from every identity field.

    Otherwise it returns ``0`` (no penalty). Placed ahead of ``-score`` in the
    shared ranking sort key so it survives near-tie BM25 noise, and after the
    ``_diffuse`` dimension so diffuse-doc demotion still wins. Pure re-rank:
    nothing is excluded.
    """
    if not node.get("partial_coverage"):
        return 0
    if len(token_groups) < _COVERAGE_MIN_GROUPS:
        return 0
    return 0 if _subject_in_identity(node.get("id", ""), node, token_groups) else 1


def subject_identity_miss(
    nid: str, node: dict, token_groups: list[list[str]]
) -> int:
    """Return 1 when an OR-fallback candidate misses the query *subject*.

    The OR-fallback tier (:func:`weld.graph_query.query_or_fallback`) is the
    *degraded* retrieval path: it fires only when strict-AND yields zero
    matches on a multi-token query, and ranks the per-group union by
    ``(group_hits_desc, BM25_desc, id)``. Without a subject signal it suffers
    the same defect as the admission tier -- for ``"typescript discovery
    strategy"`` on a graph with no node covering all three groups (the durable,
    clean-graph case once the transient ``concept:<issue>`` node is gone),
    ``discovery_state`` (group_hits 2, no ``typescript``) outranks the
    TypeScript strategy modules (group_hits 2, with ``typescript``) purely on
    BM25 IDF rarity.

    This restores intent by sorting any union candidate that does NOT carry the
    subject (leading token-group) in an identity field *after* its group-hit
    peers that do. Gated to multi-token queries (``len(token_groups) >= 2``);
    single-token queries never reach OR-fallback and have no distinct subject.
    Returns ``1`` (sorts later) when the subject is absent from every identity
    field, else ``0``. Placed ahead of ``-bm25`` and after ``-group_hits`` in
    the OR-fallback key, so a node hitting strictly more groups still wins. Pure
    re-rank: nothing is excluded.
    """
    if len(token_groups) < 2:
        return 0
    return 0 if _subject_in_identity(nid, node, token_groups) else 1


def or_fallback_sort_key(
    nid: str,
    node: dict,
    group_hits: int,
    token_groups: list[list[str]],
    bm25_score: float,
) -> tuple[int, int, float, str]:
    """Return the shared OR-fallback ranking key for one union candidate.

    The single, shared definition of the OR-fallback sort order used by BOTH
    OR-fallback impls (the JSON ``Graph`` read path and its sqlite peer), so the
    two cannot drift on rank. The dimensions, in order:

    1. ``-group_hits`` -- a node hitting more query groups wins;
    2. :func:`subject_identity_miss` -- among group-hit-tied candidates, one
       that misses the query *subject* (leading token-group) in every identity
       field sorts last (so it does not beat a subject-bearing peer on BM25 IDF
       rarity alone). Inert for single-token queries / when the subject is
       present;
    3. ``-bm25_score`` -- the caller-supplied BM25 score (each impl computes it
       with its own corpus accessor; placed after the subject dimension so the
       latter survives near-tie BM25 noise);
    4. ``nid`` -- a stable, deterministic final tiebreak.

    *bm25_score* is passed in (not computed here) so this helper stays free of
    any corpus/backend dependency: the JSON path supplies
    ``Graph._bm25.score(...)`` and the sqlite path supplies its lazy-from-sqlite
    BM25 score, but both rank identically.
    """
    return (
        -group_hits,
        subject_identity_miss(nid, node, token_groups),
        -bm25_score,
        nid,
    )


def is_diffuse_doc(nid: str, node: dict, token_groups: list[list[str]]) -> bool:
    """Return True when a ``type=doc`` node's coverage is *diffuse* (ADR 0075).

    Diffuse == the conjunction of two conditions, both required:

    * **(a) Bag-only coverage.** Every query group's hit lands *only* in a bag
      field (``props.headings`` / ``props.constants``) and *no* group is hit by
      an identity field (``nid``, ``label``, ``props.file``, ``props.qualname``,
      ``props.description``). Any identity-field hit makes the doc *about* the
      concept -> not diffuse. ``nid`` is included because
      :meth:`Graph._match_token_groups` matches on it; a doc whose id carries a
      query group is named after the concept and must not be demoted.
    * **(b) No co-locating string.** No single bag string (one heading, one
      constant) contains ``>=2`` query groups. A heading like "Boundary
      entrypoint strategy" that carries two groups means the doc is genuinely
      about the concept -> not diffuse.

    This is called only on full-coverage docs (so "no identity hit" implies the
    bag fields cover all groups). Keeping the query-group logic here keeps the
    ranker free of token-group concerns; the caller pre-tags matches and
    :func:`rank_query_matches` only reads the resulting boolean.
    """
    if node.get("type") != "doc":
        return False
    props = node.get("props") or {}
    identity_values = _identity_values(nid, node)
    bag_strings: list[str] = []
    for field in _DIFFUSE_BAG_FIELDS:
        bag_strings.extend(
            s.lower() for s in props.get(field, []) if isinstance(s, str)
        )
    # Condition (a): no group may be carried by an identity field.
    for group in token_groups:
        if any(_group_hits_string(group, v) for v in identity_values):
            return False
    # Condition (b): no single bag string may carry two or more groups.
    for s in bag_strings:
        groups_in_string = sum(
            1 for group in token_groups if _group_hits_string(group, s)
        )
        if groups_in_string >= 2:
            return False
    return True


def tag_match(nid: str, node: dict, token_groups: list[list[str]]) -> dict:
    """Return a shallow copy of *node* tagged with the diffuse flag if needed.

    Strict-AND matches are full-coverage; only ``type=doc`` nodes can be
    diffuse. The diffuse demotion is gated to N>=3 (the dual gate), so the tag
    is set only then. Copying avoids mutating the shared in-memory graph node.
    """
    if (
        len(token_groups) >= _COVERAGE_MIN_GROUPS
        and is_diffuse_doc(nid, node, token_groups)
    ):
        tagged = dict(node)
        tagged["_diffuse"] = True
        return tagged
    return node


def coverage_admissions(
    nodes: dict[str, dict],
    union: set[str] | None,
    token_groups: list[list[str]],
    already_matched: set[str],
) -> list[tuple[str, dict]]:
    """Return extra (non-doc) nodes admitted by bounded coverage (ADR 0075).

    Fires only for N>=3 (the dual gate). Scans the per-group *union* candidate
    set the caller computed (the same surface the OR-fallback uses, so a 3/4
    node the strict-AND *intersection* dropped is reachable), or all *nodes*
    when ``union`` is ``None`` (empty index). Admits every ``type!=doc`` node
    whose covered-group count is ``>= max(2, N-1)`` and that strict-AND did not
    already admit. Admitted nodes are shallow-copied and tagged
    ``partial_coverage=True``. Docs are never admitted by this path (they still
    require full coverage via strict-AND).
    """
    n = len(token_groups)
    if n < _COVERAGE_MIN_GROUPS:
        return []
    threshold = max(2, n - 1)
    if union is None:
        candidate_iter = nodes.items()
    else:
        candidate_iter = ((nid, nodes[nid]) for nid in union if nid in nodes)
    admitted: list[tuple[str, dict]] = []
    for nid, node in candidate_iter:
        if nid in already_matched:
            continue
        if node.get("type") == "doc":
            continue
        if covered_group_count(token_groups, nid, node) >= threshold:
            tagged = dict(node)
            tagged["partial_coverage"] = True
            admitted.append((nid, tagged))
    return admitted


def hydrate_union(
    get_node: Callable[[str], dict | None],
    union: set[str],
    already_matched: set[str],
) -> dict[str, dict]:
    """Materialize *union* candidates (minus *already_matched*) into a node map.

    The child-repo query paths (sqlite impl #2, eager federation impl #3) hold
    only a lazy ``get_node`` handle, but :func:`coverage_admissions` consumes an
    in-memory ``dict[str, dict]``. This builds exactly the map the admission
    scan will visit: already-matched ids are skipped (admission excludes them,
    so re-hydrating them is wasted work) and ``None`` payloads (an id present in
    the inverted index but absent from the node table) are dropped. impl #1
    already has its nodes in memory and does not need this.
    """
    node_map: dict[str, dict] = {}
    for node_id in union:
        if node_id in already_matched:
            continue
        node = get_node(node_id)
        if node is not None:
            node_map[node_id] = node
    return node_map


def strip_match(node: dict, node_id: str) -> dict:
    """Build an envelope match dict, dropping the internal ``_diffuse`` tag.

    ``_diffuse`` is private ranking machinery (the demotion bridge from
    admission to :func:`rank_query_matches`) and must not leak into the public
    query envelope. ``partial_coverage`` is intentionally retained as consumer
    signal, mirroring the ``degraded_match`` envelope flag.
    """
    match = {"id": node_id, **node}
    match.pop("_diffuse", None)
    return match
