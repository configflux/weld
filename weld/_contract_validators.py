"""Graph validation logic for the connected structure metadata contract.

Contains validators for nodes, edges, graph documents, and fragments.
Extracted from ``weld.contract`` to keep the vocabulary constants module
under the 400-line default.
"""
from __future__ import annotations

from weld.contract import (
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
    VALID_EDGE_TYPES,
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
        errors.append(ValidationError("meta", "version", "required field missing"))
    elif not isinstance(meta["version"], int):
        errors.append(ValidationError("meta", "version", "must be an integer"))
    elif meta["version"] != SCHEMA_VERSION:
        errors.append(ValidationError(
            "meta", "version",
            f"unsupported graph schema version {meta['version']}; "
            f"expected {SCHEMA_VERSION}. Run `wd discover --output "
            f".weld/graph.json` to regenerate.",
        ))
    if "updated_at" not in meta:
        errors.append(ValidationError("meta", "updated_at", "required field missing"))
    elif not isinstance(meta["updated_at"], str):
        errors.append(ValidationError("meta", "updated_at", "must be an ISO-8601 string"))
    return errors


def _validate_node_props(
    props: dict, path: str, errors: list[ValidationError],
) -> None:
    """Validate optional metadata props on a node."""
    if "source_strategy" in props and not isinstance(props["source_strategy"], str):
        errors.append(ValidationError(path, "props.source_strategy", "must be a string"))

    # Interaction-surface string-type checks (ADR 0018).
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
                f"Per ADR 0018, omit the prop instead of guessing.",
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
    diagnostic path (project-xoq.1.3).
    """
    errors: list[ValidationError] = []
    path = _prefix(source_label, f"nodes.{node_id}")

    if "type" not in node:
        errors.append(ValidationError(path, "type", "required field missing"))
    elif node["type"] not in VALID_NODE_TYPES:
        errors.append(ValidationError(path, "type", f"invalid node type: {node['type']}"))

    if "label" not in node:
        errors.append(ValidationError(path, "label", "required field missing"))

    if "props" not in node:
        errors.append(ValidationError(path, "props", "required field missing"))
        return errors

    props = node["props"]
    if not isinstance(props, dict):
        errors.append(ValidationError(path, "props", "must be a dict"))
        return errors

    _validate_node_props(props, path, errors)
    return errors


def validate_edge(
    edge: dict,
    node_ids: set[str],
    *,
    check_refs: bool = True,
    source_label: str | None = None,
) -> list[ValidationError]:
    """Validate a single edge definition.

    *node_ids* is the set of all valid node IDs for referential integrity.
    When *check_refs* is False, referential-integrity checks are skipped.
    *source_label* prefixes diagnostic paths (project-xoq.1.3).
    """
    errors: list[ValidationError] = []
    from_id = edge.get("from", "?")
    to_id = edge.get("to", "?")
    path = _prefix(source_label, f"edges[{from_id}->{to_id}]")

    if "from" not in edge:
        errors.append(ValidationError(path, "from", "required field missing"))
    elif check_refs and from_id not in node_ids:
        errors.append(ValidationError(path, "from", f"dangling reference: {from_id}"))

    if "to" not in edge:
        errors.append(ValidationError(path, "to", "required field missing"))
    elif check_refs and to_id not in node_ids:
        errors.append(ValidationError(path, "to", f"dangling reference: {to_id}"))

    if "type" not in edge:
        errors.append(ValidationError(path, "type", "required field missing"))
    elif edge["type"] not in VALID_EDGE_TYPES:
        errors.append(ValidationError(path, "type", f"invalid edge type: {edge['type']}"))

    if "props" not in edge:
        errors.append(ValidationError(path, "props", "required field missing"))
    else:
        props = edge["props"]
        if isinstance(props, dict):
            if "source_strategy" in props and not isinstance(props["source_strategy"], str):
                errors.append(ValidationError(path, "props.source_strategy", "must be a string"))
            if "confidence" in props and props["confidence"] not in CONFIDENCE_VALUES:
                errors.append(ValidationError(
                    path, "props.confidence",
                    f"invalid confidence: {props['confidence']!r}; "
                    f"valid: {sorted(CONFIDENCE_VALUES)}",
                ))

    return errors


def validate_graph(graph: dict) -> list[ValidationError]:
    """Validate an entire graph document."""
    errors: list[ValidationError] = []

    if "meta" not in graph:
        errors.append(ValidationError("graph", "meta", "required field missing"))
    else:
        errors.extend(validate_meta(graph["meta"]))

    if "nodes" not in graph:
        errors.append(ValidationError("graph", "nodes", "required field missing"))
    elif not isinstance(graph["nodes"], dict):
        errors.append(ValidationError("graph", "nodes", "must be a dict"))
    else:
        for node_id, node in graph["nodes"].items():
            errors.extend(validate_node(node_id, node))

    if "edges" not in graph:
        errors.append(ValidationError("graph", "edges", "required field missing"))
    elif not isinstance(graph["edges"], list):
        errors.append(ValidationError("graph", "edges", "must be a list"))
    else:
        nids = set(graph.get("nodes", {}).keys()) if isinstance(graph.get("nodes"), dict) else set()
        for edge in graph["edges"]:
            errors.extend(validate_edge(edge, nids))

    return errors


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
