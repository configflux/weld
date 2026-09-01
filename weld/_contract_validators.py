"""Node and ``meta`` validators for the connected structure metadata contract.

Extracted from ``weld.contract`` to keep the vocabulary constants module
under the 400-line default, and since split again: edges live in
:mod:`weld._contract_edge_validators` and the whole-document aggregators in
:mod:`weld._graph_doc_validators`. All of them are re-exported from
``weld.contract``, which stays the import path callers use.
"""
from __future__ import annotations

from weld._validate_diagnostics import (
    REGEN_HINT as _REGEN_HINT,
    missing_node_field_hint as _missing_node_hint,
    vocab_hint as _vocab_hint,
)
from weld._contract_types import (
    AUTHORITY_VALUES,
    BOUNDARY_KIND_VALUES,
    CONFIDENCE_VALUES,
    DOC_KIND_VALUES,
    PROTOCOL_TRANSPORT_COMPATIBILITY,
    PROTOCOL_VALUES,
    ROLE_VALUES,
    SCHEMA_VERSION,
    SECTION_KIND_VALUES,
    SURFACE_KIND_VALUES,
    TRANSPORT_VALUES,
    VALID_NODE_TYPES,
    ValidationError,
)

# Map prop name -> (allowed values frozenset, display name)
_VOCAB_PROPS: dict[str, tuple[frozenset[str], str]] = {
    "authority": (AUTHORITY_VALUES, "authority"),
    "confidence": (CONFIDENCE_VALUES, "confidence"),
    "doc_kind": (DOC_KIND_VALUES, "doc_kind"),
    "section_kind": (SECTION_KIND_VALUES, "section_kind"),
    "protocol": (PROTOCOL_VALUES, "protocol"),
    "surface_kind": (SURFACE_KIND_VALUES, "surface_kind"),
    "transport": (TRANSPORT_VALUES, "transport"),
    "boundary_kind": (BOUNDARY_KIND_VALUES, "boundary_kind"),
}

# Interaction-surface props that must be non-empty strings before the
# closed-vocabulary check runs.  Omission is preferred over guessing.
_INTERACTION_STRING_PROPS: tuple[str, ...] = (
    "protocol", "surface_kind", "transport", "boundary_kind",
)


def _prefix(source_label: str | None, path: str) -> str:
    """Prefix *path* with *source_label* when provided."""
    if source_label is None:
        return path
    return f"{source_label}:{path}"


def _check_nonempty_string(
    props: dict, key: str, path: str, errors: list[ValidationError],
) -> bool:
    """Validate that *key* in *props* is a non-empty string when present.

    Returns True if the value is bad (non-string or empty) so callers can
    skip subsequent vocabulary checks on the same prop.
    """
    if key not in props:
        return False
    value = props[key]
    if not isinstance(value, str):
        errors.append(ValidationError(
            path, f"props.{key}",
            f"must be a string (got {type(value).__name__}); "
            f"omit the prop instead of guessing",
        ))
        return True
    if value == "":
        errors.append(ValidationError(
            path, f"props.{key}",
            "must not be empty; omit the prop instead of guessing",
        ))
        return True
    return False


def validate_meta(meta: dict) -> list[ValidationError]:
    """Validate the graph meta block."""
    errors: list[ValidationError] = []
    if "version" not in meta:
        errors.append(ValidationError(
            "meta", "version", "required field missing", hint=_REGEN_HINT,
        ))
    elif not isinstance(meta["version"], int):
        errors.append(ValidationError(
            "meta", "version", "must be an integer",
            hint=(
                f"found {type(meta['version']).__name__} "
                f"{meta['version']!r}; expected integer {SCHEMA_VERSION}. "
                f"{_REGEN_HINT}"
            ),
        ))
    elif meta["version"] != SCHEMA_VERSION:
        errors.append(ValidationError(
            "meta", "version",
            f"unsupported graph schema version {meta['version']}; "
            f"expected {SCHEMA_VERSION}. Run `wd discover --output "
            f".weld/graph.json` to regenerate.",
            hint=_REGEN_HINT,
        ))
    # ADR 0065: ``updated_at`` lives in the ``graph-meta.json`` sidecar, not
    # in ``graph.json``. Readers overlay it back onto the logical meta, but a
    # graph validated without its (gitignored) sidecar -- e.g. a fresh
    # checkout -- legitimately lacks it. So ``updated_at`` is optional here
    # and only type-checked when present.
    if "updated_at" in meta and not isinstance(meta["updated_at"], str):
        errors.append(ValidationError(
            "meta", "updated_at", "must be an ISO-8601 string",
            hint=f"found {type(meta['updated_at']).__name__}; {_REGEN_HINT}",
        ))
    return errors


