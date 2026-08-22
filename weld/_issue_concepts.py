"""Issue-derived concept nodes: what they are, and why they are not evidence.

``concept_from_bd`` mints one ``concept:`` node per bd issue, labelled and
described by the issue *title*. A dogfood-gap issue's title quotes the query it
is about, so filing "query X returns noise" creates a node that matches query X
-- and, because an issue title is a whole sentence, matches *every* token group
of it. ADR 0113 is the decision this module implements.

The failure that decision is built on is a **candidacy** failure, not a ranking
one. :func:`weld.graph_query.query_graph` falls back to the OR path only when
strict-AND returns nothing; a full-coverage concept node makes strict-AND
"succeed" with exactly one match, so the fallback never runs and the code nodes
are never candidates at all. Measured on the repo graph at 6ed432bb:

    wd query "tree-sitter availability gate"            (bd pxjc)
      -> 1 match: concept:query-for-the-tree-sitter-availability-gate-...
    wd query "graph storage path resolution project root"  (bd c64p)
      -> 1 match: concept:weld-brief-cannot-answer-graph-storage-path-...

Zero code nodes in either envelope. Demoting the only element of a one-element
result set changes nothing, so the fix needs a candidacy half as well as a rank
half. This module owns only the rank half, :func:`issue_concept_demotion`; the
candidacy half lives in :mod:`weld._query_candidacy`, which derives it from this
predicate and its test-material peer rather than duplicating either -- bd atcb
turned out to be the identical failure with a *test* node in the concept's
place, so the two are one rule.

The demotion keys on ``props.source_strategy == "concept_from_bd"`` -- provenance,
never label-similarity-to-the-query. A similarity threshold is undefendable,
costs a re-tokenization per concept node per query, and breaks in the case that
matters: edit the issue title, drift one token, and the demotion silently stops
firing. Provenance states the real invariant -- an issue-derived concept is a
restatement of a human's issue title, never an observation about the code.
Hand-authored ``concept:`` nodes (enrichment, ``wd add-node``) carry no such
provenance and are untouched: somebody asserted those about the code
deliberately, so they *are* evidence.

Defined once here and consumed by all three query impls (the JSON ``Graph`` read
path, its sqlite peer, the eager federation path) in the shape
:mod:`weld._test_paths` established -- each impl has its own sort key and its
own strict-AND -> fallback chain, and a rule written into only one of them is
the drift ADR 0112 paid to remove.
"""

from __future__ import annotations

#: The ``props.source_strategy`` value stamped by ``weld/strategies/concept_from_bd.py``.
#: The single signal both halves key on; see the module docstring for why this
#: is provenance rather than label similarity.
_BD_CONCEPT_STRATEGY: str = "concept_from_bd"

#: Query tokens that mean the user is asking *for* the backlog. Matched against
#: element 0 of each group -- the token the user actually typed -- never the
#: expanded group, for the reason :data:`weld._test_paths._TEST_QUERY_TOKENS`
#: documents: scanning whole groups would let the synonym table silently widen
#: this guard every time it grows.
_BACKLOG_QUERY_TOKENS: frozenset[str] = frozenset({
    "backlog", "bd", "bug", "concept", "issue", "issues", "ticket",
})


def is_issue_derived_concept(node: dict) -> bool:
    """Return True when *node* was minted from a bd issue by ``concept_from_bd``.

    Reads provenance only. A node is issue-derived when its
    ``props.source_strategy`` is ``concept_from_bd``; the node ``type`` is not
    consulted, so if that strategy ever mints something other than
    ``type=concept`` the rule still covers it.
    """
    props = node.get("props") or {}
    return props.get("source_strategy") == _BD_CONCEPT_STRATEGY


def query_names_backlog(token_groups: list[list[str]]) -> bool:
    """Return True when the query itself asks for backlog material.

    Mirrors :func:`weld._test_paths.query_names_tests`. Reads element 0 of each
    group, which :func:`weld.synonyms.expand_token_groups` guarantees is the
    token the user typed (``group = [tok]`` before any alias or stem).

    This is what keeps the demotion from inverting the one query whose intent is
    unambiguous: ``wd query "bd issue worktree isolation"`` wants the issue
    node, and would otherwise get it pushed below every module that merely
    mentions worktrees.
    """
    for group in token_groups:
        if group and group[0] in _BACKLOG_QUERY_TOKENS:
            return True
    return False


def issue_concept_demotion(node: dict, token_groups: list[list[str]]) -> int:
    """Return 1 when *node* is an issue-derived concept the query did not ask for.

    A coarse pre-score gate in the shape :mod:`weld.ranking` already uses for
    ``resolution_penalty``, the ADR 0075 ``_diffuse`` demotion and
    :func:`weld._test_paths.test_noise_demotion`: 0 sorts ahead of 1, so this
    splits candidates into "not issue restatements" and "issue restatements",
    and the existing score orders each side exactly as before.

    This is a re-rank and never a filter -- the concept node stays in the result
    set, which is what bd 9ucf asked for ("the concept node alongside rather
    than instead of them"). It is needed *in addition to* the candidacy rule
    because an issue title covers every token group of the query it quotes, so
    on the ``-group_hits`` dimension of the OR-fallback key a concept node
    outranks the code by construction.

    Returns 0 for every node when the query names the backlog, so the guard
    costs one pass over the token groups and nothing else.
    """
    if query_names_backlog(token_groups):
        return 0
    return 1 if is_issue_derived_concept(node) else 0


__all__ = [
    "is_issue_derived_concept",
    "issue_concept_demotion",
    "query_names_backlog",
]
