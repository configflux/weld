"""Canonical origin classifier for graph nodes (ADR 0042).

A single ``classify_node`` predicate maps every ``symbol`` / ``file`` /
``module`` / ``package`` node to exactly one of four origin values:
``project``, ``stdlib``, ``external``, or ``unresolved``. Strategies
own the classification at emission time and stamp ``props.origin``
directly; ``classify_node`` is a thin reader that returns that field
verbatim when it is one of the four valid values.

An earlier revision of this module shipped a transitional legacy-graph
derivation that read ``authority``, ``resolved``, the
``symbol:unresolved:`` id prefix, and edge-side ``props.resolution``
to classify nodes that predated the ADR. Once every shipped strategy
stamps origin at emission time, that derivation became dead code and
was removed. A node that arrives without a valid ``props.origin`` tag
is now the symptom of either a strategy that has not yet shipped
origin tagging or a hand-crafted graph snapshot; in both cases the
function returns ``"unresolved"`` so the gap surfaces in viz / ranking
/ brief instead of being silently masked as ``"project"`` or
``"external"``.

See ``docs/adrs/0042-graph-node-origin.md`` for the full taxonomy and
per-language detection rules. The function is pure: no I/O, no graph
traversal, no logging.
"""

from __future__ import annotations

from typing import Any, Dict, Literal

#: Origin literal type alias for callers (ADR 0042 §Decision).
Origin = Literal["project", "stdlib", "external", "unresolved"]

#: The exhaustive, mutually exclusive set of origin values. Adding a
#: fifth value requires amending ADR 0042.
ORIGINS: tuple[Origin, ...] = ("project", "stdlib", "external", "unresolved")

_ORIGIN_SET: frozenset[str] = frozenset(ORIGINS)


def classify_node(node: Dict[str, Any]) -> Origin:
    """Return the origin of *node* per ADR 0042.

    Reads ``node["props"]["origin"]`` and returns it verbatim when it
    is one of the four :data:`ORIGINS` values. Returns ``"unresolved"``
    when the field is missing, non-string, or carries an out-of-vocabulary
    value. The function is total: every input produces exactly one of the
    four :data:`ORIGINS` values.
    """
    props = node.get("props") or {}
    origin = props.get("origin")
    if isinstance(origin, str) and origin in _ORIGIN_SET:
        return origin  # type: ignore[return-value]
    return "unresolved"


__all__ = [
    "ORIGINS",
    "Origin",
    "classify_node",
]
