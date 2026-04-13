# Cortex

`cortex` is a portable **agenting knowledge graph toolkit** for whole-codebase
repository understanding.

It is designed to help humans and LLM agents answer questions like:

- where does this capability live?
- which docs or policies are authoritative?
- what build, test, or operational surfaces matter for this change?
- what boundaries or entrypoints constrain the implementation?

## What `cortex` is

- A whole-codebase graph builder, not just a source-code AST extractor.
- A portable toolkit that works from a plain checkout.
- A package-owned CLI and runtime rooted in `cortex/`, with `cortex` as the primary
  entrypoint and `python -m cortex` as a raw-source compatibility path.
- A config-driven system centered on `.cortex/discover.yaml`.
- A plugin-based extractor model with bundled strategies and project-local
  overrides.
- A bridge layer for unusual repositories, legacy systems, and external
  analyzers.

## What `cortex` is not

- Not only a language-parser playground.
- Not a hardcoded Bazel-only or framework-only tool.
- Not a packaging-heavy standalone platform in its current direction.
- Not a semantic-search or vector-index project in this phase.

## Current foundation

Today `cortex` already supports:

- config-driven discovery via `discover.yaml`
- bundled extraction strategies for code, docs, tools, infra, workflows, and
  configs
- project-local strategy overrides under `.cortex/strategies/`
- optional tree-sitter support for broader language extraction
- a graph export plus keyword-oriented file index
- query, context, path, find, and staleness inspection commands

## Supported languages

`cortex` ships bundled extraction support for the following languages and
ecosystems. All language parsers run through the shared tree-sitter strategy
and degrade gracefully when the matching grammar package is not installed.

| Language / ecosystem | Extraction surface                                                                                  | Grammar package          |
|----------------------|-----------------------------------------------------------------------------------------------------|--------------------------|
| Python               | modules, classes, functions, imports, call graph                                                    | `tree-sitter-python`     |
| TypeScript / JS      | exports, classes, imports                                                                            | `tree-sitter-typescript` |
| Go                   | exports, types, imports                                                                              | `tree-sitter-go`         |
| Rust                 | exports, types, imports                                                                              | `tree-sitter-rust`       |
| C++                  | exports, classes, imports, best-effort call graph with include-aware cross-file resolution           | `tree-sitter-cpp`        |
| ROS2                 | `ros_package`, `ros_interface`, `ros_node`, `ros_topic`, `ros_service`, `ros_action`, `ros_parameter`; `package.xml`, `CMakeLists.txt`, `.msg`/`.srv`/`.action`, `rclcpp`/`rclpy` node topology, literal launch entries | (reuses Python + C++)    |

C++ extraction is best-effort and does **not** require `clangd`, `libclang`,
or a `compile_commands.json`. See
[ADR 0017: C++ Extraction via Tree-sitter with a Best-effort Include Resolver](../docs/adrs/0017-cpp-tree-sitter-extraction.md)
for the limits (macros, templates, ADL, non-standard include layouts).

ROS2 extraction keys off the vocabulary described in
[ADR 0016: KG ROS2 Knowledge Graph Schema](../docs/adrs/0016-kg-ros2-knowledge-graph.md).
`cortex init` detects ROS2 workspaces and wires every `ros2_*` strategy
automatically; for an existing workspace, the usual two-step flow works:

```bash
cd path/to/ros2_workspace           # any checkout with src/<pkg>/package.xml
cortex init                             # writes .cortex/discover.yaml with ros2_* entries
cortex discover > .cortex/graph.json        # emits ros_package / ros_node / ros_topic / ... nodes
cortex query "cmd_vel"                  # ask ROS2-shaped questions
cortex context ros_node:demo_pkg/talker # inspect a specific node's topology
```

Dynamic names (topics assembled from parameters or remap arguments) are out
of scope — only statically extractable literal names land in the graph.

## Repo boundary

When `cortex` runs inside a Git repository, its discovery boundary follows the
Git-visible tree: tracked files plus untracked files that are not ignored.
Hard exclusions still apply for generated/cache paths such as `.cortex/`,
`node_modules/`, Bazel outputs, and nested repo copies.

That means the `cortex/` source tree is covered when discovery runs against this
repository, while consumer repositories that only use packaged `cortex` artifacts
do not grow internal cortex source nodes by accident.

## Direction

The current roadmap is to turn `cortex` from an artifact catalog into an
agent-facing repository context system.

That means future work prioritizes:

1. agent semantics over raw parser breadth
2. portable toolkit ergonomics over standalone packaging
3. whole-repo coverage across code, docs, infra, build, policy, tests, and
   operations

See the direction ADR for the canonical statement of that roadmap:

- [ADR 0001: Plugin Strategy Architecture](docs/adr/0001-plugin-strategy-architecture.md)
- [ADR 0002: Tree-sitter as Optional Native Dependency](docs/adr/0002-tree-sitter-optional-dependency.md)
- [ADR 0003: Agenting Knowledge Graph Toolkit Direction](docs/adr/0003-agenting-knowledge-graph-toolkit-direction.md)

