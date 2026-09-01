"""Edge validators for the connected structure metadata contract.

Split out of :mod:`weld._contract_validators` -- which holds the node and
meta validators -- so both files stay under the 400-line cap, on the same
rule as the sibling :mod:`weld._graph_doc_validators`. The trigger was
ADR 0137 ss3: endpoint checking stopped being one boolean about an id's
*shape* and became a classification against the ids a federated workspace
actually holds, and that reasoning is exactly what belongs in one file of
its own. Public names are re-exported via :mod:`weld.contract`.
"""
from __future__ import annotations

from weld._contract_types import (
    CONFIDENCE_VALUES,
    VALID_EDGE_TYPES,
    ValidationError,
)
from weld._contract_validators import _prefix
from weld._federation_validate import (
    DANGLING_REF_MESSAGE_PREFIX,
    ENDPOINT_OK,
    ENDPOINT_UNVERIFIABLE,
    FederationIdIndex,
    UNVERIFIABLE_REF_MESSAGE_PREFIX,
    is_well_formed_cross_repo_edge_type as _is_cross_repo_type,
    is_well_formed_federation_id as _is_federation_id,
)
from weld._validate_diagnostics import (
    dangling_ref_hint as _dangling_ref_hint,
    missing_edge_field_hint as _missing_edge_hint,
    unverifiable_ref_hint as _unverifiable_ref_hint,
    vocab_hint as _vocab_hint,
)

__all__ = ["validate_edge"]


def _check_edge_endpoint(
    field_name: str,
    value: object,
    node_ids: set[str],
    path: str,
    errors: list[ValidationError],
    *,
    check_refs: bool,
    federation: bool,
    id_index: FederationIdIndex | None,
) -> None:
    """Referential-integrity check for one edge endpoint (ADR 0137 ss3).

    With an *id_index* the endpoint is classified against the ids the
    workspace actually holds; without one, a well-formed federated id under
    *federation* is accepted on shape alone, which is the only answer a
    caller that cannot enumerate the children can honestly give.
    """
    if not check_refs or value in node_ids:
        return
    if id_index is not None:
        verdict = id_index.classify_endpoint(value)
        if verdict == ENDPOINT_OK:
            return
        if verdict == ENDPOINT_UNVERIFIABLE:
            child = id_index.endpoint_child(value) or "?"
            state = id_index.child_state(child)
            errors.append(ValidationError(
                path, field_name,
                f"{UNVERIFIABLE_REF_MESSAGE_PREFIX} {value!r} "
                f"(child {child!r}: {state})",
                hint=_unverifiable_ref_hint(child, state),
            ))
            return
    elif federation and _is_federation_id(value):
        return
    errors.append(ValidationError(
        path, field_name, f"{DANGLING_REF_MESSAGE_PREFIX} {value!r}",
        hint=_dangling_ref_hint(value, node_ids),
    ))


def validate_edge(
    edge: dict,
    node_ids: set[str],
    *,
    check_refs: bool = True,
    source_label: str | None = None,
    federation: bool = False,
    id_index: FederationIdIndex | None = None,
) -> list[ValidationError]:
    """Validate a single edge definition.

    *node_ids* is the set of all valid node IDs for referential integrity.
    When *check_refs* is False, referential-integrity checks are skipped.
    *source_label* prefixes diagnostic paths (tracked project).
    *federation* gates the cross-repo bypasses (separator-bearing IDs and
    ``cross_repo:<suffix>`` edge types). It is set to True by
    :func:`validate_graph` when the containing graph advertises
    ``meta.schema_version == 2`` (tracked issue). Callers handling
    fragments under federation may pass it explicitly. Even with
    ``federation=True`` the bypass requires a *well-formed* id/prefix --
    pathological strings still fail with a diagnostic naming the offender.
    *id_index* (ADR 0137 ss3) replaces that shape-only endpoint bypass with
    a classification against the ids the workspace holds: a well-formed id
    naming a node no child has is ``dangling``, and one naming a registered
    child whose graph cannot be read is ``unverifiable``. Both are errors --
    cannot-verify is not verified (ADR 0134). The edge-*type* bypass is
    unaffected; it is a vocabulary question, not a reference one.
    """
    errors: list[ValidationError] = []
    from_id = edge.get("from", "?")
    to_id = edge.get("to", "?")
    path = _prefix(source_label, f"edges[{from_id}->{to_id}]")

    for field_name, value in (("from", from_id), ("to", to_id)):
        if field_name not in edge:
            errors.append(ValidationError(
                path, field_name, "required field missing",
                hint=_missing_edge_hint(from_id, to_id, field_name),
            ))
            continue
        _check_edge_endpoint(
            field_name, value, node_ids, path, errors,
            check_refs=check_refs, federation=federation, id_index=id_index,
        )

    if "type" not in edge:
        errors.append(ValidationError(
            path, "type", "required field missing",
            hint=_missing_edge_hint(from_id, to_id, "type"),
        ))
    elif (
        edge["type"] not in VALID_EDGE_TYPES
        and not (federation and _is_cross_repo_type(edge["type"]))
    ):
        errors.append(ValidationError(
            path, "type",
            f"invalid edge type: {edge['type']!r} on edge "
            f"{from_id!r} -> {to_id!r}",
            hint=_vocab_hint(
                edge["type"], VALID_EDGE_TYPES, label="edge type",
            ),
        ))

    if "props" not in edge:
        errors.append(ValidationError(
            path, "props", "required field missing",
            hint=_missing_edge_hint(from_id, to_id, "props"),
        ))
    else:
        _validate_edge_props(edge["props"], path, errors)

    return errors


def _validate_edge_props(
    props: object, path: str, errors: list[ValidationError],
) -> None:
    """Validate edge ``props`` (ignores non-dict to preserve prior behavior)."""
    if not isinstance(props, dict):
        return
    if "source_strategy" in props and not isinstance(props["source_strategy"], str):
        errors.append(ValidationError(
            path, "props.source_strategy", "must be a string",
            hint=(
                "props.source_strategy should be the producing strategy "
                "name, e.g. \"python_callgraph\""
            ),
        ))
    if "confidence" in props and props["confidence"] not in CONFIDENCE_VALUES:
        errors.append(ValidationError(
            path, "props.confidence",
            f"invalid confidence: {props['confidence']!r}; "
            f"valid: {sorted(CONFIDENCE_VALUES)}",
            hint=_vocab_hint(
                props["confidence"], CONFIDENCE_VALUES, label="confidence",
            ),
        ))
