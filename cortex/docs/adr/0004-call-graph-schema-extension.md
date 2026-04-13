# 0004. Call Graph Schema Extension

Date: 2026-04-06
Status: Proposed

## Context

The single most common reason an LLM falls back to `grep` inside this repo
is "who calls X?" / "where is this function used?" / "what does Y depend
on?". The current `cortex` graph cannot answer those questions at function
granularity. Edges are coarse: `package contains entity`, `route
responds_with contract`, `stage feeds_into stage`. The `invokes` edge in
[cortex/contract.py](../../contract.py) is used only by the Bazel and
SQLAlchemy strategies and means "command invokes agent", not "function
calls function".

The tree-sitter strategy already loads grammars for Python, TypeScript,
Go, and Rust (per [ADR 0014](../../../docs/adrs/0014-kg-rust-language-strategy.md)),
and Python has stdlib `ast` available with zero extra dependencies. The
marginal cost of adding function-level call edges is therefore moderate,
and the payoff is replacing the most frequent grep pattern with a
structured query that the cortex MCP server
([ADR 0015](../../../docs/adrs/0015-kg-mcp-server-exposure.md)) can expose
as a first-class tool.

The constraint that has historically kept this out of scope is type
resolution: a full, correct call graph in a dynamic language requires a
type resolver the size of a language server. We do not need correctness;
we need enough coverage to short-circuit grep.

## Decision

Extend the graph contract with a new `calls` edge type and add
function-level symbol granularity. Resolution is best-effort and
explicitly partial.

### Node granularity: new `symbol` node type

Introduce a new first-class node type `symbol` rather than overloading
`file` or `entity` or stuffing call data into props. Rationale:

- `file` is a coarse artifact node. Attaching per-function call lists as
  props breaks the "one concept per node" model the rest of cortex relies
  on and makes ranking/briefing surfaces harder to compose.
- `entity` is already used for ORM entities and top-level Python classes
  via [cortex/strategies/python_module.py](../../strategies/python_module.py);
  widening its meaning would muddy existing queries.
- A new node type keeps symbols cleanly queryable (`cortex query symbol:foo`),
  participates naturally in edges, and lets ranking treat function-level
  hits differently from file-level hits.

Symbol node IDs follow the form `symbol:<lang>:<module-path>:<qualname>`
(for example `symbol:py:cortex.strategies.tree_sitter:_load_language`).
Unresolved call targets become sentinel nodes of the form
`symbol:unresolved:<name>` so references remain indexable without
claiming definitive resolution.

### New edge type: `calls`

Add `calls` to `VALID_EDGE_TYPES` in [cortex/contract.py](../../contract.py).
Endpoints are always `symbol` nodes (resolved or unresolved). Edge props
carry a single `resolved: bool`. `resolved: true` covers both same-module
name lookup and import-table resolution; `resolved: false` is used only
for `symbol:unresolved:<name>` sentinel targets.

### Per-language extraction strategies

- **Python**: new `cortex/strategies/python_callgraph.py` using stdlib `ast`.
  Walks `FunctionDef` / `AsyncFunctionDef` nodes, records symbol nodes,
  and for each `Call` in the body attempts: (1) same-module name lookup,
  (2) import-table resolution against `Import` / `ImportFrom` statements
  in the module, (3) fallback to `symbol:unresolved:<name>`. No inference
  across module boundaries beyond what imports make explicit.
- **TypeScript / Go / Rust**: extend
  [cortex/strategies/tree_sitter.py](../../strategies/tree_sitter.py) and the
  per-language query files in [cortex/languages/](../../languages/) with a new
  `calls` query. Same best-effort resolution model: imports resolved where
  grammars expose them, otherwise `symbol:unresolved:<name>`.

### Schema version and migration

Bump `SCHEMA_VERSION` in [cortex/contract.py](../../contract.py) from `1` to
`2`. Existing `graph.json` files are not migrated in place; the migration
story is "re-run `cortex discover`". The orchestrator rejects graphs with an
older schema version with a clear regenerate-this message, matching how
[ADR 0001](0001-plugin-strategy-architecture.md) treats strategy contract
changes.

### What we explicitly do not do

- No full type resolver. No attribute-chain resolution
  (`self.foo.bar()`), no dynamic dispatch inference, no class-hierarchy
  method resolution order beyond what the Python `ast` layer gives us
  directly.
- No runtime tracing or coverage-driven edges.
- No cross-repository call resolution.
- No new CLI surface in this ADR. `cortex callers` / `cortex references` and the
  matching MCP tools `cortex_callers` / `cortex_references` are an implementation
  concern; the ADR fixes only the schema.
- No replacement of the existing coarse edges (`contains`, `depends_on`,
  `invokes`). `calls` is additive.
- No multi-value confidence gradient on `calls` edges. The single
  `resolved: bool` prop is the only metadata; ranking heuristics that need
  finer gradations can layer them in later if a real consumer materializes.

## Consequences

### What becomes easier

- Agents can answer "who calls X?" and "what does this function depend
  on?" with a single structured query, replacing the most frequent grep
  pattern.
- Ranking can prefer resolved symbols over unresolved sentinels, giving
  the brief surface a natural precision/recall dial.
- Adding new language support for call graphs reuses the existing
  tree-sitter strategy pattern and a single new query file per language.

### What becomes harder

- `graph.json` grows. Call edges are the highest-cardinality edge type we
  have proposed so far; the brief surface must rank and truncate
  aggressively or risk swamping LLM prompts.
- The graph now contains best-effort data. Consumers must distinguish
  resolved from unresolved sentinels via `props.resolved`; unresolved
  entries are intentionally indexable but should not be treated as ground
  truth.
- Schema version bump forces a one-time `cortex discover` for every consumer
  on upgrade.

### What the team commits to

- `symbol` is a first-class node type, not a sub-type or a property.
- Every `calls` edge carries a `resolved: bool` prop. There is no
  confidence gradient.
- Unresolved targets always use the `symbol:unresolved:<name>` sentinel
  shape; strategies never drop a call site silently.
- The Python strategy uses stdlib `ast` only; no new mandatory
  dependencies.

## Related Issues

(none)
