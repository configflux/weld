"""Shared "is this a test artifact" predicates, and the query-side demotion.

:func:`looks_like_test_path` and :func:`is_test_node` were lifted verbatim
from :mod:`weld.arch_lint_orphan`, which is now a caller rather than an
owner. The move is what kept bd to8x from adding a *third* near-copy of the
same convention list -- :mod:`weld.impact_surfaces` already carries a second
one, narrower on purpose (it answers "which bucket does this node go in",
including the ``test-target`` node types, so folding it in here would change
what ``wd impact`` reports and is deliberately left alone).

The retrieval half is the reason this module exists at all. ``wd query
"graph.json serialization write json dump indent"`` ranked eight
``tools.lint_terminal_safety_test`` symbols above ``weld.serializer``
-- the documented single funnel every canonical ``graph.json`` emitter goes
through, which sat at rank 31 of 57 and so fell outside the default limit of
20 entirely (bd to8x).

Nothing in the graph separated the two. ``python_callgraph`` stamps
``roles: ["implementation"]`` on *every* symbol it mints, including symbols
defined inside a test module; only the ``file:`` node a test gets from
``test_peer`` carries ``roles: ["test"]``. So both nodes reached the ranker
as ``authority=derived, confidence=definite, roles=["implementation"]`` and
the tie broke on lexical signal alone -- which a test loses by construction:
a test is *named* after the behaviour it covers in a full sentence
(``test_unwrapped_graph_dump_is_flagged`` carries json/serializer/graph/dump)
while the code it covers is named in one or two words (``dumps_graph``). The
more thoroughly a test is named, the further it buries its own subject.

The fix is a re-rank, never a filter: test nodes stay in the result set and
keep their relative order, they just sort after the non-test nodes they are
tied with. Excluding them would break the query that wants them, and the
demotion already declines to fire for that query -- see
:func:`query_names_tests`.

bd ikof added the second exemption below, :func:`_summary_earns_exemption`,
for a query that never says "test" but whose entire subject is a property
only a test states -- ``"incremental discovery equivalence full"`` matched
none of the ``incremental_*_equivalence_test.py`` files at all, because
``test_peer`` (unlike every other discovery strategy since ADR 0114) never
read a test file's own docstring into ``props.summary``. Fixing that gap
alone was not sufficient: the OR-fallback sort key ranks this demotion ahead
of group-hit count, so a demoted test node cannot outrank *any* non-test
node regardless of how many groups its new summary lets it hit, and the
measured live graph already has 147+ non-test nodes matching this query's
generic "incremental" vocabulary -- more than the default result window.

A coverage-count exemption alone would not be honest, though: bd to8x's and
bd atcb's own adversarial fixture nodes (test methods named
``test_unwrapped_graph_dump_is_flagged`` / ``..._for_broken_reference``)
reach full group coverage too, via qualname alone -- that is the *original*
defect this module exists to correct, a verbose test name accidentally
out-lexing a one-word production symbol. Coverage count cannot tell the two
apart; neither fixture node carries a ``props.summary`` at all. What
distinguishes them is where the coverage comes from: bd ikof's equivalence
tests reach their coverage partly *because of* their own summary (the
docstring literally states "a full discover" -- the one group their
filename alone does not carry), while bd to8x/atcb's noise reaches it
entirely without one. So the exemption requires both: high total coverage
(ADR 0075's own ``max(2, N-1)`` admission bar, reused rather than invented)
AND ``props.summary`` contributing a group hit the node would not otherwise
have -- checked the same way bd ek4y's :func:`weld._summary_match.
summary_only_match_demotion` already checks candidacy: strip the field,
recount, compare.

Scoped to the OR-fallback callers only (:func:`weld._coverage_admission.
or_fallback_sort_key`, via the optional *nid* / *group_hits* keywords),
never to :func:`weld.ranking.rank_query_matches`'s strict-AND-admission
path. That path's own coverage-admission gate (ADR 0075) already bounds
every candidate it hands this function to ``>= max(2, N-1)`` before ranking
starts, so the same threshold checked again there would be true for nearly
every candidate by construction -- including bd to8x's noise node, which
*does* reach full strict-AND coverage on its verbatim six-token query. The
two paths need different answers to "is this coverage strong enough", so
the exemption only fires where the threshold is still a real filter.
"""

from __future__ import annotations

from weld._match_surface import count_group_hits

#: Node types that *are* a test by construction, whatever path they carry.
#: ``test-target://weld/tests:weld_atomic_write_bytes_test`` outranked the
#: atomic-write helpers themselves on the second query in bd to8x, and it has
#: no ``props.file`` to catch it by -- a Bazel target is named, not located.
_TEST_NODE_TYPES: frozenset[str] = frozenset({"test-target", "test-suite"})

#: Query tokens that mean the user is asking *for* tests. Matched against the
#: raw token a group was built from, never the expanded group: per
#: :func:`weld.synonyms.expand_token_groups` the group for ``test`` is
#: ``[test, spec, fixture, mock, assert, unittest, pytest, tests]``, so
#: scanning whole groups would let one synonym table decide when the demotion
#: applies and silently widen this guard every time that table grows.
_TEST_QUERY_TOKENS: frozenset[str] = frozenset({
    "fixture", "fixtures", "pytest", "spec", "specs", "test",
    "testcase", "testing", "tests", "unittest",
})


