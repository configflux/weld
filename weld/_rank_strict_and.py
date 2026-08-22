"""The shared strict-AND ranking key for the two child-repo query impls.

:func:`weld._sqlite_query.query_sqlite_backed` (impl #2) and
:meth:`weld._federation_eager_index.EagerFederationIndex.query_child_matches`
(impl #3) rank their strict-AND matches with the same ten dimensions in the same
order. Until ADR 0113 they did so from two byte-identical private closures,
which is precisely the shape ADR 0112 was written to remove: a copy that nothing
forces to stay a copy. Each new rank dimension had to be applied twice, and
``issue_concept_demotion`` -- the fifth dimension to be added -- is what made the
duplication worth paying off rather than extending again.

Impl #1 (:func:`weld.ranking.rank_query_matches`, the in-memory JSON path)
deliberately does **not** share this key. It ranks by the ADR 0010 hybrid score
-- normalized BM25 blended with semantic and structural signals -- and carries
two dimensions the child-repo paths have no input for (``amalgamation_boost``
needs a cpp amalgamation tag discovery only writes on the root graph;
``role_boost`` needs the ``--role`` filter the child paths never receive).
Folding all three impls into one function would mean passing unused arguments
from two callers to keep a superset key honest; the OR-fallback side is shared
(:func:`weld._coverage_admission.or_fallback_sort_key`) because there the three
really do agree.

Every dimension this key *does* carry sits in impl #1's relative order, and that
is a contract rather than a coincidence (bd cgj3). It was not one before:
``test_noise_demotion`` reached impl #1 alone when bd to8x landed, so the same
query over the same graph was answered by the serializer on the JSON path and by
a lint test *about* json.dumps on the sqlite and federation paths -- three
answers, chosen by which backend happened to reply. ADR 0113 recorded the drift
here rather than fixing it, because closing it moves sqlite and federation rank
order for every multi-token query and that needed its own measurement. The
measurement is the eval corpus: ``weld_query_backend_parity_test`` now runs every
entry in :mod:`weld.tests.query_corpus` through all three impls and asserts they
agree on the leading result, so a dimension added to one key and not the other
fails a gate instead of waiting for a dogfood report.
"""

from __future__ import annotations

from weld._coverage_admission import partial_coverage_subject_miss
from weld._issue_concepts import issue_concept_demotion
from weld._summary_match import summary_only_match_demotion
from weld._test_paths import test_noise_demotion
from weld.ranking import (
    authority_score,
    confidence_score,
    exact_symbol_match_rank,
    resolution_penalty,
)


def strict_and_sort_key(
    node_id: str,
    node: dict,
    token_groups: list[list[str]],
    bm25_score: float,
) -> tuple[int, int, int, int, int, int, int, float, int, int, str]:
    """Return the shared strict-AND ranking key for one matched node.

    Dimensions, in order (lower sorts first throughout):

    1. :func:`weld.ranking.resolution_penalty` -- unresolved-symbol sentinels
       sort below definite resolved peers whatever their BM25;
    2. :func:`weld.ranking.exact_symbol_match_rank` -- an exact symbol
       label/qualname hit on a single-token query leads;
    3. :func:`weld._summary_match.summary_only_match_demotion` -- bd ek4y: a
       single-token match carried only by the node's own ``props.summary``
       (ADR 0124 Go/Rust doc comments, ADR 0118 Python docstrings) sorts
       after every node with independent (non-summary) support. Inert for
       N != 1 and whenever summary is not the sole route to candidacy;
    4. ``_diffuse`` -- an ADR 0075 diffuse full-coverage doc (pre-tagged by
       :func:`weld._coverage_admission.tag_match`, N>=3 only) sorts after every
       non-diffuse node, i.e. below the bounded-coverage code and entity nodes
       admitted alongside it;
    5. :func:`weld._coverage_admission.partial_coverage_subject_miss` -- ADR 0075
       subject tie-break: a coverage-admitted node missing the query's leading
       token-group in every identity field sorts after coverage-tied peers that
       carry it. Inert for N<3 and for non-admitted nodes;
    6. :func:`weld._test_paths.test_noise_demotion` -- bd to8x: test material
       sorts after the code it covers unless the query names tests. Ahead of
       ``-bm25`` because that is the whole failure it answers: a test is named
       after its subject in a sentence while the subject is named in a word, so
       on a prose query the test carries more of the query's vocabulary and wins
       on lexical signal alone. Behind ``exact_symbol_match_rank`` so naming a
       test outright still returns it first. A re-rank, never a filter;
    7. :func:`weld._issue_concepts.issue_concept_demotion` -- ADR 0113: a concept
       node minted from a bd issue title sorts after substantive matches unless
       the query names the backlog. Ahead of ``-bm25`` because an issue title
       quotes the query it reports and would otherwise lead on lexical coverage
       alone;
    8. ``-bm25_score`` -- caller-supplied, so this helper stays free of any
       corpus or backend dependency. Impl #2 passes a lazy-from-sqlite score and
       impl #3 a per-child one, and the two do NOT agree to the last decimal:
       their IDF counts different things (bd ki4u). Dimensions 1-7 are what both
       must agree on, and the backend-parity gate asserts exactly that. Placed
       after dimensions 3-7 so those survive near-tie BM25 noise;
    9-11. ``authority``, ``confidence``, ``node_id`` -- the standard metadata
       tiebreakers, ending in a stable deterministic id sort.
    """
    node_with_id = {"id": node_id, **node}
    return (
        resolution_penalty(node_with_id),
        exact_symbol_match_rank(node_with_id, token_groups),
        summary_only_match_demotion(node_with_id, token_groups),
        int(bool(node.get("_diffuse"))),
        partial_coverage_subject_miss(node_with_id, token_groups),
        test_noise_demotion(node_with_id, token_groups),
        issue_concept_demotion(node_with_id, token_groups),
        -bm25_score,
        authority_score(node_with_id),
        confidence_score(node_with_id),
        node_id,
    )


__all__ = ["strict_and_sort_key"]
