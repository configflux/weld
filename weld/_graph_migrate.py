"""Graph-migration helpers for ADR 0050 confidence adoption.

The CLI surface is ``wd migrate --add-confidence``. The function in
this module is the in-process entry point so the discovery write
pipeline, the federation aggregator, and unit tests can all reuse the
same backfill logic without re-implementing the per-edge classifier.

Why a helper instead of one-shot in-line code: the same classifier is
called from the CLI (where the report is printed) and from the
discover post-processing path (where filled edges feed straight back
into the in-memory graph). Splitting the shape into a pure function
plus a structured report keeps both call sites honest.
"""

from __future__ import annotations

from dataclasses import dataclass

from weld._confidence_defaults import classify_strategy
from weld.contract import CONFIDENCE_VALUES


@dataclass(frozen=True)
class BackfillReport:
    """Summary counts produced by :func:`backfill_confidence`.

    Attributes
    ----------
    filled:
        Number of edges that were missing ``props.confidence`` and
        received a value from the static map.
    unchanged:
        Number of edges that already carried a valid confidence value
        and were therefore not modified.
    invalid:
        Number of edges that carried a confidence value outside
        :data:`weld.contract.CONFIDENCE_VALUES`. These are *not*
        rewritten -- the operator must adjudicate the underlying
        producer's intent. The count is reported so the operator
        knows there is still manual work to do.
    """

    filled: int = 0
    unchanged: int = 0
    invalid: int = 0

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation."""
        return {
            "filled": self.filled,
            "unchanged": self.unchanged,
            "invalid": self.invalid,
        }


def backfill_confidence(graph: dict) -> BackfillReport:
    """Stamp a default ``confidence`` onto every edge that is missing one.

    Mutates *graph* in place: every edge whose ``props`` dict lacks a
    ``confidence`` key gets one filled in by classifying the edge's
    ``source_strategy`` against
    :data:`weld._confidence_defaults.STRATEGY_DEFAULT_CONFIDENCE`.
    Strategies absent from the map default to ``"speculative"`` per
    ADR 0050.

    The function is idempotent: a second call on the same graph
    rewrites nothing because every edge now carries a valid value.

    Edges that already carry a valid value are not touched -- the
    producing strategy made an explicit choice and the migration
    helper must respect it. Edges with a malformed (non-dict) props
    block are counted under ``invalid`` and left alone; they will fail
    contract validation downstream, where the operator gets a pointed
    diagnostic.
    """
    edges = graph.get("edges")
    if not isinstance(edges, list):
        return BackfillReport()

    filled = 0
    unchanged = 0
    invalid = 0

    for edge in edges:
        if not isinstance(edge, dict):
            invalid += 1
            continue
        props = edge.get("props")
        if not isinstance(props, dict):
            # Leave the malformed props alone; the contract validator
            # will surface this with an actionable diagnostic.
            invalid += 1
            continue
        if "confidence" in props:
            value = props["confidence"]
            if value in CONFIDENCE_VALUES:
                unchanged += 1
            else:
                # Refuse to silently rewrite an out-of-vocab value:
                # the operator must adjudicate.
                invalid += 1
            continue
        # Missing -> classify by source_strategy.
        source_strategy = props.get("source_strategy")
        props["confidence"] = classify_strategy(source_strategy)
        filled += 1

    return BackfillReport(
        filled=filled,
        unchanged=unchanged,
        invalid=invalid,
    )


__all__ = [
    "BackfillReport",
    "backfill_confidence",
]
