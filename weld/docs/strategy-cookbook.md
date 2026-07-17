# Weld Strategy Cookbook

This cookbook explains how to choose and extend `weld` extraction paths without
turning the tool into a giant built-in parser collection.

## Decision order

When adding coverage for a new repository surface, choose the first option
that fits:

1. bundled strategy
2. optional tree-sitter strategy
3. project-local strategy in `.weld/strategies/`
4. external adapter command that emits normalized weld JSON

Use the lightest viable option. Prefer repo-local customization over bloating
the bundled strategy set with one-off project logic.

## Recipe: Wire the interface strategies (gRPC, events, runtime contract)

Beyond file- and symbol-level extraction, `weld` bundles five **interface
strategies** that model how services talk to each other: gRPC surfaces,
asynchronous event channels, and the runtime contract. When your repository
carries the matching artifacts, `wd init` detects them and writes the source
entries below into `.weld/discover.yaml` for you -- so the entries in this
section are exactly what you will see in a generated config. You can also add
or adjust them by hand.

These strategies are conservative on purpose. They extract only what is
declared in static text -- proto files, compose environment variables, call
sites, and the runtime-contract document -- and never execute code or infer
data flow. Any edge whose endpoint is not present in the graph is dropped when
fragments merge, so partial coverage stays honest instead of guessing.

Two of the five (`grpc_bindings` and `events_bindings`) emit only edges onto
nodes their companion declaration strategy creates. List each after its
companion: `grpc_bindings` after `grpc_proto`, and `events_bindings` after
`events`.

### gRPC proto declarations (`grpc_proto`)

```yaml
  - glob: "**/*.proto"
    type: rpc
    strategy: grpc_proto
```

Parses declared `.proto` files into interaction-graph nodes:

- each `rpc` line inside a `service` becomes an `rpc` node carrying its
  request and response type names, with `protocol` `grpc`, a `surface_kind` of
  `request_response` or `stream`, and `transport` `http2`. The declaring file
  gains an `invokes` edge to the rpc, and the rpc gains `accepts` and
  `responds_with` edges to its request and response messages.
- each `message` becomes a `contract` node (nested messages included).
- each `enum` becomes an `enum` node.

Declaration nodes are `authority` `canonical` and `confidence` `definite` --
the facts come from the proto text alone. Cross-file `import` resolution and
option parsing are out of scope.

### gRPC Python bindings (`grpc_bindings`)

```yaml
  - glob: "**/*.py"
    type: file
    strategy: grpc_bindings
    proto_glob: "**/*.proto"
```

Links Python server and client code back to the `rpc` nodes that `grpc_proto`
declares. The extra `proto_glob` key points at the same `.proto` tree so the
strategy can index services by name; it defaults to `proto/**/*.proto` when
omitted, which is why `wd init` sets it explicitly. Detection is structural:

- a class that subclasses a `*Servicer` base imported from a `*_pb2_grpc`
  module is treated as a server. Each method matching a declared rpc emits an
  `implements` edge from the method symbol and an `invokes` edge from the file.
- a `stub = <module>.<Name>Stub(...)` assignment followed by `stub.Method(...)`
  in the same function emits an `invokes` edge from the file to the rpc.

These edges are `confidence` `inferred`, because the class and stub naming is a
code-generation convention rather than a direct proto reference. The strategy
emits no nodes of its own.

### Generic DDS `.idl` contracts and topics (`dds_idl`)

```yaml
  - glob: "**/*.idl"
    type: contract
    strategy: dds_idl
```

Parses OMG IDL data-definition files used by non-ROS2 DDS stacks (CycloneDDS,
FastDDS) into interaction-graph nodes. Extraction is text-only: no
`idlc` / `fastddsgen` run, and `#include` directives are **not** followed.

- each `struct` becomes a `contract` node with its typed fields (module
  nesting yields a dotted qualified name, e.g. `contract:dds:sensors.image`).
- each `enum` becomes an `enum` node with its member identifiers.
- every topic-capable struct also mints a `channel:ros2_dds:<qualified>` node
  with `surface_kind` `pub_sub`. A struct is topic-capable unless it is
  explicitly `@nested`; the channel is `confidence` `definite` when an
  `@topic` annotation or a `#pragma keylist` names the struct, else
  `inferred`. Each channel gains an `implements` edge to its data contract,
  and the declaring file gains `contains` edges to every node.

