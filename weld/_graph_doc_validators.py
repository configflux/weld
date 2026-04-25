"""Top-level graph document validators (``validate_graph`` / ``validate_fragment``).

Split out of :mod:`weld._contract_validators` to keep both modules under the
400-line default. Node/edge/meta validators remain in the sibling module;
this file only aggregates them across a graph document or a strategy
fragment. Public names are re-exported via :mod:`weld.contract`.
"""
from __future__ import annotations

from weld._contract_validators import (
    validate_edge,
    validate_meta,
    validate_node,
)
from weld._federation_validate import ROOT_FEDERATED_SCHEMA_VERSION
from weld._validate_diagnostics import (
    REGEN_HINT as _REGEN_HINT,
    missing_top_level_hint as _missing_top_hint,
)
from weld.contract import ValidationError


def _is_federation_root(graph: dict) -> bool:
    """Return True iff *graph* advertises the federation root schema.

    Federation-only constructs (``\\x1f``-bearing IDs, ``cross_repo:``
    edge types) are bypassed by ``validate_edge`` only when this is
    True. The check insists on an *integer* schema_version equal to
    :data:`ROOT_FEDERATED_SCHEMA_VERSION` (currently ``2``); a
    non-integer or unexpected value disables the bypass so the standard
    diagnostics still name the offending node (bd-5038-6zm).
    """
    meta = graph.get("meta")
    if not isinstance(meta, dict):
        return False
    sv = meta.get("schema_version")
    return isinstance(sv, int) and sv == ROOT_FEDERATED_SCHEMA_VERSION


def validate_graph(graph: dict) -> list[ValidationError]:
    """Validate an entire graph document."""
    errors: list[ValidationError] = []

    if "meta" not in graph:
        errors.append(ValidationError(
            "graph", "meta", "required field missing",
            hint=_missing_top_hint("meta"),
        ))
    else:
        errors.extend(validate_meta(graph["meta"]))

    federation = _is_federation_root(graph)

    _check_top_level_container(
        graph, "nodes", dict, "a dict", errors,
        "top-level `nodes` must map node_id -> node object. " + _REGEN_HINT,
    )
    if isinstance(graph.get("nodes"), dict):
        for node_id, node in graph["nodes"].items():
            errors.extend(validate_node(node_id, node))

    _check_top_level_container(
        graph, "edges", list, "a list", errors,
        "top-level `edges` must be a JSON array of edge objects. "
        + _REGEN_HINT,
    )
    if isinstance(graph.get("edges"), list):
        nids = (
            set(graph["nodes"].keys())
            if isinstance(graph.get("nodes"), dict) else set()
        )
        for edge in graph["edges"]:
            errors.extend(validate_edge(edge, nids, federation=federation))

    return errors


def _check_top_level_container(
    graph: dict, field: str, expected_type: type, expected_label: str,
    errors: list[ValidationError], type_hint: str,
) -> None:
    """Emit a missing/wrong-type error for a top-level graph field."""
    if field not in graph:
        errors.append(ValidationError(
            "graph", field, "required field missing",
            hint=_missing_top_hint(field),
        ))
    elif not isinstance(graph[field], expected_type):
        errors.append(ValidationError(
            "graph", field,
            f"must be {expected_label} (got {type(graph[field]).__name__})",
            hint=type_hint,
        ))


def validate_fragment(
    fragment: dict,
    *,
    source_label: str = "fragment",
    allow_dangling_edges: bool = False,
) -> list[ValidationError]:
    """Validate a graph fragment (strategy output, topology, or adapter).

    Fragments have ``nodes`` and ``edges`` but no ``meta`` block.
    *source_label* is embedded in error paths for actionable diagnostics.
    *allow_dangling_edges* skips referential-integrity checks.
    """
    errors: list[ValidationError] = []

    if not isinstance(fragment, dict):
        errors.append(ValidationError(source_label, "fragment", "must be a dict"))
        return errors

    if "nodes" not in fragment:
        errors.append(ValidationError(source_label, "nodes", "required field missing"))
    elif not isinstance(fragment["nodes"], dict):
        errors.append(ValidationError(source_label, "nodes", "must be a dict"))
    else:
        for node_id, node in fragment["nodes"].items():
            errors.extend(validate_node(node_id, node, source_label=source_label))

    if "edges" not in fragment:
        errors.append(ValidationError(source_label, "edges", "required field missing"))
    elif not isinstance(fragment["edges"], list):
        errors.append(ValidationError(source_label, "edges", "must be a list"))
    else:
        nids = (
            set(fragment["nodes"].keys())
            if isinstance(fragment.get("nodes"), dict)
            else set()
        )
        for edge in fragment["edges"]:
            errors.extend(validate_edge(
                edge, nids,
                check_refs=not allow_dangling_edges,
                source_label=source_label,
            ))

    if "discovered_from" in fragment:
        df = fragment["discovered_from"]
        if not isinstance(df, list):
            errors.append(ValidationError(source_label, "discovered_from", "must be a list"))
        else:
            for i, entry in enumerate(df):
                if not isinstance(entry, str):
                    errors.append(ValidationError(
                        source_label, "discovered_from",
                        f"entry [{i}] must be a string, got {type(entry).__name__}",
                    ))

    return errors
