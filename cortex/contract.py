"""Normalized metadata contract and graph validation for the knowledge graph.

project-f7y.3
"""

from __future__ import annotations

from dataclasses import dataclass

# -- Schema version --------------------------------------------------------
#: v2: ``symbol`` node + ``calls`` edge (ADR cortex/docs/adr/0004).
#: v3: seven ``ros_*`` node types; no new edges (ADR docs/adrs/0016).
#: v4: generalized interaction-surface vocabulary -- ``rpc`` and ``channel``
#:     node types plus optional protocol metadata (``protocol``,
#:     ``surface_kind``, ``transport``, ``boundary_kind``, ``declared_in``);
#:     no new edges (ADR docs/adrs/0018, project-xoq.1.2).
SCHEMA_VERSION: int = 4

VALID_NODE_TYPES = frozenset([
    "service", "package", "entity", "stage", "concept", "doc", "route", "contract", "enum", "file",
    "dockerfile", "compose", "agent", "command", "tool", "workflow", "test-suite", "config",
    "policy", "runbook", "build-target", "test-target", "boundary", "entrypoint", "gate",
    "deploy",
    "symbol",  # function-level callable; ADR 0004.
    # ROS2 vocabulary (ADR 0016): package, interface, node, topic, service, action, parameter.
    "ros_package", "ros_interface", "ros_node",
    "ros_topic", "ros_service", "ros_action", "ros_parameter",
    # Generalized interaction-surface vocabulary (ADR 0018, project-xoq.1.2):
    # ``rpc`` is a request/response or stream method exposed or consumed by
    # a module (HTTP handler, gRPC method, ROS2 service/action). ``channel``
    # is a named pub/sub or stream endpoint (event topic, ROS2 topic, queue).
    "rpc", "channel",
])
VALID_EDGE_TYPES = frozenset([
    "contains", "depends_on", "produces", "consumes", "implements", "documents", "relates_to",
    "responds_with", "accepts", "builds", "orchestrates", "invokes", "configures", "tests",
    "represents", "feeds_into", "enforces", "verifies", "exposes", "governs",
    # Function-level call edge; symbol -> symbol. See ADR 0004.
    "calls",
])

# -- Value vocabularies ----------------------------------------------------
#: canonical | derived | manual | external
AUTHORITY_VALUES: frozenset[str] = frozenset(
    ["canonical", "derived", "manual", "external"]
)
#: definite | inferred | speculative
CONFIDENCE_VALUES: frozenset[str] = frozenset(
    ["definite", "inferred", "speculative"]
)
#: implementation | test | config | doc | build | migration | fixture | script
ROLE_VALUES: frozenset[str] = frozenset(
    ["implementation", "test", "config", "doc", "build",
     "migration", "fixture", "script"]
)
#: adr | policy | runbook | guide | gate | verification
DOC_KIND_VALUES: frozenset[str] = frozenset(
    ["adr", "policy", "runbook", "guide", "gate", "verification"]
)
#: Section-level semantic tags derived from markdown headings.
SECTION_KIND_VALUES: frozenset[str] = frozenset([
    "setup", "configuration", "api-reference", "architecture",
    "troubleshooting", "overview", "deployment", "usage",
    "testing", "migration", "security", "contributing",
])

# -- Interaction-surface metadata (ADR 0018, project-xoq.1.2) --------------
#: Protocol family. ``http``/``grpc``/``event``/``ros2`` are the four
#: Phase 7 families; ``inproc`` covers in-process calls that a strategy
#: chooses to model as an interaction surface.
PROTOCOL_VALUES: frozenset[str] = frozenset(
    ["http", "grpc", "event", "ros2", "inproc"]
)
#: Shape of the interaction as statically declared. ``request_response``
#: covers classic RPC/HTTP calls; ``pub_sub`` covers broadcast channels
#: including ROS2 topics; ``stream`` covers long-lived bidirectional
#: streams; ``one_way`` covers fire-and-forget publishes or commands.
SURFACE_KIND_VALUES: frozenset[str] = frozenset(
    ["request_response", "pub_sub", "stream", "one_way"]
)
#: Transport binding for the surface, when statically knowable.
TRANSPORT_VALUES: frozenset[str] = frozenset(
    ["tcp", "http", "http2", "amqp", "kafka", "mqtt", "ros2_dds", "inproc"]
)
#: Which side of the module boundary the surface sits on.
BOUNDARY_KIND_VALUES: frozenset[str] = frozenset(
    ["inbound", "outbound", "internal"]
)