Channels reuse the existing `ros2_dds` transport value — it denotes the
DDS/RTPS wire, not the ROS2 framework — so a raw-DDS topic and a ROS2 topic on
the same wire share a `channel:ros2_dds:<topic>` key for cross-repo binding.
`protocol` is left unset because no protocol value fits non-ROS2 DDS. To model
ROS2 interface files (`.msg` / `.srv` / `.action`) instead, use the
`ros2_interfaces` strategy.

### Asynchronous event channels (`events`)

```yaml
  - glob: "**/*.py"
    type: channel
    strategy: events
    kind: py_callsite

  - glob: "docker-compose.yml"
    type: channel
    strategy: events
    kind: compose_env
```

Emits `channel` nodes for declared Kafka, Celery, and Redis surfaces. The
`kind` key selects one of two extractors (it defaults to `compose_env`):

- `compose_env` reads `docker-compose*.yml` for environment entries whose key
  matches a channel shape (`KAFKA_*_TOPIC`, `KAFKA_TOPIC_*`, `CELERY_*_QUEUE`,
  or `REDIS_*_CHANNEL`) and whose value is a bare literal string.
- `py_callsite` walks Python for `<root>.<verb>("literal", ...)` publish calls
  where `<root>` is a known client name (`KafkaProducer`, `kafka`, or `redis`)
  and `<verb>` is a publish verb (`send`, `produce`, `send_and_wait`, or
  `publish`).

Channels are keyed `channel:<transport>:<name>` (`transport` is `kafka`,
`amqp`, or `tcp`), carry `protocol` `event` and `surface_kind` `pub_sub`, and
gain a `contains` edge from the declaring file. `wd init` emits the
`py_callsite` entry when it sees an async-client import, plus one `compose_env`
entry per compose file that declares a channel variable.

### Asynchronous channel bindings (`events_bindings`)

```yaml
  - glob: "**/*.py"
    type: file
    strategy: events_bindings
```

Links Python producer and consumer call sites back to the `channel` nodes that
`events` declares:

- a producer call such as `<root>.send("orders.placed", ...)` emits a
  `produces` edge from the file to the channel.
- a consumer call such as `<root>.subscribe(["orders.placed"])` emits a
  `consumes` edge from the file to each named channel.
- when the function enclosing a producer call takes a typed payload parameter,
  an `implements` edge links the channel to that `contract`.

As with the gRPC bindings, these edges are `confidence` `inferred` and the
strategy emits no nodes of its own.

### Runtime contract (`runtime_contract`)

```yaml
  - glob: "docs/runtime-contract.md"
    type: rpc
    strategy: runtime_contract
```

Reads a `runtime-contract.md` -- any file the glob matches that contains a
`## Runtime Summary` table -- as the authoritative record of runtime
boundaries and healthchecks. It emits `rpc` nodes for the healthcheck
endpoints declared for the `api` boundary (for example `GET /healthz` and
`GET /readyz`), each stamped with `protocol` `http` and a `doc` role, and
wires the graph together with `documents`, `exposes`, `verifies`, and
`relates_to` edges onto service, gate, and deploy nodes that already exist.
Because dangling edges are dropped at merge time, pointing the glob at a wider
markdown tree is safe -- only files carrying the summary table contribute.

## Recipe: Add coverage for a new language

Use a bundled or optional tree-sitter path when:

- the language has a stable grammar
- file-level or symbol-level structure is broadly reusable
- the extraction problem is common across many projects

For C# repositories, use the shared tree-sitter path with
`language: csharp`. It extracts class-like types, methods, properties,
attributes, namespaces, and `using` dependencies without requiring a compiler
workspace. Import package nodes use `.csproj` `PackageReference` entries and
common .NET namespace prefixes to classify `origin` as project, standard
library, external, or unresolved. Exact method, property, and class queries
rank the promoted definition symbol before the owning file.

For Java repositories, use the shared tree-sitter path with
`language: java`. It extracts class-like types, methods, annotations,
package declarations, and `import` dependencies without requiring a compiler
workspace. Import package nodes are classified by origin: ``java.*``,
``javax.*``, and ``jdk.*`` prefixes resolve to stdlib; the project's own
``<groupId>`` (read from any ``pom.xml`` under the project root) resolves to
project; declared Maven ``<dependency><groupId>`` entries resolve to external;
everything else stays unresolved. Exact class and method queries rank the
promoted definition symbol before the owning file. Gradle parsing is not yet
implemented.

