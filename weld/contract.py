"""Normalized metadata contract and graph validation for the connected structure.

project-f7y.3
"""

from __future__ import annotations

from dataclasses import dataclass

# -- Schema version --------------------------------------------------------
#: v2: ``symbol`` node + ``calls`` edge (ADR weld/docs/adr/0004).
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
    # Polyrepo federation (ADR 0011 ss4, ss7): one ``repo:<name>`` node per
    # registered child in the root meta-graph. Carries path metadata
    # (``path``, ``path_segments``, ``depth``, ``tags``) and is emitted
    # exclusively by the root discovery branch. Presence of any ``repo:*``
    # node triggers ``meta.schema_version = 2`` on save (ADR 0012 ss4).
    "repo",
])
VALID_EDGE_TYPES = frozenset([
    "contains", "depends_on", "produces", "consumes", "implements", "documents", "relates_to",
    "responds_with", "accepts", "builds", "orchestrates", "invokes", "configures", "tests",
    "represents", "feeds_into", "enforces", "verifies", "exposes", "governs",
    # Function-level call edge; symbol -> symbol. See ADR 0004.
    "calls",
    # Governance and provenance vocabulary (ADR 0016, project-asu).
    # Labels cover ownership (``owned_by``), bidirectional gating
    # (``gates`` / ``gated_by``), temporal replacement (``supersedes``),
    # validator-subject assertions (``validates``), producer-artifact
    # emission (``generates``), data-model evolution (``migrates``), and
    # contractual agreement between parties and interfaces (``contracts``).
    "owned_by", "gates", "gated_by", "supersedes", "validates",
    "generates", "migrates", "contracts",
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

# -- Validators (re-exported from _contract_validators) --------------------
# The actual implementation lives in weld._contract_validators to keep this
# module under the 400-line default. All public names are re-exported here
# so existing callers (``from weld.contract import validate_graph``) work
# unchanged.
from weld._contract_validators import (  # noqa: E402
    validate_edge,
    validate_fragment,
    validate_graph,
    validate_meta,
    validate_node,
)

__all__ = [
    "SCHEMA_VERSION",
    "VALID_NODE_TYPES",
    "VALID_EDGE_TYPES",
    "AUTHORITY_VALUES",
    "CONFIDENCE_VALUES",
    "ROLE_VALUES",
    "DOC_KIND_VALUES",
    "SECTION_KIND_VALUES",
    "PROTOCOL_VALUES",
    "SURFACE_KIND_VALUES",
    "TRANSPORT_VALUES",
    "BOUNDARY_KIND_VALUES",
    "NODE_OPTIONAL_PROPS",
    "EDGE_OPTIONAL_PROPS",
    "PROTOCOL_TRANSPORT_COMPATIBILITY",
    "ValidationError",
    "validate_meta",
    "validate_node",
    "validate_edge",
    "validate_graph",
    "validate_fragment",
]