NODE_OPTIONAL_PROPS: tuple[str, ...] = (
    "source_strategy", "authority", "confidence", "roles", "file", "span",
    "doc_kind", "section_kind",
    # Interaction-surface metadata (ADR 0018).
    "protocol", "surface_kind", "transport", "boundary_kind", "declared_in",
)
EDGE_OPTIONAL_PROPS: tuple[str, ...] = ("source_strategy", "confidence")

#: Allowed ``transport`` values per ``protocol``. Drives the coherence
#: check in :func:`validate_node` (project-xoq.1.3): when a strategy or
#: adapter stamps both props, the pair must be physically plausible
#: per ADR 0018's static-truth policy. Omission of either prop skips
#: the check -- partial coverage is honest.
PROTOCOL_TRANSPORT_COMPATIBILITY: dict[str, frozenset[str]] = {
    "http": frozenset(["http", "http2", "tcp"]),
    "grpc": frozenset(["http2", "tcp"]),
    "event": frozenset(["amqp", "kafka", "mqtt", "tcp", "inproc"]),
    "ros2": frozenset(["ros2_dds"]),
    "inproc": frozenset(["inproc"]),
}

# -- Validation error ------------------------------------------------------
@dataclass(frozen=True)
class ValidationError:
    """A single validation finding."""
    path: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}.{self.field}: {self.message}"

# -- Validators ------------------------------------------------------------

# Map prop name -> (allowed values frozenset, display name)
_VOCAB_PROPS: dict[str, tuple[frozenset[str], str]] = {
    "authority": (AUTHORITY_VALUES, "authority"),
    "confidence": (CONFIDENCE_VALUES, "confidence"),
    "doc_kind": (DOC_KIND_VALUES, "doc_kind"),
    "section_kind": (SECTION_KIND_VALUES, "section_kind"),
    # Interaction-surface metadata (ADR 0018, project-xoq.1.2). These are
    # optional on every node type, not just ``rpc``/``channel`` -- an HTTP
    # route modelled as ``route`` can also carry ``protocol="http"`` once
    # extractors begin stamping it.
    "protocol": (PROTOCOL_VALUES, "protocol"),
    "surface_kind": (SURFACE_KIND_VALUES, "surface_kind"),
    "transport": (TRANSPORT_VALUES, "transport"),
    "boundary_kind": (BOUNDARY_KIND_VALUES, "boundary_kind"),
}

#: Interaction-surface props that must be non-empty strings before the
#: closed-vocabulary check runs (project-xoq.1.3). Per ADR 0018,
#: omission is preferred over guessing -- empty strings are guessing.
_INTERACTION_STRING_PROPS: tuple[str, ...] = (
    "protocol", "surface_kind", "transport", "boundary_kind",
)

def _prefix(source_label: str | None, path: str) -> str:
    """Prefix *path* with *source_label* when provided.

    Used so that diagnostics from bundled strategies and external
    adapters surface *which* producer emitted the bad value. Callers
    that operate on whole graphs (``validate_graph``) pass
    ``source_label=None`` to keep legacy paths untouched.
    """
    if source_label is None:
        return path
    return f"{source_label}:{path}"

