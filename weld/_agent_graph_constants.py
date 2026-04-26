"""Shared constants for agent-graph description-vagueness checks.

`weld.agent_graph_audit._vague_descriptions` and
`weld._agent_graph_strict.suppressed_vague_findings` sit on the inverse
sides of the same ADR 0029 suppression rule: the audit emits a
``vague_description`` finding for canonical assets that fail the check,
and strict mode emits a matching ``vague_description_suppressed`` finding
for derived/generated rendered copies (where the description is blank by
design). Both must score against the exact same type filter and word
bag, so they live in this single module and are imported from both
call sites.

A change to either set is a deliberate rule edit and only needs to
happen here.
"""

from __future__ import annotations

# Asset types whose descriptions are user-facing enough that we expect
# a precise activation description (vs. e.g. tools/hooks).
_CLEAR_DESCRIPTION_TYPES = {"agent", "skill", "subagent"}

# Single-word descriptions that are too generic to be useful.
_VAGUE_DESCRIPTIONS = {"content", "todo", "tbd", "misc", "general", "helper"}

__all__ = ["_CLEAR_DESCRIPTION_TYPES", "_VAGUE_DESCRIPTIONS"]
