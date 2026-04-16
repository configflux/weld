# Weld Glossary

## Artifact

Any repository object that `weld` models directly, such as a source file, route,
contract, workflow, tool, policy document, runbook, build target, or test
target.

## Boundary

An explicit seam in the repository or system that constrains how a change
should move, such as public vs internal surfaces, service ownership lines, or
runtime trust boundaries.

## Authority

How authoritative an artifact is for a concept. Defined in `AUTHORITY_VALUES`.

Values:

- `canonical` -- the primary, definitive source for a concept
- `derived` -- automatically generated or inferred from a canonical source
- `manual` -- hand-written or manually maintained
- `external` -- originates outside the repository

## Confidence

How reliable a classification or relationship is. Defined in
`CONFIDENCE_VALUES`.

Values:

- `definite` -- high certainty; verified by structure or declaration
- `inferred` -- reasonable certainty; derived by heuristic or convention
- `speculative` -- low certainty; best-effort guess

## Role

What job an artifact plays in the repository. Defined in `ROLE_VALUES`.

Values:

- `implementation` -- production source code
- `test` -- test code and test infrastructure
- `config` -- configuration files
- `doc` -- documentation
- `build` -- build system targets and rules
- `migration` -- database or schema migrations
- `fixture` -- test fixtures and seed data
- `script` -- operational or utility scripts

## Node Types

The `type` field on a weld node. Defined in `VALID_NODE_TYPES`.

Core values: `service`, `package`, `entity`, `stage`, `concept`, `doc`,
`route`, `contract`, `enum`, `file`, `dockerfile`, `compose`, `agent`,
`command`, `tool`, `workflow`, `test-suite`, `config`, `policy`, `runbook`,
`build-target`, `test-target`, `boundary`, `entrypoint`, `gate`, `deploy`,
`symbol`.

ROS2 vocabulary (schema v3, ADR 0016): `ros_package`, `ros_interface`,
`ros_node`, `ros_topic`, `ros_service`, `ros_action`, `ros_parameter`.

Interaction-surface vocabulary (schema v4, ADR 0018): `rpc`, `channel`.
`rpc` models a request/response or stream method exposed or consumed by
a module (HTTP handler, gRPC method, ROS2 service or action). `channel`
models a named pub/sub or stream endpoint (event topic, ROS2 topic,
queue). Both node types carry the optional protocol metadata below.

## Edge Types

The `type` field on a weld edge. Defined in `VALID_EDGE_TYPES`.

Values: `contains`, `depends_on`, `produces`, `consumes`, `implements`,
`documents`, `relates_to`, `responds_with`, `accepts`, `builds`,
`orchestrates`, `invokes`, `configures`, `tests`, `represents`, `feeds_into`,
`enforces`, `verifies`, `exposes`, `governs`.

## Doc Kind

Semantic classification for documentation nodes. Defined in `DOC_KIND_VALUES`.

Values: `adr`, `policy`, `runbook`, `guide`, `gate`, `verification`.

## Section Kind

Semantic tags derived from markdown headings. Defined in
`SECTION_KIND_VALUES`.

Values: `setup`, `configuration`, `api-reference`, `architecture`,
`troubleshooting`, `overview`, `deployment`, `usage`, `testing`, `migration`,
`security`, `contributing`.

## Protocol

Which protocol family an interaction surface belongs to. Defined in
`PROTOCOL_VALUES`. Optional on any node but primarily used on `rpc`,
`channel`, `route`, and `ros_*` nodes.

Values: `http`, `grpc`, `event`, `ros2`, `inproc`.

See ADR 0018 for the static-truth policy that constrains how strategies
stamp this field.

## Surface Kind

The shape of an interaction surface as statically declared. Defined in
`SURFACE_KIND_VALUES`.

Values:

- `request_response` -- classic RPC or HTTP request/response
- `pub_sub` -- broadcast channels including ROS2 topics and event streams
- `stream` -- long-lived bidirectional streams
- `one_way` -- fire-and-forget publishes or commands

## Transport

Transport binding for an interaction surface when statically knowable.
Defined in `TRANSPORT_VALUES`.

Values: `tcp`, `http`, `http2`, `amqp`, `kafka`, `mqtt`, `ros2_dds`,
`inproc`.

## Boundary Kind

Which side of the module boundary an interaction surface sits on.
Defined in `BOUNDARY_KIND_VALUES`.

Values:

- `inbound` -- the module serves this surface
- `outbound` -- the module calls this surface
- `internal` -- the surface is module-local (useful for in-process calls
  modelled as RPCs)

## Declared In

Optional repo-relative path to the declarative source (IDL file, schema,
manifest, launch file) that declares an interaction surface. Per ADR 0018,
`declared_in` is always a checked-in path -- never a runtime URI.

## Adapter

A repo-local bridge that turns external analysis output into normalized weld
nodes and edges without requiring that logic to live in a bundled strategy.

## Brief

The high-level agent-facing retrieval surface. `wd brief` returns a compact,
classified context packet spanning implementation, authority, boundary,
verification, and provenance information. It is the recommended first call for
agents starting work on a task. See [Agent Workflow](agent-workflow.md) for
usage guidance.