def looks_like_test_path(path: str) -> bool:
    """Return True when *path* looks like a test source file."""
    if not path:
        return False
    p = path.replace("\\", "/").lower()
    if "_test.py" in p or "_test.go" in p:
        return True
    if ".test.ts" in p or ".test.tsx" in p or ".test.js" in p:
        return True
    segments = p.split("/")
    if "tests" in segments or "__tests__" in segments:
        return True
    base = segments[-1]
    if base.startswith("test_"):
        return True
    return False


def is_test_node(node: dict) -> bool:
    """Return True when *node* should be classified as a test node."""
    props = node.get("props") or {}
    file_path = str(props.get("file") or "")
    if looks_like_test_path(file_path):
        return True
    # Roles tagged 'test' (rare but possible).
    roles = props.get("roles") or []
    if isinstance(roles, list) and "test" in roles:
        return True
    return False


def query_names_tests(token_groups: list[list[str]]) -> bool:
    """Return True when the query itself asks for tests.

    Reads element 0 of each group, which :func:`weld.synonyms.
    expand_token_groups` guarantees is the token the user actually typed
    (``group = [tok]`` before any alias or stem is appended).

    This is what keeps the demotion from being a filter with extra steps.
    ``wd query "discover writes file index test"`` wants the test file first,
    and would otherwise get it pushed below every module that merely
    mentions discovery -- the demotion inverting the one query whose intent
    is unambiguous.
    """
    for group in token_groups:
        if group and group[0] in _TEST_QUERY_TOKENS:
            return True
    return False


def _summary_earns_exemption(
    node: dict, nid: str, token_groups: list[list[str]], group_hits: int
) -> bool:
    """Return True when *node*'s own summary earns it out of the demotion.

    Two conditions, both required (bd ikof):

    * **High total coverage** -- *group_hits* meets ADR 0075's own
      high-coverage admission bar (``max(2, N-1)`` of N groups), the same
      number :func:`weld._coverage_admission.coverage_admissions` already
      uses to decide a bounded-coverage match is strong enough to admit.
      Below it, a test node is not a strong enough match for this to matter.
    * **The summary is load-bearing** -- stripping ``props.summary`` and
      recounting (the same strip-and-recount :func:`weld._summary_match.
      summary_only_match_demotion` already does for the single-token case)
      yields *fewer* hits than *group_hits*. A test node whose coverage
      comes entirely from its id/label/file/qualname -- exactly bd to8x's
      and bd atcb's adversarial shape, a verbose test name that happens to
      lexically cover the query -- does not earn the exemption: its summary
      contributed nothing, so stripping it changes nothing.

    *nid* is required (not read from ``node["id"]``) so the recount uses the
    same id the caller's own *group_hits* was computed against; an empty
    fallback would silently drop the id from both haystacks and could make
    the delta look larger than it is.

    ``max(2, N-1)`` is a local literal, not an import of :data:`weld.
    _coverage_admission._COVERAGE_MIN_GROUPS` / the threshold inside
    :func:`weld._coverage_admission.coverage_admissions` -- that module
    already imports :func:`test_noise_demotion` from this one, so importing
    back would cycle. Recomputing one small formula here is the same
    trade-off ADR 0120 made for :func:`weld._rank_subject.
    _subject_identity_match`'s separator-variant split, for the same reason.
    """
    n = len(token_groups)
    if group_hits < max(2, n - 1):
        return False
    props = node.get("props") or {}
    if not props.get("summary"):
        return False
    stripped = {**node, "props": {**props, "summary": ""}}
    hits_without_summary = count_group_hits(
        token_groups, nid, stripped, short_circuit=False, include_constants=False,
    )
    return hits_without_summary < group_hits


def test_noise_demotion(
    node: dict,
    token_groups: list[list[str]],
    *,
    nid: str = "",
    group_hits: int | None = None,
) -> int:
    """Return 1 when *node* is test material the query did not ask for.

    A coarse pre-score gate in the shape :mod:`weld.ranking` already uses for
    ``resolution_penalty`` and the ADR 0075 ``_diffuse`` demotion: 0 sorts
    ahead of 1, so this splits the candidates into "not tests" and "tests"
    and the existing hybrid score orders each side exactly as before.

    Returns 0 for every node when the query names tests, so the guard costs
    one pass over the token groups and nothing else.

    *nid* / *group_hits* (bd ikof): optional, supplied only by the
    OR-fallback callers (:func:`weld._coverage_admission.
    or_fallback_sort_key`), which already compute *group_hits* for their own
    ``-group_hits`` dimension. When present, a test node whose own summary
    earns :func:`_summary_earns_exemption` is exempted (returns 0) instead
    of demoted. :func:`weld.ranking.rank_query_matches` (the strict-AND +
    coverage-admission path) never passes either, so its behavior -- and
    every golden that depends on it, including bd to8x -- is unchanged.
    """
    if query_names_tests(token_groups):
        return 0
    is_test = node.get("type") in _TEST_NODE_TYPES or is_test_node(node)
    if not is_test:
        return 0
    if group_hits is not None and _summary_earns_exemption(
        node, nid, token_groups, group_hits
    ):
        return 0
    return 1


__all__ = [
    "is_test_node",
    "looks_like_test_path",
    "query_names_tests",
    "test_noise_demotion",
]
