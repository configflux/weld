"""Evidence-preserving node selection for the discover orchestrator (ADR 0103).

``weld.discover`` folds one ``StrategyResult`` per source entry into a single
node table. It used to do that with ``dict.update`` -- last-writer-wins, with
no regard for what either side knows. Two source entries do claim the same
node ID: ``python_callgraph`` mints an evidence-free stub for a call target
defined outside the glob it is currently extracting (no ``file``, no ``line``,
no ``kind``, ``confidence: speculative``), because a single-glob extract cannot
know where that callee is defined. Minting it is right -- it keeps the graph
referentially closed. Letting it overwrite the real definition another source
entry already walked is not: the definition's ``props.file`` is what
``graph_closure`` anchors ``contains`` edges on, so the symbol lost its file,
its line, its kind and its containment (bd 4ux4).

The rule here is a veto, not a merge algebra: the incoming claim still wins by
default, and only loses when it is *strictly* worse evidenced than the claim
already recorded for that ID. ADR 0103 § "Why a selection rule and not a merge"
covers why ADR 0041's commutative ``ensure_node`` merge was not used instead --
its order-independence would rewrite the deliberate later-batch-wins ordering
``weld.init`` relies on (ADR 0071).

Rejecting a stub never dangles an edge: the rejection only happens when a node
with that exact ID is already present, so every endpoint the stub was minted to
guarantee still resolves. :func:`incremental_claim_wins` is what makes that
sentence true on the incremental path too -- see its docstring.
"""

from __future__ import annotations

from typing import Any, Collection, Optional

#: Confidence ordering, lower rank = better evidenced. The vocabulary is
#: :data:`weld.contract.CONFIDENCE_VALUES` (a frozenset, so it carries no
#: order) and the ordering is the one :data:`weld.ranking.CONFIDENCE_RANK`
#: applies to retrieval. It is restated rather than imported so discovery does
#: not depend on the retrieval layer for a three-entry table; the two are
#: pinned equal by ``discover_node_merge_test`` so they cannot drift.
_CONFIDENCE_RANK: dict[str, int] = {
    "definite": 0,
    "inferred": 1,
    "speculative": 2,
}


def _confidence_rank(node: Optional[dict[str, Any]]) -> Optional[int]:
    """Return the confidence rank of *node*, or ``None`` when it states none.

    ``None`` means "this side did not make a comparable claim" -- an absent
    ``props``, a missing ``confidence``, or a value outside the vocabulary.
    Callers fall back to last-writer-wins in that case rather than inventing a
    rank for it.
    """
    if not isinstance(node, dict):
        return None
    props = node.get("props")
    if not isinstance(props, dict):
        return None
    confidence = props.get("confidence")
    if not isinstance(confidence, str):
        return None
    return _CONFIDENCE_RANK.get(confidence)


def claim_supersedes(
    existing: Optional[dict[str, Any]],
    incoming: dict[str, Any],
) -> bool:
    """Return True when *incoming* may take the node ID *existing* holds.

    True when there is no existing claim, when either side states no
    comparable confidence (preserving the historical last-writer-wins
    behaviour for every collision this rule was not written for), or when
    *incoming* is at least as well evidenced as *existing*.

    False in exactly one case: both sides state a known confidence and the
    incoming one is strictly weaker. That is the case where accepting the
    write would discard proven facts for unproven ones.
    """
    if existing is None:
        return True
    existing_rank = _confidence_rank(existing)
    incoming_rank = _confidence_rank(incoming)
    if existing_rank is None or incoming_rank is None:
        return True
    return incoming_rank <= existing_rank


def incremental_claim_wins(
    existing: Optional[dict[str, Any]],
    incoming: dict[str, Any],
    dirty: Collection[str],
) -> bool:
    """Return True when the incremental merge may write *incoming* (bd n0p2).

    The incremental orchestrator re-runs only the source entries that own a
    dirty file, so on top of the ADR 0103 veto above it has a second question
    to answer: may a re-run source speak about a file that did *not* change?

    It may not *overwrite* one. In a full run every source entry claims its
    node IDs and the ordering decides the winner; incrementally the clean
    entries never run, so letting a re-run entry install its version of a clean
    file's node would install an early entry's claim where a full run kept a
    later entry's. That is the guard this rule has carried since incremental
    discovery landed, and it is unchanged.

    It may *introduce* one, and that is the fix. The guard used to be a single
    drop gate, so it also discarded a claim on a node ID the graph did not hold
    at all -- which is not a clobber, it is the only chance that node has to
    exist. ``validator_targets`` mints a ``file:`` stub for the export-less
    ``__init__.py`` a lint governs; editing the lint marks the lint dirty and
    the ``__init__.py`` clean, so the stub was dropped on every incremental run
    and the ``validates`` edge that needed it dangled and was pruned. The node
    appeared only under ``--full``, which is exactly the incremental-vs-full
    divergence ADR 0008 forbids.

    Admitting it cannot reintroduce that divergence. If the ID is absent after
    the stale purge and its file is clean, no clean entry claims it: a clean
    entry sees identical inputs (its files are unchanged, and a changed config
    is refused as an incremental basis outright), so it emits what it emitted
    on the run that built this graph -- had it claimed the ID, the ID would be
    in the graph, and the purge could not have taken it, because the purge keys
    on ``props.file`` and this file is clean. So every claimant of an absent ID
    is a re-run entry, and the re-run entries iterate in the same relative
    order and under the same veto as they would in a full run.

    ``props.file`` is read as defensively as ``confidence`` is above, and for
    the same reason: props reach here from strategy plugins, including the
    project-local ones a repository drops into ``.weld/strategies/``. A missing
    or non-dict ``props``, or a ``file`` that is not a string, means this side
    named no file to test against the dirty set -- so it falls through to the
    veto rather than raising. Read naively, ``props: None`` raises
    ``AttributeError`` and an unhashable ``file`` raises ``TypeError`` out of
    the set membership test, and either one takes down the whole run.
    """
    if existing is None:
        return True
    props = incoming.get("props")
    node_file = props.get("file") if isinstance(props, dict) else None
    if isinstance(node_file, str) and node_file and node_file not in dirty:
        return False
    return claim_supersedes(existing, incoming)


__all__ = ["claim_supersedes", "incremental_claim_wins"]
