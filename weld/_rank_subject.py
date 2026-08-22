"""What the query *subject* is, and the two ranks that turn on it alone.

Carved out of :mod:`weld._coverage_admission` for the reason
:mod:`weld._graph_match` was (bd jkir): the ADR 0107 exact-subject-label
dimension pushed that module past the 400-line cap, and the cheapest local fix
-- deleting the reasoning that explains why the new dimension leads the key --
is worse than the right one.

What came out is the group that answers one question: *is this node about what
the user asked for?* The subject is the leading token group, and
:func:`_subject_in_identity` already existed to stop two ranking dimensions
drifting on what that means; ADR 0107 added a third reading of it, so the
notion earned a home. :func:`subject_identity_miss` (ADR 0075) and
:func:`subject_label_exact_miss` (ADR 0107) live here because they need nothing
but the subject.

``partial_coverage_subject_miss`` deliberately did **not** come with them. It is
the same notion applied in the strict-AND admission tier, and it is gated on
that tier's ``_COVERAGE_MIN_GROUPS``, so it stays beside the gate it shares
rather than dragging the constant across a module boundary and making the
dependency circular. It builds on the boolean :func:`_subject_in_identity`
only -- not on the graded classification below -- so bd ght0 does not touch
its behavior (see :func:`_subject_identity_match`).

:mod:`weld._coverage_admission` re-exports both functions, so every existing
importer keeps addressing them at their original home. The dependency runs one
way: that module imports this one, and nothing here imports it back.

ADR 0120 (bd ght0) added :func:`subject_identity_specificity` and graded
:func:`subject_identity_miss` from a 0/1 hit-or-miss into a 0/1/2 tier: ADR
0119 widened separator-variant matching so a re-spelled node is *reachable*,
but a re-spelling is not as strong *identity* evidence as the user's own
spelling -- see :func:`_subject_identity_match` for the full argument.
"""

from __future__ import annotations

from weld.synonyms import _separator_variants

__all__ = [
    "_group_hits_string",
    "_identity_values",
    "_subject_identity_match",
    "_subject_in_identity",
    "subject_identity_miss",
    "subject_identity_specificity",
    "subject_label_exact_miss",
]


def _group_hits_string(group: list[str], value: str) -> bool:
    """Return True if any synonym ``t`` in *group* is a substring of *value*."""
    return any(t in value for t in group)


