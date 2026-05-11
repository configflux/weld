"""Structured warning helper for missing / invalid edge confidence (ADR 0050).

Centralises the warning shape so every emission site -- ``Graph.add_edge``,
the discovery write path, the federation aggregator -- formats the
diagnostic identically. Tests pin the substring shape (``"[weld] warning:
edge..."``, ``"missing confidence"`` / ``"invalid confidence"``, the
``source_strategy`` token, and the ``edge_type`` token) so a regression
that drops attribution context from the message fails loudly.

The helper is intentionally untyped beyond ``dict``: callers pass the
edge mapping as it sits on the wire, and the helper extracts the fields
it needs. This keeps the surface usable from both the in-memory
``Graph`` API (``Graph.add_edge``) and the post-processing JSON-shape
sites without an intermediate adapter.
"""

from __future__ import annotations

import sys

from weld.contract import CONFIDENCE_VALUES


def edge_confidence_finding(edge: dict) -> str | None:
    """Return a human-readable warning string when *edge* is non-conformant.

    Returns ``None`` when *edge* carries a valid ``confidence`` value
    (the silent-success path). Otherwise returns a single-line string
    suitable for ``print(..., file=sys.stderr)`` that names the
    ``source_strategy`` (or ``"<unset>"`` when absent), the edge type,
    and the from/to ids so a single discovery run produces an
    attributable list of every offending edge.
    """
    props = edge.get("props") if isinstance(edge, dict) else None
    if not isinstance(props, dict):
        # An edge with no props at all violates the existing required-
        # field guard in :func:`weld.contract.validate_edge`. The
        # ADR-0050 warning surface treats it the same as missing
        # confidence: the producer has not declared a stance.
        props = {}

    source_strategy = props.get("source_strategy") or "<unset>"
    edge_type = edge.get("type", "<unknown>")
    from_id = edge.get("from", "?")
    to_id = edge.get("to", "?")

    if "confidence" not in props:
        return (
            f"[weld] warning: edge {from_id!r} -> {to_id!r} "
            f"(type={edge_type!r}, source_strategy={source_strategy!r}) "
            f"is missing confidence; per ADR 0050 the producer must "
            f"stamp 'confidence' as one of "
            f"{sorted(CONFIDENCE_VALUES)}. Run 'wd migrate "
            f"--add-confidence' to backfill an existing graph."
        )

    value = props["confidence"]
    if value not in CONFIDENCE_VALUES:
        return (
            f"[weld] warning: edge {from_id!r} -> {to_id!r} "
            f"(type={edge_type!r}, source_strategy={source_strategy!r}) "
            f"has invalid confidence: {value!r}; valid values are "
            f"{sorted(CONFIDENCE_VALUES)}."
        )

    return None


def warn_edge_confidence(edge: dict) -> bool:
    """Print the warning for *edge* to stderr when non-conformant.

    Returns ``True`` when a warning was emitted, ``False`` otherwise.
    Callers that want to suppress the warning (the migration helper, for
    instance, prefers a single aggregate report at the end) should use
    :func:`edge_confidence_finding` directly and route the string
    themselves.
    """
    finding = edge_confidence_finding(edge)
    if finding is None:
        return False
    print(finding, file=sys.stderr)
    return True


__all__ = [
    "edge_confidence_finding",
    "warn_edge_confidence",
]
