"""Schema version, closed vocabularies, and ``ValidationError`` -- the
dependency-free leaf under the connected-structure metadata contract.

Split out of :mod:`weld.contract` (bd 5038-l24d9, ADR 0130 disposition #4):
``contract.py`` re-exported the validators it imports at its own bottom
(the "facade re-exports a sibling split out for the line-count cap" shape),
while ``_contract_validators.py``/``_graph_doc_validators.py``/
``_validate_diagnostics.py`` imported ``ValidationError`` -- and, for
``_contract_validators.py``, the vocabulary constants and
``SCHEMA_VERSION`` too -- back from ``contract.py`` at real top level. That
made a 4-member import cycle: ``contract -> _contract_validators ->
_validate_diagnostics -> contract`` (``ValidationError``) plus
``contract <-> _graph_doc_validators`` directly.

This module holds no import of :mod:`weld.contract` or any validator
sibling, so nothing importing it can cycle back. ``contract.py`` imports
from here and re-exports everything for its existing public surface
(``from weld.contract import ROLE_VALUES`` and friends keep working
unchanged); the three validator modules import from here directly instead
of from ``contract.py``, which is what breaks the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

# -- Schema version --------------------------------------------------------
#: v2: ``symbol`` node + ``calls`` edge (ADR weld/docs/adr/0004).
#: v3: seven ``ros_*`` node types; no new edges (ADR docs/adrs/0016).
#: v4: generalized interaction-surface vocabulary -- ``rpc`` and ``channel``
#:     node types plus optional protocol metadata (``protocol``,
#:     ``surface_kind``, ``transport``, ``boundary_kind``, ``declared_in``);
#:     no new edges (ADR docs/adrs/0086, tracked project).
#: v5: Agent Graph vocabulary for static AI customization assets:
#:     subagent, skill, instruction, prompt, hook, mcp-server, permission,
#:     platform, and scope nodes plus explicit agent-relationship edges
#:     (ADR docs/adrs/0021).
#: v6: ``external-dep`` node type -- a dependency a build target declares
#:     via an external-workspace label (``@pypi//tree_sitter_cpp`` and
#:     the like) but does not contain; no new edges, ``depends_on`` reused
#:     (ADR docs/adrs/0121).
#: v7: ``decorates`` edge type -- a python decorator's resolved target ->
#:     the symbol it decorates (distinct from ``calls``: applying a
#:     decorator is not calling the decorated symbol); no new node type
#:     (ADR docs/adrs/0122).
#: v8: ``references`` edge type -- a bare-name VALUE reference (not a
#:     call, e.g. a class passed by name as a keyword-argument value) that
#:     resolves to a same-module top-level symbol; no new node type (ADR
#:     docs/adrs/0127).
SCHEMA_VERSION: int = 8

VALID_NODE_TYPES = frozenset([
    "service", "package", "entity", "stage", "concept", "doc", "route", "contract", "enum", "file",
    "dockerfile", "compose", "agent", "command", "tool", "workflow", "test-suite", "config",
    "policy", "runbook", "build-target", "test-target", "boundary", "entrypoint", "gate",
    "deploy",
    "symbol",  # function-level callable; ADR 0004.
    # ROS2 vocabulary (ADR 0016): package, interface, node, topic, service, action, parameter.
    "ros_package", "ros_interface", "ros_node",
    "ros_topic", "ros_service", "ros_action", "ros_parameter",
    # Generalized interaction-surface vocabulary (ADR 0086, tracked project):
    # ``rpc`` is a request/response or stream method exposed or consumed by
    # a module (HTTP handler, gRPC method, ROS2 service/action). ``channel``
    # is a named pub/sub or stream endpoint (event topic, ROS2 topic, queue).
    "rpc", "channel",
    # Agent Graph vocabulary (ADR 0021): static AI customization assets and
    # their normalized platform/scope/tooling surfaces.
    "subagent", "skill", "instruction", "prompt", "hook", "mcp-server",
    "permission", "platform", "scope",
    # Polyrepo federation (ADR 0011 ss4, ss7): one ``repo:<name>`` node per
    # registered child in the root meta-graph. Carries path metadata
    # (``path``, ``path_segments``, ``depth``, ``tags``) and is emitted
    # exclusively by the root discovery branch. Presence of any ``repo:*``
    # node triggers ``meta.schema_version = 2`` on save (ADR 0012 ss4).
    "repo",
    # ADR 0057 Wave 3 (optional libclang semantic layer): ``macro``
    # nodes represent preprocessor definitions; ``template_definition``
    # nodes represent class/function templates the libclang index sees.
    # Both are minted only by the ``cpp_libclang`` strategy and stay
    # absent when the optional extra is not installed.
    "macro", "template_definition",
    # ADR 0121: a dependency a build target declares via an
    # external-workspace label (``@pypi//tree_sitter_cpp``) but does not
    # contain. One node per distinct ``(repo, name)`` identity, id
    # ``external-dep:<repo>:<name>``; reached by ``depends_on`` edges from
    # the ``build-target``/``test-target`` nodes that declare it.
    "external-dep",
])
VALID_EDGE_TYPES = frozenset([
    "contains", "depends_on", "produces", "consumes", "implements", "documents", "relates_to",
    "responds_with", "accepts", "builds", "orchestrates", "invokes", "configures", "tests",
    "represents", "feeds_into", "enforces", "verifies", "exposes", "governs",
    # Function-level call edge. ADR 0004: symbol -> symbol. ADR 0122
    # widens the ``from`` endpoint's node-type population (unchanged
    # meaning) to also allow ``file:`` (a module-level statement, sourced
    # at the module's file anchor) and a class ``symbol:`` (a class-body
    # statement, sourced at the class's own symbol).
    "calls",
    # Decorator attribution (ADR 0122): decorator's resolved target ->
    # decorated symbol. Distinct from ``calls`` -- applying a decorator
    # (``f = deco(f)``) is not calling the decorated symbol.
    "decorates",
    # Same-module value reference (ADR 0127): referencing symbol (or the
    # module's ``file:`` anchor, for a module-level statement) -> a
    # same-module top-level symbol named as a bare-name VALUE -- a
    # keyword-argument value, a tuple/list element, an assignment RHS --
    # and never called. Distinct from ``calls``: nothing is invoked.
    "references",
    # C++ header/source pairing (ADR 0057 Wave 2): ``file:<header>``
    # ``--implemented_by-->`` ``file:<source>`` when a ``.h``/``.hpp``/
    # ``.hxx`` finds its peer ``.cpp``/``.cc``/``.cxx``/``.c++``. Definite
    # for stem-match; ``inferred`` for one-cpp-in-dir fallback.
    "implemented_by",
    # Governance and provenance vocabulary (ADR 0016, tracked project).
    # Labels cover ownership (``owned_by``), bidirectional gating
    # (``gates`` / ``gated_by``), temporal replacement (``supersedes``),
    # validator-subject assertions (``validates``), producer-artifact
    # emission (``generates``), data-model evolution (``migrates``), and
    # contractual agreement between parties and interfaces (``contracts``).
    "owned_by", "gates", "gated_by", "supersedes", "validates",
    "generates", "migrates", "contracts",
    # Agent Graph vocabulary (ADR 0021): relationships among static AI
    # customization assets. Kept strict so adapters converge on one spelling.
    "uses_skill", "uses_command", "invokes_agent", "handoff_to",
    "references_file", "applies_to_path", "provides_tool",
    "restricts_tool", "triggers_on_event", "overrides", "duplicates",
    "conflicts_with", "implements_workflow", "part_of_platform",
    "generated_from",
    # ADR 0057 Wave 3 (optional libclang semantic layer):
    # ``file --defines_macro--> macro`` (a TU defines a preprocessor
    # macro), ``macro --expands_to--> symbol`` (an expansion target
    # libclang resolves), ``template_definition --instantiated_by-->
    # file`` (an instantiation site for a template). All three carry
    # ``confidence: definite`` per ADR 0050 because libclang is the
    # ground-truth side of the precedence rule.
    "defines_macro", "expands_to", "instantiated_by",
    # Class/interface inheritance (ADR 0056 base-list extension, ADR 0064
    # criterion 2): ``symbol:csharp:<module_path>:<derived> --inherits-->
    # symbol:csharp:<ns>.<base>`` (or ``file:<base>`` when the base
    # resolves to a project file) when the C# enricher extracts a
    # non-interface base from a ``base_list``. Companion edge to
    # ``implements`` (interface bases). Both carry ``confidence:
    # inferred`` because the interface/class distinction is a
    # naming-convention heuristic (``^I[A-Z]``); deeper resolution
    # would require Roslyn-grade type analysis. ADR 0050 confidence
    # placement applies. Edges originate at the class-level promoted
    # symbol node so per-class context surfaces inheritance neighbours
    # in multi-class files. ``implements`` is already in the
    # vocabulary so only ``inherits`` is added here.
    "inherits",
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
#: implementation | entrypoint | package | test | config | doc | build
#: | migration | fixture | script
#: ``entrypoint`` is the file-anchor-symmetry exemption marker per ADR 0041
#: Layer 3: ``file:*`` nodes that carry it bypass the inbound-edge requirement.
#: ``package`` marks a grouping container rather than a code artifact: it is
#: what ``python_package`` and ``csharp_package`` stamp on the ``package:``
#: nodes they mint. No other member describes a namespace, and reusing
#: ``implementation`` would be wrong twice over -- packages hold code rather
#: than being it, and ``ranking.role_boost`` would then rank every package
#: alongside real implementation files on role-filtered queries.
ROLE_VALUES: frozenset[str] = frozenset(
    ["implementation", "entrypoint", "package", "test", "config", "doc",
     "build", "migration", "fixture", "script"]
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

# -- Interaction-surface metadata (ADR 0086, tracked project) --------------
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

#: Allowed ``transport`` values per ``protocol``. Drives the coherence
#: check in :func:`weld._contract_validators.validate_node`: when a
#: strategy or adapter stamps both props, the pair must be physically
#: plausible per ADR 0086's static-truth policy. Omission of either prop
#: skips the check -- partial coverage is honest.
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
    """A single validation finding.

    *hint* is an optional actionable fix suggestion used by ``wd validate``
    and ``wd validate-fragment`` to turn sparse diagnostics into
    copy-pasteable guidance. When present it is appended to ``__str__`` as
    ``" (hint: ...)"`` so the existing JSON payload still carries the
    enriched text while preserving ``path.field: message`` substrings that
    downstream callers and tests match against.
    """
    path: str
    field: str
    message: str
    hint: str | None = None

    def __str__(self) -> str:
        base = f"{self.path}.{self.field}: {self.message}"
        if self.hint:
            return f"{base} (hint: {self.hint})"
        return base