def _identity_values(nid: str, node: dict) -> list[str]:
    """Return the lowered identity-field strings for *node* (ADR 0075/0116).

    Identity fields are the ones that make a node *about* a concept (as
    opposed to merely mentioning it): the node id, ``label``, ``props.file``,
    ``props.qualname``, ``props.description`` and ``props.summary``. This is
    the exact set the diffuse-doc discriminator (:func:`is_diffuse_doc`)
    treats as "identity"; sharing one extractor keeps the two ADR 0075
    demotion signals from drifting on what "identity" means. Bag fields
    (``headings`` / ``constants`` / ``exports``) are intentionally excluded --
    a hit there is a scattered mention, not an identity.

    ``props.summary`` (ADR 0114: a module's own opening docstring paragraph,
    read from source every discover) joined this list at ADR 0116. It was
    left out when the OR-fallback/admission subject dimensions were written
    because it did not exist yet; once it did, excluding it was an
    inconsistency rather than a decision -- ``description`` (an LLM's
    *inferred* opinion about the node) already counted as identity, while
    ``summary`` (the module's own stated purpose, structural and always
    present once discovery runs) did not. A node mentioned only by name in a
    query but described only in its own docstring -- e.g. ``graph.json``,
    named nowhere in ``weld/serializer``'s id/label/path but stated in its
    opening line -- was misread as "not about" the query's subject, and lost
    the subject tie-break to any node whose *name* happened to contain a
    generic query token instead.
    """
    props = node.get("props") or {}
    return [
        nid.lower(),
        node.get("label", "").lower(),
        (props.get("file") or "").lower(),
        str(props.get("qualname") or "").lower(),
        (props.get("description") or "").lower(),
        (props.get("summary") or "").lower(),
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

    Delegates to :func:`_subject_identity_match` (bd ght0) so the admission
    tier keeps exactly its pre-existing boolean behavior -- raw and
    separator-variant hits both count as "in identity" here, unchanged -- while
    the OR-fallback tier alone reads the graded tier that function also
    returns. tier < 2 iff some group member (raw, alias, or variant) hit,
    which is byte-for-byte the old ``any(...)`` condition.
    """
    return _subject_identity_match(nid, node, token_groups)[0] < 2


def _subject_identity_match(
    nid: str, node: dict, token_groups: list[list[str]]
) -> tuple[int, str | None]:
    """Classify how the query *subject* lands in *node*'s identity fields.

    Returns ``(tier, field_value)``:

    * **tier 0** -- some identity field contains the RAW subject token the
      user typed, or one of its non-separator aliases (a synonym or stem).
      ``field_value`` is the shortest such field.
    * **tier 1** -- no identity field carries the raw token or a non-separator
      alias, but one carries a *separator-variant* re-spelling of it (bd 2xoj:
      the same characters, punctuated differently -- ``graph_json`` for a
      ``graph.json`` query). ``field_value`` is the shortest such field.
    * **tier 2** -- the subject is absent from every identity field (raw,
      alias, and variant alike). ``field_value`` is ``None``.

    ADR 0119 widened separator-variant matching so a re-spelled node is
    *reachable* -- it must be a candidate, and OR-fallback must not exclude
    it. It does not follow that a re-spelling is as strong *identity*
    evidence as the user's own spelling: bd ght0 found the widening let every
    ``graph_json``-shaped symbol in the repo (functions that only check or
    locate the file) tie ``weld/serializer`` (the write funnel, which states
    ``graph.json`` verbatim in its own docstring) at "hits the subject" for
    ``"where is graph.json written"`` -- and BM25 then broke the tie on term
    density, favoring the short symbol ids over the correct, longer answer.
    Tiering the match keeps the widened reach (tier 1 still outranks tier 2,
    a true miss) while restoring the raw spelling's priority (tier 0 outranks
    tier 1). See ADR 0120.

    ``field_value`` (the shortest matching field, not just whether one
    exists) feeds :func:`subject_identity_specificity`, the companion
    dimension that breaks a tier-0-vs-tier-0 tie BM25 gets wrong for a
    structural reason: BM25 scores a node's *entire* indexed text, so a
    verbose file node is diluted relative to a short function whose one-line
    docstring happens to name the same subject. Comparing only the identity
    field that actually matched sidesteps that dilution.

    Both :func:`_subject_in_identity` and :func:`subject_identity_miss` /
    :func:`subject_identity_specificity` are views onto this one
    classification so they cannot drift on what counts as which tier.
    """
    if not token_groups or not token_groups[0]:
        return 0, None
    subject_group = token_groups[0]
    raw = subject_group[0]
    variants = set(_separator_variants(raw))
    strong = [t for t in subject_group if t not in variants]
    weak = [t for t in subject_group if t in variants]
    identity = [v for v in _identity_values(nid, node) if v]
    strong_hits = [v for v in identity if _group_hits_string(strong, v)]
    if strong_hits:
        return 0, min(strong_hits, key=len)
    weak_hits = [v for v in identity if _group_hits_string(weak, v)]
    if weak_hits:
        return 1, min(weak_hits, key=len)
    return 2, None


def subject_identity_miss(
    nid: str, node: dict, token_groups: list[list[str]]
) -> int:
    """Return how badly an OR-fallback candidate misses the query *subject*.

    The OR-fallback tier (:func:`weld.graph_query.query_or_fallback`) is the
    *degraded* retrieval path: it fires only when strict-AND yields zero
    matches on a multi-token query, and ranks the per-group union by
    ``(group_hits_desc, subject_tier, specificity, BM25_desc, id)``. Without a
    subject signal it suffers the same defect as the admission tier -- for
    ``"typescript discovery strategy"`` on a graph with no node covering all
    three groups (the durable, clean-graph case once the transient
    ``concept:<issue>`` node is gone), ``discovery_state`` (group_hits 2, no
    ``typescript``) outranks the TypeScript strategy modules (group_hits 2,
    with ``typescript``) purely on BM25 IDF rarity.

    This restores intent by sorting any union candidate that does NOT carry
    the subject (leading token-group) in an identity field *after* its
    group-hit peers that do. Gated to multi-token queries (``len(token_groups)
    >= 2``); single-token queries never reach OR-fallback and have no distinct
    subject.

    Returns the :func:`_subject_identity_match` tier verbatim: ``0`` when the
    raw token (or a non-separator alias) hits an identity field, ``1`` when
    only a separator-variant re-spelling hits (bd ght0 / ADR 0120), ``2`` when
    the subject is absent from every identity field. Was a 0/1 hit-or-miss
    before ADR 0120 added the middle tier -- ``1`` used to mean today's ``2``;
    callers that compared for exact equality rather than ordering had to move
    with it (see ``weld_typescript_strategy_query_test.py``). Placed ahead of
    ``-bm25`` and after ``-group_hits`` in the OR-fallback key, so a node
    hitting strictly more groups still wins. Pure re-rank: nothing is
    excluded.
    """
    if len(token_groups) < 2:
        return 0
    tier, _ = _subject_identity_match(nid, node, token_groups)
    return tier


def subject_identity_specificity(
    nid: str, node: dict, token_groups: list[list[str]]
) -> int:
    """Return the length of the identity field that carries the subject match.

    Second-order OR-fallback tie-break (bd ght0 / ADR 0120), placed directly
    after :func:`subject_identity_miss` and ahead of ``-bm25_score`` so it
    decides ties BM25 would otherwise get wrong for a structural reason: BM25
    scores a node's *entire* indexed text (every bag field included -- headings,
    constants, exports), so a whole-file node with many exports is diluted
    relative to a single short function whose one-line docstring happens to
    name the same subject -- even when the file's own docstring states the
    subject as its entire purpose. ``"where is graph.json written"`` is the
    reported case: ``weld/serializer`` (40-character summary, "Canonical
    serializer for ``graph.json``.") and ``weld.doctor._check_graph_json``
    (68-character summary, "Report graph.json presence + ...") both carry a
    raw hit (tier 0), so :func:`subject_identity_miss` alone cannot separate
    them, and whole-node BM25 favors the shorter symbol.

    Comparing only the length of the identity field that actually matched
    (ignoring exports/headings/constants entirely) asks a narrower, better
    -justified question than BM25's: which candidate names the subject most
    directly, in the fewest surrounding words? Ascending sort (shorter field
    wins) rewards the more on-point statement.

    Returns ``0`` -- inert, not a specificity claim -- for a candidate with no
    identity-field hit (tier 2) or for a single-token query; the dimension it
    follows already sorts those cases out, so this one never needs to break a
    tie for them.
    """
    if len(token_groups) < 2:
        return 0
    _, field_value = _subject_identity_match(nid, node, token_groups)
    return len(field_value) if field_value is not None else 0


def subject_label_exact_miss(
    node: dict, token_groups: list[list[str]]
) -> int:
    """Return 0 when the node's ``label`` *is* the query subject (ADR 0107).

    The leading dimension of the OR-fallback key, and the only matcher in this
    module that compares by **equality** rather than substring. It exists
    because :func:`subject_identity_miss` was placed *after* ``-group_hits``,
    where it can only break ties between candidates hitting the same number of
    groups -- so a node whose entire label is the query's subject still lost to
    any node that overlapped two generic tokens, and not merely in rank:

        query "weld_examples_test"                      -> that node, rank 1, sole match
        query "weld_examples_test discover integration" -> that node absent entirely

    The winners carried ``discover`` + ``integration`` (2 groups) against the
    target's 1. Adding one adjacent word evicted the node the query names
    verbatim (bd xsdm).

    Two narrowings keep this at the defect and away from ADR 0075's goldens:

    * **exact equality, not substring** -- the entire justification is that the
      user typed this node's name. Substring matching here would re-open the
      generic-overlap problem the dimension exists to close;
    * **subject group only** -- the leading token-group is this module's
      established spelling of "the subject" (:func:`_subject_in_identity`).
      Honouring any token would let a node labelled ``main`` or ``cli`` dominate
      every query mentioning those words.

    So the dimension is inert unless a candidate's label exactly equals the
    user's leading token, which no ADR 0075 golden turns on. Synonyms expanded
    into the subject group count: an alternate spelling of the subject is still
    the subject. Empty *token_groups* -> 1 (no subject, so no node earns the
    lead). Pure re-rank: nothing is excluded.
    """
    if not token_groups:
        return 1
    label = node.get("label", "").lower()
    if not label:
        return 1
    return 0 if label in set(token_groups[0]) else 1