def validate_meta(meta: dict) -> list[ValidationError]:
    """Validate the graph meta block."""
    errors: list[ValidationError] = []

    if "version" not in meta:
        errors.append(ValidationError("meta", "version", "required field missing"))
    elif not isinstance(meta["version"], int):
        errors.append(ValidationError("meta", "version", "must be an integer"))
    elif meta["version"] != SCHEMA_VERSION:
        errors.append(
            ValidationError(
                "meta",
                "version",
                (
                    f"unsupported graph schema version {meta['version']}; "
                    f"expected {SCHEMA_VERSION}. Run `cortex discover > "
                    f".cortex/graph.json` to regenerate."
                ),
            )
        )

    if "updated_at" not in meta:
        errors.append(ValidationError("meta", "updated_at", "required field missing"))
    elif not isinstance(meta["updated_at"], str):
        errors.append(ValidationError("meta", "updated_at", "must be an ISO-8601 string"))

    return errors

def validate_node(
    node_id: str,
    node: dict,
    *,
    source_label: str | None = None,
) -> list[ValidationError]:
    """Validate a single node definition.

    *source_label* is an optional producer label (e.g.
    ``"strategy:grpc_proto"`` or ``"adapter:external_json"``) prefixed
    onto every diagnostic ``path``. Fragment and graph callers pass it
    through so bundled strategies and external adapters get actionable
    diagnostics that identify which producer emitted the bad value
    (project-xoq.1.3).
    """
    errors: list[ValidationError] = []
    path = _prefix(source_label, f"nodes.{node_id}")

    # Required fields
    if "type" not in node:
        errors.append(ValidationError(path, "type", "required field missing"))
    elif node["type"] not in VALID_NODE_TYPES:
        errors.append(
            ValidationError(path, "type", f"invalid node type: {node['type']}")
        )

    if "label" not in node:
        errors.append(ValidationError(path, "label", "required field missing"))

    if "props" not in node:
        errors.append(ValidationError(path, "props", "required field missing"))
        return errors  # cannot validate props if missing

    props = node["props"]
    if not isinstance(props, dict):
        errors.append(ValidationError(path, "props", "must be a dict"))
        return errors

    # --- Optional metadata validation (omission is fine; wrong values are not) ---

    if "source_strategy" in props and not isinstance(props["source_strategy"], str):
        errors.append(
            ValidationError(path, "props.source_strategy", "must be a string")
        )

    # Interaction-surface metadata (ADR 0018, project-xoq.1.3).
    #
    # Type-check every interaction prop *before* the closed-vocabulary
    # check. A non-string value should produce a clear "must be a
    # string (got int)" diagnostic, not a noisy "invalid protocol: 42;
    # valid: [...]" message. Empty strings are also rejected: per ADR
    # 0018 omission is preferred over guessing, and "" is neither.
    # Props that fail type/empty checks are removed from consideration
    # below so the vocabulary loop doesn't double-report them.
    interaction_bad_props: set[str] = set()
    for prop_name in _INTERACTION_STRING_PROPS:
        if prop_name not in props:
            continue
        value = props[prop_name]
        if not isinstance(value, str):
            errors.append(ValidationError(
                path, f"props.{prop_name}",
                f"must be a string (got {type(value).__name__}); "
                f"omit the prop instead of guessing",
            ))
            interaction_bad_props.add(prop_name)
        elif value == "":
            errors.append(ValidationError(
                path, f"props.{prop_name}",
                "must not be empty; omit the prop instead of guessing",
            ))
            interaction_bad_props.add(prop_name)

    # Validate vocabulary-constrained props (authority, confidence,
    # doc_kind, section_kind, protocol, surface_kind, transport,
    # boundary_kind).
    for prop_name, (allowed, display) in _VOCAB_PROPS.items():
        if prop_name not in props or prop_name in interaction_bad_props:
            continue
        if props[prop_name] not in allowed:
            errors.append(ValidationError(
                path, f"props.{display}",
                f"invalid {display}: {props[prop_name]!r}; valid: {sorted(allowed)}",
            ))

    # Protocol/transport coherence (ADR 0018, project-xoq.1.3).
    #
    # When both props are present *and* individually valid, the pair
    # must be physically plausible. Fail loudly on incoherent pairs
    # (e.g. ``protocol=ros2`` with ``transport=kafka``) so the
    # producing strategy either drops the guessed prop or fixes the
    # mapping. Omission of either prop skips the check.
    protocol = props.get("protocol")
    transport = props.get("transport")
    if (
        isinstance(protocol, str) and protocol in PROTOCOL_VALUES
        and isinstance(transport, str) and transport in TRANSPORT_VALUES
        and "protocol" not in interaction_bad_props
        and "transport" not in interaction_bad_props
    ):
        allowed_transports = PROTOCOL_TRANSPORT_COMPATIBILITY.get(
            protocol, frozenset()
        )
        if transport not in allowed_transports:
            errors.append(ValidationError(
                path, "props.transport",
                f"transport {transport!r} is not compatible with "
                f"protocol {protocol!r}; valid transports for "
                f"{protocol!r}: {sorted(allowed_transports)}. "
                f"Per ADR 0018, omit the prop instead of guessing.",
            ))

    if "roles" in props:
        roles = props["roles"]
        if not isinstance(roles, list):
            errors.append(
                ValidationError(path, "props.roles", "must be a list of strings")
            )
        else:
            for role in roles:
                if role not in ROLE_VALUES:
                    errors.append(ValidationError(
                        path, "props.roles",
                        f"invalid role: {role!r}; valid: {sorted(ROLE_VALUES)}",
                    ))

    if "file" in props and not isinstance(props["file"], str):
        errors.append(ValidationError(path, "props.file", "must be a string"))

    # ``declared_in`` is a pointer to the declarative source (IDL, manifest,
    # schema file) that declares an interaction surface. Per ADR 0018 it is
    # always a repo-relative path -- no runtime URIs. Empty strings are
    # also rejected (project-xoq.1.3): omission beats guessing.
    if "declared_in" in props:
        declared_in = props["declared_in"]
        if not isinstance(declared_in, str):
            errors.append(ValidationError(
                path, "props.declared_in",
                f"must be a string (got {type(declared_in).__name__}); "
                f"omit the prop instead of guessing",
            ))
        elif declared_in == "":
            errors.append(ValidationError(
                path, "props.declared_in",
                "must not be empty; omit the prop instead of guessing",
            ))

    if "span" in props:
        span = props["span"]
        if not isinstance(span, dict):
            errors.append(ValidationError(path, "props.span", "must be a dict"))
        else:
            if "start_line" not in span or "end_line" not in span:
                errors.append(
                    ValidationError(
                        path,
                        "props.span",
                        "must contain both start_line and end_line",
                    )
                )
            elif not isinstance(span["start_line"], int) or not isinstance(
                span["end_line"], int
            ):
                errors.append(
                    ValidationError(
                        path, "props.span", "start_line and end_line must be integers"
                    )
                )
            elif span["start_line"] > span["end_line"]:
                errors.append(
                    ValidationError(
                        path,
                        "props.span",
                        f"start_line ({span['start_line']}) > end_line ({span['end_line']})",
                    )
                )

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
    When *check_refs* is False, referential-integrity checks are skipped
    (useful for fragments that intentionally reference external nodes).

    *source_label* is an optional producer label prefixed onto every
    diagnostic path so bundled strategies and external adapters get
    actionable diagnostics (project-xoq.1.3).
    """
    errors: list[ValidationError] = []
    # Build a path label from edge content when available
    from_id = edge.get("from", "?")
    to_id = edge.get("to", "?")
    path = _prefix(source_label, f"edges[{from_id}->{to_id}]")

    # Required fields
    if "from" not in edge:
        errors.append(ValidationError(path, "from", "required field missing"))
    elif check_refs and from_id not in node_ids:
        errors.append(
            ValidationError(path, "from", f"dangling reference: {from_id}")
        )

    if "to" not in edge:
        errors.append(ValidationError(path, "to", "required field missing"))
    elif check_refs and to_id not in node_ids:
        errors.append(
            ValidationError(path, "to", f"dangling reference: {to_id}")
        )

    if "type" not in edge:
        errors.append(ValidationError(path, "type", "required field missing"))
    elif edge["type"] not in VALID_EDGE_TYPES:
        errors.append(
            ValidationError(path, "type", f"invalid edge type: {edge['type']}")
        )

    if "props" not in edge:
        errors.append(ValidationError(path, "props", "required field missing"))
    else:
        props = edge["props"]
        if isinstance(props, dict):
            # Optional metadata validation on edge props
            if "source_strategy" in props and not isinstance(
                props["source_strategy"], str
            ):
                errors.append(
                    ValidationError(path, "props.source_strategy", "must be a string")
                )

            if "confidence" in props:
                if props["confidence"] not in CONFIDENCE_VALUES:
                    errors.append(
                        ValidationError(
                            path,
                            "props.confidence",
                            f"invalid confidence: {props['confidence']!r}; "
                            f"valid: {sorted(CONFIDENCE_VALUES)}",
                        )
                    )

    return errors

def validate_graph(graph: dict) -> list[ValidationError]:
    """Validate an entire graph document.

    Checks the meta block, all nodes, and all edges including referential
    integrity.
    """
    errors: list[ValidationError] = []

    # Top-level structure
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
        node_ids = set(graph.get("nodes", {}).keys()) if isinstance(
            graph.get("nodes"), dict
        ) else set()
        for edge in graph["edges"]:
            errors.extend(validate_edge(edge, node_ids))

    return errors

def validate_fragment(
    fragment: dict,
    *,
    source_label: str = "fragment",
    allow_dangling_edges: bool = False,
) -> list[ValidationError]:
    """Validate a graph fragment (strategy output, topology, or adapter).

    Fragments differ from full graphs: they have ``nodes`` and ``edges``
    but no ``meta`` block.  An optional ``discovered_from`` list is
    validated when present.

    *source_label* is embedded in error paths so diagnostics are
    actionable (e.g. ``"strategy:sqlalchemy"`` or ``"topology"``).

    *allow_dangling_edges* skips referential-integrity checks on edge
    endpoints, useful when a fragment intentionally references nodes
    defined in other fragments.
    """
    errors: list[ValidationError] = []

    if not isinstance(fragment, dict):
        errors.append(
            ValidationError(source_label, "fragment", "must be a dict")
        )
        return errors

    # -- nodes --
    if "nodes" not in fragment:
        errors.append(
            ValidationError(source_label, "nodes", "required field missing")
        )
    elif not isinstance(fragment["nodes"], dict):
        errors.append(
            ValidationError(source_label, "nodes", "must be a dict")
        )
    else:
        for node_id, node in fragment["nodes"].items():
            errors.extend(
                validate_node(node_id, node, source_label=source_label)
            )

    # -- edges --
    if "edges" not in fragment:
        errors.append(
            ValidationError(source_label, "edges", "required field missing")
        )
    elif not isinstance(fragment["edges"], list):
        errors.append(
            ValidationError(source_label, "edges", "must be a list")
        )
    else:
        node_ids = (
            set(fragment["nodes"].keys())
            if isinstance(fragment.get("nodes"), dict)
            else set()
        )
        for edge in fragment["edges"]:
            errors.extend(
                validate_edge(
                    edge,
                    node_ids,
                    check_refs=not allow_dangling_edges,
                    source_label=source_label,
                )
            )

    # -- discovered_from (optional) --
    if "discovered_from" in fragment:
        df = fragment["discovered_from"]
        if not isinstance(df, list):
            errors.append(
                ValidationError(
                    source_label, "discovered_from", "must be a list"
                )
            )
        else:
            for i, entry in enumerate(df):
                if not isinstance(entry, str):
                    errors.append(
                        ValidationError(
                            source_label,
                            "discovered_from",
                            f"entry [{i}] must be a string, "
                            f"got {type(entry).__name__}",
                        )
                    )

    return errors