## Install

For a normal development workflow, install `cortex` into your environment and
use the `cortex` command directly.

### Local editable install

From the repository root:

```bash
pip install -e ./cortex
cortex --help
```

### Install from GitHub

From another repository:

```bash
pip install "git+ssh://git@github.com/configflux/cortex.git@main#subdirectory=cortex"
cortex --help
```

### Agent bootstrap

If an agent or tool needs to install and bootstrap `cortex`:

```bash
pip install -e ./cortex
cortex --help
cortex prime                      # check what needs to be done
cortex bootstrap claude           # if you're Claude Code
cortex bootstrap codex            # if you're Codex
```

No external dependencies required (tree-sitter is optional).

### Raw source checkout compatibility

If you are working from a plain checkout without installing `cortex` first, the
module entrypoint remains available:

```bash
python -m cortex --help
```

## Upgrading from `kg`

If your project was previously using the `kg` toolkit, `cortex` ships an
automated migration subcommand (see
[ADR 0019](../docs/adrs/0019-kg-to-cortex-rename.md) for the full rename
mapping).

1. Install `cortex`:

   ```bash
   pip install -e ./cortex
   ```

2. From the root of the project you want to migrate, run:

   ```bash
   cortex migrate
   ```

   `cortex migrate` performs the following mechanical edits automatically
   and is idempotent — safe to run multiple times:

   - renames `.kg/` to `.cortex/` (only when `.cortex/` does not already
     exist; an existing `.cortex/` is never overwritten)
   - patches `.mcp.json` — renames the `"kg"` MCP server entry to
     `"cortex"` and updates the module path from `kg.mcp_server` to
     `cortex.mcp_server`
   - rewrites `.gitignore` lines matching `.kg/*.tmp.*` to
     `.cortex/*.tmp.*`

   It also scans for — but does **not** edit — the following items, and
   prints them as manual actions you need to take:

   - `.claude/commands/kg.md` → rename to `cortex.md`
   - `.claude/agents/kg.md` → rename to `cortex.md`
   - `.claude/commands/enrich-kg.md` → rename to `enrich-cortex.md`
   - `.codex/skills/kg/SKILL.md` → move to `.codex/skills/cortex/SKILL.md`
   - `.claude/settings.json` — update `Bash(kg)` / `Bash(kg *)` permission
     entries to the `cortex` equivalents

3. During the transition period, the legacy `kg` console script remains
   available as a deprecation shim. Running `kg <subcommand>` prints a
   one-line warning and delegates to `cortex`:

   ```
   ⚠ kg has been renamed to cortex — run `cortex migrate` to update your project
   ```

   The `kg` shim is intended to be removed in a future release (at least
   two months after the rename lands per ADR 0019). Migrate promptly.

## Quickstart

1. Bootstrap a starter config:

   ```bash
   cortex init
   ```

   Optionally, check what needs to be done:

   ```bash
   cortex prime
   ```

2. Tune `.cortex/discover.yaml` so it reflects the repository's real code, docs,
   infra, policy, build, and verification surfaces.

3. Build or refresh discovery artifacts:

   ```bash
   cortex discover > .cortex/graph.json
   cortex build-index
   ```

4. Inspect the graph:

   ```bash
   cortex query "stores page"
   cortex context file:web/app/stores/page
   cortex find footer
   cortex stale
   ```

## Agent onboarding

To set up agent integration, run the bootstrap command for your framework:

```bash
cortex bootstrap claude    # writes .claude/commands/cortex.md
cortex bootstrap codex     # writes .codex/skills/cortex/SKILL.md
```

Both also write `.cortex/README.md` and bootstrap `discover.yaml` if missing.

## Onboarding and extension

Use the following docs when adopting `cortex` in a new project:

- [Onboarding Guide](docs/onboarding.md)
- [Agent Workflow](docs/agent-workflow.md) -- when to use each retrieval surface
- [Strategy Cookbook](docs/strategy-cookbook.md)
- [Glossary](docs/glossary.md)

The intended extension order is:

1. use bundled strategies where they fit
2. add project-local strategies under `.cortex/strategies/` when repo-specific
   extraction is needed
3. use external adapter commands for tools like clang, custom build analyzers,
   or legacy repository scripts

Use `cortex scaffold` to write the bundled starter templates into your repository:

```bash
cortex scaffold local-strategy my_strategy
cortex scaffold external-adapter my_adapter
```

## Planned next surfaces

The following are part of the direction but are not fully implemented yet:

- normalized authority, confidence, and role metadata
- new first-class node types for policy, runbooks, build targets, test
  targets, boundaries, and entrypoints
- `strategy: external_json` as the standard bridge for repo-local adapters

`cortex brief` is now implemented as the high-level agent context packet. See
[Agent Workflow](docs/agent-workflow.md) for usage guidance.

## Design limits in this phase

This direction slice does **not** redesign:

- indexing
- storage format
- graph backend selection
- standalone packaging/distribution

Those are deferred until the agent-semantics and onboarding roadmap is in
place.
