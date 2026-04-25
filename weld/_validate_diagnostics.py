"""Diagnostic-message helpers for the graph validator (bd-5038-eds.3).

Split out of :mod:`weld._contract_validators` to keep the validator module
under the 400-line cap. The helpers here compute *actionable fix hints* --
they never produce :class:`weld.contract.ValidationError` instances
directly. Validators compose these hints by passing the returned string as
the ``hint=`` keyword of :class:`ValidationError`.

Public API:
* :data:`REGEN_HINT` -- canonical wording for "rebuild the graph".
* :func:`suggest_close_matches` -- typo recovery for closed vocabularies.
* :func:`vocab_hint` -- hint text for an invalid closed-vocabulary value.
* :func:`dangling_ref_hint` -- hint text for a dangling edge endpoint.
* :func:`format_validation_report` -- multi-line stderr block for the
  ``wd validate`` / ``wd validate-fragment`` CLI paths.

All names are module-private to the ``weld`` package; they are not part of
the public ``weld.contract`` surface.
"""
from __future__ import annotations

import difflib
from typing import Iterable, Sequence

from weld.contract import ValidationError

# Canonical fix hint shared by any diagnostic whose remedy is to rebuild the
# graph file from source-of-truth discovery (bd-5038-eds.3). Kept as a module
# constant so wording stays stable for docs and test assertions.
REGEN_HINT = (
    "run `wd discover --output .weld/graph.json` to regenerate the graph "
    "from source-of-truth discovery"
)


def suggest_close_matches(
    value: object, allowed: Iterable[str], *, limit: int = 3,
) -> list[str]:
    """Return up to *limit* close-match suggestions from *allowed*.

    Non-string values yield an empty list -- close-match suggestion only
    makes sense for user-supplied string identifiers.
    """
    if not isinstance(value, str):
        return []
    return difflib.get_close_matches(value, list(allowed), n=limit, cutoff=0.6)


def vocab_hint(
    value: object, allowed: Iterable[str], *, label: str,
) -> str:
    """Format a hint for an invalid closed-vocabulary value.

    Prefers listing close matches when available; falls back to the full
    sorted vocabulary so the caller always knows the exact set of accepted
    spellings.
    """
    matches = suggest_close_matches(value, allowed)
    if matches:
        return (
            f"did you mean {matches!r}? "
            f"valid {label}s: {sorted(allowed)}"
        )
    return f"use one of the valid {label}s: {sorted(allowed)}"


def dangling_ref_hint(node_id: object, node_ids: Iterable[str]) -> str:
    """Actionable hint for a dangling edge endpoint.

    Suggests the closest-matching existing node id (typo recovery) or, when
    no match is close enough, tells the user to either add the referenced
    node or remove the edge.
    """
    ids_set = set(node_ids)
    matches = suggest_close_matches(node_id, ids_set)
    if matches:
        return (
            f"no node with id {node_id!r} exists; did you mean "
            f"{matches!r}? Otherwise add the referenced node or remove "
            f"this edge"
        )
    return (
        f"no node with id {node_id!r} exists; add a node with that id "
        f"or remove this edge"
    )


def missing_node_field_hint(node_id: str, field: str) -> str:
    """Actionable hint for a node that is missing a required field.

    Keeps wording per-field so the message is concrete rather than generic.
    """
    if field == "type":
        return (
            f"node {node_id!r} has no `type` field; every node must "
            f"declare one of the valid node types. " + REGEN_HINT
        )
    if field == "label":
        return (
            f"node {node_id!r} has no `label`; add a human-readable "
            f"string, e.g. {{\"label\": \"{node_id}\", ...}}"
        )
    if field == "props":
        return (
            f"node {node_id!r} has no `props`; every node needs a dict "
            f"of properties (use `{{}}` if none)"
        )
    return f"node {node_id!r} is missing required field `{field}`"


def missing_edge_field_hint(from_id: object, to_id: object, field: str) -> str:
    """Actionable hint for an edge that is missing a required field."""
    pair = f"{from_id!r} -> {to_id!r}"
    if field in ("from", "to"):
        return f"every edge must declare a `{field}` node id"
    if field == "type":
        return (
            f"edge {pair} has no `type`; add one of the valid edge types"
        )
    if field == "props":
        return (
            f"edge {pair} has no `props`; use `{{}}` when there are no "
            f"properties"
        )
    return f"edge {pair} is missing required field `{field}`"


def missing_top_level_hint(field: str) -> str:
    """Actionable hint for a graph document that is missing a top-level key."""
    if field == "meta":
        return (
            "the graph document needs a top-level `meta` block with at "
            "least `version` and `updated_at`. " + REGEN_HINT
        )
    if field == "nodes":
        return (
            "the graph document needs a top-level `nodes` object (use "
            "`{}` when empty). " + REGEN_HINT
        )
    if field == "edges":
        return (
            "the graph document needs a top-level `edges` list (use `[]` "
            "when empty). " + REGEN_HINT
        )
    return f"the graph document is missing top-level `{field}`. " + REGEN_HINT


def format_validation_report(
    errors: Sequence[ValidationError], *, source: str,
) -> str:
    """Build a multi-line human-readable stderr block for *errors*.

    *source* is the file path (or synthetic label, e.g. ``"<stdin>"``) the
    validator was pointed at, echoed in the header so CLI users know which
    file failed. Returns the full block (including trailing newline) so the
    caller can write it directly to ``sys.stderr``.

    The block prefers clarity over compactness: one header line, a summary
    count, then one block per error with the location on the first line and
    the hint (when present) indented underneath. Keeping every finding
    anchored to a ``path.field`` makes jump-to-location tooling reliable.
    """
    header = f"{source}: {len(errors)} validation error(s)"
    lines = [header]
    for err in errors:
        lines.append(f"  - {err.path}.{err.field}: {err.message}")
        if err.hint:
            lines.append(f"      hint: {err.hint}")
    return "\n".join(lines) + "\n"
