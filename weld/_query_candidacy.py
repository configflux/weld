"""When a strict-AND result is not evidence the query was answered (ADR 0113).

:func:`weld.graph_query.query_graph` and its two peers fall back to the OR path
only when strict-AND returns *nothing*. That treats "matched something" as
"answered the question", and two reported gaps are the same counter-example:

* bd pxjc / bd c64p -- the only strict-AND match was the ``concept:`` node
  ``concept_from_bd`` minted from the gap issue's own title. A title quotes the
  query it reports, so it covers every token group, so strict-AND "succeeded"
  with one match and the OR fallback that finds the code never ran::

      wd query "graph storage path resolution project root"
        -> 1 match: concept:weld-brief-cannot-answer-graph-storage-path-...

* bd atcb -- the only strict-AND match was ``FindingSignatureTests.
  test_signature_uses_diagnostic_for_broken_reference``, a test *about* the
  diagnostic. ``weld/agent_graph_metadata_diagnostics.py``, the module that
  emits it, was never a candidate. The OR fallback returns it fourth.

Same shape, different node class, so one rule rather than two special cases: a
strict-AND result that consists entirely of material the ranker would demote is
not evidence, and must not suppress the fallback.

Deriving "non-evidence" from the demotion predicates themselves is the point.
:func:`weld._test_paths.test_noise_demotion` and
:func:`weld._issue_concepts.issue_concept_demotion` already answer "is this what
the user asked for", already return ``1`` for no, and are already gated on the
query (asking for tests, or for the backlog, turns each off). Reusing them means
what gets demoted and what fails to count as an answer cannot drift apart -- and
a future demotion dimension joins both halves by being added once.

The rule is deliberately conservative: it only ever *widens* a result set, never
narrows one, and it declines whenever relaxing would return less than the caller
already holds.
"""

from __future__ import annotations

from typing import Callable

from weld._issue_concepts import issue_concept_demotion
from weld._test_paths import test_noise_demotion

#: The demotion predicates that define "not what the user asked for". Each takes
#: ``(node, token_groups)`` and returns 1 when the node is that. Adding a
#: dimension here extends both the candidacy rule and, by construction, nothing
#: else -- the rank keys import their dimensions directly.
_NON_EVIDENCE_PREDICATES: tuple[Callable[[dict, list[list[str]]], int], ...] = (
    test_noise_demotion,
    issue_concept_demotion,
)


def is_non_evidence(node: dict, token_groups: list[list[str]]) -> bool:
    """Return True when *node* is material this query would demote.

    True means "the ranker would sort this below a substantive peer", not "this
    is irrelevant" -- the node stays in every result set either way.
    """
    return any(
        bool(predicate(node, token_groups))
        for predicate in _NON_EVIDENCE_PREDICATES
    )


def only_non_evidence(
    matched: list[tuple[str, dict]], token_groups: list[list[str]]
) -> bool:
    """Return True when *matched* is non-empty and entirely non-evidence.

    Empty input returns False: an empty strict-AND result already reaches the
    fallback through the pre-existing path, and answering True here would only
    make that decision harder to read.
    """
    if not matched:
        return False
    return all(is_non_evidence(node, token_groups) for _, node in matched)


def relaxed_or_none(
    matched: list[tuple[str, dict]],
    token_groups: list[list[str]],
    run_or_fallback: Callable[[], object],
) -> object | None:
    """Return the relaxed envelope when ADR 0113 candidacy demands one, else None.

    The whole candidacy rule in one call, so the three query impls share the
    decision instead of each re-deriving it::

        relaxed = relaxed_or_none(matched, token_groups, lambda: self._or_fallback(...))
        if relaxed is not None:
            return relaxed

    Returns ``None`` -- "carry on and rank ``matched`` normally" -- whenever
    relaxing would hand back *less* than the caller already holds: when the
    strict-AND result contains any substantive match, and when the fallback
    itself came back empty. The second protects single-token queries, whose OR
    fallback is empty by construction because OR and AND are the same thing for
    one group. Trading a poor answer for no answer would be a worse bug than the
    one this fixes.

    Nothing re-merges the demoted nodes afterwards, and deliberately so: a node
    that matched strict-AND hit every token group, so it is in the OR union and
    the fallback re-surfaces it on its own -- now ranked behind the substantive
    matches rather than instead of them. Retention is a property of the
    fallback, not a merge step three impls would each have to copy.

    *run_or_fallback* is a thunk so the fallback never runs on the overwhelmingly
    common path where strict-AND already found real evidence. Its return value
    passes through untouched, so each impl keeps its own shape (see
    :func:`_has_matches`).
    """
    if not only_non_evidence(matched, token_groups):
        return None
    relaxed = run_or_fallback()
    return relaxed if _has_matches(relaxed) else None


def _has_matches(result: object) -> bool:
    """Return True when an OR-fallback *result* actually carries matches.

    The three impls do not agree on the fallback's return shape and cannot be
    made to here: impls #1 and #2 return the full ``{"query", "matches", ...}``
    envelope, while impl #3's ``_or_fallback`` returns the bare ``list[dict]``
    of matches its caller (``query_child_matches``) is contracted to produce.
    Reading ``.get("matches")`` unconditionally would raise ``AttributeError``
    on the federation path.
    """
    if isinstance(result, dict):
        return bool(result.get("matches"))
    return bool(result)


__all__ = ["is_non_evidence", "only_non_evidence", "relaxed_or_none"]