Use a project-local or external adapter when:

- the repository uses heavy macros, generated sources, or bespoke conventions
- the real source of truth is a compiler or build-graph tool
- extraction depends on repository-specific structure more than on the
  language itself

## Recipe: Onboard a clang or C++ codebase

Do **not** require `weld` to understand every C++ repository natively.

Preferred path:

1. reuse the repo's existing compile database or clang-based analysis
2. emit normalized weld JSON from a repo-local command
3. plug that command into the future `strategy: external_json` surface
4. keep repository-specific heuristics inside the adapter, not inside bundled
   weld code

The adapter should emit:

- canonical node IDs
- repository-relative file paths
- explicit `source_strategy`, `authority`, `confidence`, and `roles` metadata
- discovered-from paths for provenance

## Recipe: Handle a custom build system

If the build system already has a graph or manifest of targets and
dependencies, prefer adapting that output rather than reverse-engineering it
with regex.

Good adapter outputs include:

- build targets
- test targets
- entrypoints
- dependency relationships
- repository boundaries or ownership groups when they are already encoded

The graph does not need to mirror the build system perfectly. It only needs to
extract the surfaces that help an agent choose implementation and verification
paths safely.

## Recipe: Handle a legacy repository

Legacy repositories often have:

- mixed languages
- weak directory conventions
- generated code mixed with hand-written code
- docs and runbooks that matter more than AST structure

Recommended approach:

1. onboard docs, runbooks, workflows, and policy first
2. model stable entrypoints and boundaries explicitly in topology
3. use targeted strategies or adapters for the highest-value subsystems
4. accept partial coverage and mark confidence honestly

The goal is agent usefulness, not theoretical completeness.

## Recipe: Write a project-local strategy

Use `.weld/strategies/<name>.py` when:

- extraction logic is repository-specific
- the input is simple enough to parse directly in Python
- the repository does not already have a better external analyzer

Start from the copyable template:

```bash
wd scaffold local-strategy my_strategy
```

Then edit the `extract()` function to match your project's needs.

Guidelines:

- implement a single `extract(root, source, context)` function
- return normalized `nodes`, `edges`, and `discovered_from`
- keep extraction local and explicit
- use shared helpers where appropriate
- do not import other strategies directly

Good project-local strategies are small, honest, and tightly scoped.

## Recipe: Write an external adapter command

Use an external adapter when the best analyzer already exists outside `weld` --
a compiler, build tool, or custom script that can emit structured output.

Start from the copyable template:

```bash
wd scaffold external-adapter my_adapter
```

Then wire it into `discover.yaml`:

```yaml
sources:
  - strategy: external_json
    command: "python3 .weld/adapters/my_adapter.py"
```

Edit `build_fragment()` in the adapter to emit the nodes and edges your
project needs.  The adapter runs with `cwd` set to the repo root and must
print valid weld JSON to stdout.  Invalid output is rejected gracefully.
If the fragment should participate in `wd trace`, map custom runtime terms
onto the documented trace buckets and edge labels in `docs/graph-schema.md`.

## Recipe: Emit normalized metadata

As agent semantics become first-class, strategies and adapters should populate
these fields when they can do so honestly:

- `source_strategy`
- `authority`
- `confidence`
- `roles`
- `file`
- `span`

Use the standard vocabularies:

- `authority`: `canonical`, `derived`, `manual`, `external`
- `confidence`: `definite`, `inferred`, `speculative`
- `roles`: `implementation`, `test`, `config`, `doc`, `build`,
  `migration`, `fixture`, `script`

If the strategy cannot justify a value, omit it rather than guessing.

## Recipe: Model boundaries and overlays

Use topology overlays when the right answer is architectural, not syntactic.

Examples:

- public vs internal service boundaries
- human-owned subsystem seams
- runtime entrypoints
- operations-only surfaces
- test and release gates

This is often the most important layer for agent guidance because it encodes
what a maintainer knows that a parser cannot infer safely.

## Recipe: Keep the graph useful for agents

When extending `weld`, optimize for questions an agent asks during real work:

- what file or module should I open first?
- what doc is authoritative here?
- what tests or gates verify this surface?
- what policy constrains this change?
- what other system boundary will I cross if I edit this component?

If a strategy or adapter improves those answers, it fits the toolkit.
If it only extracts more symbols without improving agent decisions, it should
be lower priority.
