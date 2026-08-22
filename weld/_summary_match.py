"""Summary-only match demotion for single-token strict-AND queries.

ADR 0124 gave Go and Rust ``symbol:`` nodes ``props.summary`` from their own
doc comments, mirroring what ADR 0118 already did for Python and ADR 0114 for
``file:`` nodes. The field is unconditionally part of the match surface
(:mod:`weld._match_surface`, ADR 0114), so a symbol's own doc-comment prose
became retrievable for the first time on every language ADR 0124 touched.

Doc comments routinely *mention* another symbol by name inside a relational
sentence -- "Area returns pi * r^2, satisfying shapes.Shape", "Rectangle ...
also embeds shapes.Base and defines Area" -- and a bare-substring match
surface cannot tell that mention apart from a node stating something about
itself. On the bundled Go tier1 fixture
(``weld/tests/fixtures/tier1/go/sample_go``) this made eight single-token
queries -- ``Shape``, ``Base``, ``shapes``, ``Area``, ``Describe``,
``FormatLabel``, ``Format``, ``test`` -- retrieve a same-package neighbor
that is not about the query subject at all, crowding the real gold members
out of the fixture's fixed ``top_k`` window and dropping the ADR 0064
criterion-7 mean F1 from 1.0 to 0.8927 (below the 0.9 gate; bd ek4y).

Distinguishing "the doc comment asserts this about itself" from "the doc
comment mentions another symbol's name" would need grammatical subject
detection this codebase has never had -- ADR 0116 recorded the identical
substring-match imprecision for ``props.description`` and explicitly left it
unsolved ("Same imprecision description already carried... Noted rather than
solved here"). This module asks a narrower, purely mechanical question
instead: strip ``props.summary`` and ask whether the node would still be a
strict-AND candidate at all. A node with independent support (its own id,
label, file, qualname, or any other match-surface field) is unaffected by
what its summary also happens to say -- exactly the Base/Describe/FormatLabel
members of the fixture's ``shapes`` package, which all carry the query
through their own ``file``/``id``/``exports`` regardless of summary text. A
node whose *only* route to candidacy is its own summary is exactly the shape
every measured false positive shares.

Gated to single-token queries, mirroring :func:`weld.ranking.
exact_symbol_match_rank` and :func:`weld.ranking.amalgamation_boost` -- the
two existing dimensions in the same sort keys that are scoped that way. A
multi-token query already requires every group to hit somewhere, and a
node's own summary carrying one of several required groups is the outcome
ADR 0114 / ADR 0116 deliberately shipped (letting a module's own docstring
make it a first-class OR-fallback competitor), not a bug this module should
undo -- so this dimension stays out of that path entirely.
"""

from __future__ import annotations

from weld._match_surface import match_haystacks


def summary_only_match_demotion(
    node: dict, token_groups: list[list[str]]
) -> int:
    """Return 1 when a single-token query matches *node* only via ``props.summary``.

    Returns 0 (no penalty) whenever:

    * the query has other than exactly one token group -- inert outside the
      strict-AND single-token path; see the module docstring for why;
    * ``props.summary`` is empty or absent -- nothing to strip, so the
      question this asks does not arise;
    * the node still matches once ``props.summary`` is blanked -- independent
      support from another match-surface field (id, label, file, qualname,
      exports, constants, headings, keywords, description).

    Returns 1 -- sorts after every node with independent support, the same
    0-beats-1 shape :func:`weld._test_paths.test_noise_demotion` and
    :func:`weld._issue_concepts.issue_concept_demotion` already use -- only
    when the node would drop out of candidacy entirely without its own
    summary. A pure re-rank: the node is never excluded, only sorted behind
    peers that carry the query's one token on firmer ground.

    *node* is expected to already carry its ``id`` key merged in (the
    ``node_with_id`` shape both call sites -- :func:`weld.ranking.
    rank_query_matches` and :func:`weld._rank_strict_and.strict_and_sort_key`
    -- already build for every other dimension in the same sort key).
    """
    if len(token_groups) != 1:
        return 0
    props = node.get("props") or {}
    summary = str(props.get("summary") or "")
    if not summary:
        return 0
    stripped = {**node, "props": {**props, "summary": ""}}
    haystacks = match_haystacks(node.get("id", ""), stripped)
    group = token_groups[0]
    if any(token in haystack for token in group for haystack in haystacks):
        return 0
    return 1


__all__ = ["summary_only_match_demotion"]