def _validate_node_props(
    props: dict, path: str, errors: list[ValidationError],
) -> None:
    """Validate optional metadata props on a node."""
    if "source_strategy" in props and not isinstance(props["source_strategy"], str):
        errors.append(ValidationError(path, "props.source_strategy", "must be a string"))

    # Interaction-surface string-type checks (ADR 0086).
    bad: set[str] = set()
    for prop_name in _INTERACTION_STRING_PROPS:
        if _check_nonempty_string(props, prop_name, path, errors):
            bad.add(prop_name)

    # Vocabulary-constrained props.
    for prop_name, (allowed, display) in _VOCAB_PROPS.items():
        if prop_name not in props or prop_name in bad:
            continue
        if props[prop_name] not in allowed:
            errors.append(ValidationError(
                path, f"props.{display}",
                f"invalid {display}: {props[prop_name]!r}; valid: {sorted(allowed)}",
            ))

    # Protocol/transport coherence.
    protocol = props.get("protocol")
    transport = props.get("transport")
    if (
        isinstance(protocol, str) and protocol in PROTOCOL_VALUES
        and isinstance(transport, str) and transport in TRANSPORT_VALUES
        and "protocol" not in bad and "transport" not in bad
    ):
        ok = PROTOCOL_TRANSPORT_COMPATIBILITY.get(protocol, frozenset())
        if transport not in ok:
            errors.append(ValidationError(
                path, "props.transport",
                f"transport {transport!r} is not compatible with "
                f"protocol {protocol!r}; valid transports for "
                f"{protocol!r}: {sorted(ok)}. "
                f"Per ADR 0086, omit the prop instead of guessing.",
            ))

    if "roles" in props:
        roles = props["roles"]
        if not isinstance(roles, list):
            errors.append(ValidationError(path, "props.roles", "must be a list of strings"))
        else:
            for role in roles:
                if role not in ROLE_VALUES:
                    errors.append(ValidationError(
                        path, "props.roles",
                        f"invalid role: {role!r}; valid: {sorted(ROLE_VALUES)}",
                    ))

    if "file" in props and not isinstance(props["file"], str):
        errors.append(ValidationError(path, "props.file", "must be a string"))

    _check_nonempty_string(props, "declared_in", path, errors)

    if "span" in props:
        span = props["span"]
        if not isinstance(span, dict):
            errors.append(ValidationError(path, "props.span", "must be a dict"))
        elif "start_line" not in span or "end_line" not in span:
            errors.append(ValidationError(
                path, "props.span", "must contain both start_line and end_line",
            ))
        elif not isinstance(span["start_line"], int) or not isinstance(span["end_line"], int):
            errors.append(ValidationError(
                path, "props.span", "start_line and end_line must be integers",
            ))
        elif span["start_line"] > span["end_line"]:
            errors.append(ValidationError(
                path, "props.span",
                f"start_line ({span['start_line']}) > end_line ({span['end_line']})",
            ))


def validate_node(
    node_id: str,
    node: dict,
    *,
    source_label: str | None = None,
) -> list[ValidationError]:
    """Validate a single node definition.

    *source_label* is an optional producer label prefixed onto every
    diagnostic path (tracked project).
    """
    errors: list[ValidationError] = []
    path = _prefix(source_label, f"nodes.{node_id}")

    if "type" not in node:
        errors.append(ValidationError(
            path, "type", "required field missing",
            hint=_missing_node_hint(node_id, "type"),
        ))
    elif node["type"] not in VALID_NODE_TYPES:
        errors.append(ValidationError(
            path, "type",
            f"invalid node type: {node['type']!r} on node {node_id!r}",
            hint=_vocab_hint(
                node["type"], VALID_NODE_TYPES, label="node type",
            ),
        ))

    if "label" not in node:
        errors.append(ValidationError(
            path, "label", "required field missing",
            hint=_missing_node_hint(node_id, "label"),
        ))

    if "props" not in node:
        errors.append(ValidationError(
            path, "props", "required field missing",
            hint=_missing_node_hint(node_id, "props"),
        ))
        return errors

    props = node["props"]
    if not isinstance(props, dict):
        errors.append(ValidationError(
            path, "props",
            f"must be a dict (got {type(props).__name__})",
            hint=(
                f"node {node_id!r} has a non-dict `props`; replace with a "
                f"JSON object, e.g. `{{}}` when there are no properties"
            ),
        ))
        return errors

    _validate_node_props(props, path, errors)
    return errors


# Edge validators live in ``_contract_edge_validators`` and the top-level
# graph / fragment validators in ``_graph_doc_validators``, so all three
# files stay under the 400-line cap. ``weld.contract`` re-exports every
# public name from all three, so ``from weld.contract import validate_edge,
# validate_graph, validate_fragment`` is unchanged.
