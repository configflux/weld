"""Coverage-aware admission and diffuse-doc demotion helpers (ADR 0075).

Pure, dependency-free helpers for the JSON-``Graph`` read path (impl #1). Split
out of :mod:`weld.graph_query` to keep that module under the line-count cap; the
logic is unchanged. Both the admission relaxation and the diffuse-doc demotion
are gated to N>=3 (the load-bearing dual gate): below it ``max(2, N-1) == N ==
full coverage`` so admission admits nothing strict-AND would not, and demotion
must NOT run (it could reorder a future N<=2 doc-vs-code result a golden
depends on).

The match surface here is :mod:`weld._match_surface`, the same one
:meth:`weld.graph.Graph._match_token_groups` uses (ADR 0075 requires admission
and strict-AND to agree on what "covered" means, and the reliable way to make
two functions agree on a field list is to give them one).
"""

from __future__ import annotations

from typing import Callable

from weld._issue_concepts import issue_concept_demotion
from weld._match_surface import count_group_hits
from weld._test_paths import test_noise_demotion

# bd jkir / ADR 0107: the query-subject primitives and the two rank dimensions
# that turn on the subject alone live in weld._rank_subject, which is where they
# went when this module hit the 400-line cap. Re-exported here because
# weld.ranking, weld.graph_query, weld._sqlite_query, weld._federation_eager_index
# and the tests that pin them all address these at this module's path.
from weld._rank_subject import (  # noqa: F401 -- re-exported for callers
    _group_hits_string,
    _identity_values,
    _subject_in_identity,
    subject_identity_miss,
    subject_identity_specificity,
    subject_label_exact_miss,
)

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

    The match surface is :mod:`weld._match_surface`, shared with
    :meth:`Graph._match_token_groups` rather than restated -- ADR 0075 requires
    admission and strict-AND to agree on what "covered" means, and two copies
    of a field list is how they stop agreeing. So this includes
    ``props.constants``, unlike the OR-fallback counter
    :func:`count_groups_hit` below.
    """
    return count_group_hits(token_groups, nid, node, short_circuit=False)


def count_groups_hit(
    token_groups: list[list[str]], nid: str, node: dict
) -> int:
    """Count how many ``token_groups`` are hit by the OR-fallback surface.

    The single, shared definition of the OR-fallback group-hit counter used by
    BOTH OR-fallback impls (the JSON ``Graph`` read path and its sqlite peer),
    so the two cannot drift on what "hit a group" means. Unlike
    :func:`covered_group_count` (the *admission*/strict-AND counter), this
    surface intentionally OMITS ``props.constants``: that is the pre-existing
    OR-fallback field set, and the relaxation tier deliberately retains it so
    the OR fallback never *under* counts a node strict-AND would have hit while
    keeping the existing OR-fallback behavior unchanged. That omission is the
    single argument :mod:`weld._match_surface` takes, so it stays a decision
    rather than becoming a sixth copy of the field list. Like
    :func:`covered_group_count`, it does NOT short-circuit on a missing group --
    it counts partial hits so callers can rank by ``num_groups_hit_desc``.
    """
    return count_group_hits(
        token_groups, nid, node, short_circuit=False, include_constants=False,
    )


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


def or_fallback_sort_key(
    nid: str,
    node: dict,
    group_hits: int,
    token_groups: list[list[str]],
    bm25_score: float,
) -> tuple[int, int, int, int, int, int, float, str]:
    """Return the shared OR-fallback ranking key for one union candidate.

    The single, shared definition of the OR-fallback sort order used by ALL
    THREE OR-fallback impls (the JSON ``Graph`` read path, its sqlite peer and
    the eager-federation path), so they cannot drift on rank. The dimensions, in
    order:

    1. :func:`subject_label_exact_miss` -- a node whose ``label`` *is* the query
       subject leads, ahead of group count (ADR 0107). Inert unless some
       candidate's label exactly equals a subject token;
    2. :func:`weld._test_paths.test_noise_demotion` -- test material sorts
       below non-test material unless the query names tests (bd to8x). It has
       to sit *above* group count, because outranking the code is exactly what
       a test's naming convention earns it here: the query "graph.json
       serialization write json dump indent" hit two groups on
       ``JsonSerializerBoundaryTest.test_unwrapped_graph_dump_is_flagged`` and
       one on ``weld.serializer.dumps_graph``, the funnel that actually writes
       the file. A test states its subject in a sentence; the subject states
       itself in a word. Below dimension 1, so naming a test exactly still
       returns it first, and gated on the query, so asking for tests still
       ranks them top. *nid* / *group_hits* are threaded through (bd ikof) so
       a test node whose own summary -- not just its name -- earns high
       coverage of the query can be exempted; see
       :func:`weld._test_paths._summary_earns_exemption`;
    3. :func:`weld._issue_concepts.issue_concept_demotion` -- a concept node
       minted from a bd issue title sorts below substantive matches unless the
       query names the backlog (ADR 0113). It sits above ``-group_hits`` for the
       same reason dimension 2 does, only more so: an issue title is a whole
       *sentence* quoting the query it reports, so it covers every token group
       by construction and takes the group count outright. Without this, the
       ADR 0113 candidacy rule would admit the code nodes and still leave the
       issue's own restatement sitting on top of them;
    4. ``-group_hits`` -- a node hitting more query groups wins;
    5. :func:`subject_identity_miss` -- among group-hit-tied candidates, a
       0/1/2 tier (ADR 0120) for how the query *subject* (leading token-group)
       lands in an identity field: 0 for a raw-token/alias hit, 1 for a
       separator-variant-only hit (bd 2xoj respellings, e.g. ``graph_json`` for
       a ``graph.json`` query), 2 for absent from every identity field. A
       higher tier sorts later, so a variant-only hit does not beat a raw hit,
       and neither beats a subject-bearing peer on BM25 IDF rarity alone.
       Inert (always 0) for single-token queries;
    6. :func:`subject_identity_specificity` -- among candidates *tied* on tier
       5 (almost always both tier 0, e.g. two nodes that both state the raw
       subject), the one whose matched identity field is shorter wins. BM25
       (dimension 7) scores a node's whole indexed text, bag fields included,
       so it is not a reliable arbiter between two nodes that both name the
       subject directly -- it rewards whichever candidate happens to have less
       *unrelated* text, not whichever states the subject more concisely. This
       dimension asks the narrower question directly. Inert (0) for single-
       token queries or a tier-2 (miss) candidate, where dimension 5 already
       decided the order;
    7. ``-bm25_score`` -- the caller-supplied BM25 score (each impl computes it
       with its own corpus accessor; placed after the subject dimensions so they
       survive near-tie BM25 noise);
    8. ``nid`` -- a stable, deterministic final tiebreak.

    :func:`subject_label_exact_miss` and :func:`subject_identity_miss` are
    deliberately *not* merged: the former is exact-equality on ``label`` and
    outranks retrieval count, the latter is substring across every identity
    field and only breaks ties within a count. Collapsing them would either make
    the exact signal substring-loose or promote every substring identity hit
    above group count, and ADR 0075 chose the latter's placement on purpose.

    *bm25_score* is passed in (not computed here) so this helper stays free of
    any corpus/backend dependency: the JSON path supplies
    ``Graph._bm25.score(...)`` and the sqlite path supplies its lazy-from-sqlite
    BM25 score, but both rank identically.
    """
    return (
        subject_label_exact_miss(node, token_groups),
        test_noise_demotion(node, token_groups, nid=nid, group_hits=group_hits),
        issue_concept_demotion(node, token_groups),
        -group_hits,
        subject_identity_miss(nid, node, token_groups),
        subject_identity_specificity(nid, node, token_groups),
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
