"""Static edge-type weights for ``wd agents impact`` and ``plan-change``.

ADR 0030 documents the rationale. Three tiers:

- ``STRONG_WEIGHT`` (5.0) — direct semantic coupling. Editing one asset
  almost always reaches the other (skill use, agent invocation, render
  provenance, tool wiring, event triggers, workflow implementation).
- ``RELATED_WEIGHT`` (2.0) — worth surfacing, not always edited in
  lockstep (path scope, override / duplicate / conflict, platform
  membership, tool restriction). Same-name and same-purpose synthetic
  siblings sit in this tier.
- ``INCIDENTAL_WEIGHT`` (0.5) — free-text or path-mention signal,
  typically a doc cross-reference. ``references_file`` is the only edge
  type explicitly here; unknown types default to this tier.

``SECONDARY_THRESHOLD`` (1.0) is the cutoff applied by
``agent_graph_plan._secondary_assets``: a candidate must aggregate at
least 1.0 of edge weight against the change set to be surfaced.

``passes_secondary_threshold`` carves out one exception to the cutoff:
``status == "canonical"`` assets bypass the weight filter so authority
sources stay visible to the operator even when reachable only through
incidental (text-mention) edges. ADR 0030's audit follow-up captures
the rationale.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# Tier constants -- exposed for callers that want to score synthetic
# siblings (same_name, same_purpose) without duplicating the table.
STRONG_WEIGHT: float = 5.0
RELATED_WEIGHT: float = 2.0
INCIDENTAL_WEIGHT: float = 0.5

# Cutoff for ``_secondary_assets``: assets with weight below this are
# dropped (a single ``references_file`` edge alone is filtered out).
SECONDARY_THRESHOLD: float = 1.0

# Synthetic sibling labels used by inventory; not real edge types but
# carried alongside edge weights so the plan layer can apply the same
# threshold uniformly.
SAME_NAME_LABEL: str = "same_name"
SAME_PURPOSE_LABEL: str = "same_purpose"

_EDGE_WEIGHTS: Mapping[str, float] = {
    # Strong semantic coupling: direct uses, invokes, hand-offs,
    # render provenance, tool wiring, event triggers, workflow links.
    "uses_skill": STRONG_WEIGHT,
    "uses_command": STRONG_WEIGHT,
    "invokes_agent": STRONG_WEIGHT,
    "handoff_to": STRONG_WEIGHT,
    "generated_from": STRONG_WEIGHT,
    "provides_tool": STRONG_WEIGHT,
    "triggers_on_event": STRONG_WEIGHT,
    "implements_workflow": STRONG_WEIGHT,
    # Related but indirect: scope, override, conflict, restriction,
    # platform membership, plus synthetic same_name / same_purpose.
    "applies_to_path": RELATED_WEIGHT,
    "overrides": RELATED_WEIGHT,
    "duplicates": RELATED_WEIGHT,
    "conflicts_with": RELATED_WEIGHT,
    "restricts_tool": RELATED_WEIGHT,
    "part_of_platform": RELATED_WEIGHT,
    SAME_NAME_LABEL: RELATED_WEIGHT,
    SAME_PURPOSE_LABEL: RELATED_WEIGHT,
    # Incidental text-mention: free-text or path reference scraped from
    # bodies. The customer-reported noise lives here.
    "references_file": INCIDENTAL_WEIGHT,
}


def edge_weight(edge_type: str) -> float:
    """Return the static weight for *edge_type*.

    Unknown edge types fall back to ``INCIDENTAL_WEIGHT`` so a future
    schema addition does not silently graduate to "strong" without an
    ADR review.
    """
    return _EDGE_WEIGHTS.get(edge_type, INCIDENTAL_WEIGHT)


def aggregate_weight(edge_types: Iterable[str]) -> float:
    """Return the summed weight of *edge_types* incident to one node."""
    return sum(edge_weight(item) for item in edge_types)


def passes_secondary_threshold(weight: float, status: str) -> bool:
    """Return whether a candidate clears the secondary-asset cutoff.

    Canonical-authority assets bypass the weight filter: hiding the
    authoritative source of a concept when an operator's change set
    even touches it (however incidentally) is a worse failure mode
    than surfacing one extra item. All other assets must clear
    ``SECONDARY_THRESHOLD``.
    """
    if status == "canonical":
        return True
    return weight >= SECONDARY_THRESHOLD
