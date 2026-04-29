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

## Recipe: Add coverage for a new language

Use a bundled or optional tree-sitter path when:

- the language has a stable grammar
- file-level or symbol-level structure is broadly reusable
- the extraction problem is common across many projects

For C# repositories, use the shared tree-sitter path with
`language: csharp`. It extracts class-like types, methods, properties,
attributes, namespaces, and `using` dependencies without requiring a compiler
workspace.

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
