# Weld

Weld is a lightweight agent-first utility for structuring connected work across
an entire repository.

It helps people and agents answer questions like:

- where does this capability live?
- which docs or policies are authoritative?
- what build, test, or operational surfaces matter for this change?
- what boundaries or entrypoints constrain the implementation?

## What Weld is

- A whole-codebase structure builder, not just a source-code AST extractor.
- A portable toolkit that works from a plain checkout.
- A package-owned CLI and runtime rooted in `weld/`, with `wd` as the primary
  entrypoint and `python -m weld` as the raw-source compatibility path.
- A config-driven system centered on `.weld/discover.yaml`.
- A plugin-based extractor model with bundled strategies and project-local
  overrides.
- A bridge layer for unusual repositories, legacy systems, and external
  analyzers.

## What Weld is not

- Not only a language-parser playground.
- Not a hardcoded Bazel-only or framework-only tool.
- Not a packaging-heavy standalone platform in its current direction.
- Not a semantic-search or vector-index project in this phase.

## Current foundation

Today Weld already supports:

- config-driven discovery via `discover.yaml`
- bundled extraction strategies for code, docs, tools, infra, workflows, and
  configs
- project-local strategy overrides under `.weld/strategies/`
- optional tree-sitter support for broader language extraction
- built-in semantic enrichment through pluggable providers
- a browser graph visualizer, graph export, and keyword-oriented file index
- query, context, path, impact, enrich, find, and staleness inspection commands
- static Agent Graph discovery for agents, skills, prompts, commands, hooks,
  instructions, MCP servers, tool permissions, and platform variants

## Supported languages

Weld ships bundled extraction support for the following languages and
ecosystems. All language parsers run through the shared tree-sitter strategy
and degrade gracefully when the matching grammar package is not installed.

| Language / ecosystem | Extraction surface                                                                                  | Grammar package          |
|----------------------|-----------------------------------------------------------------------------------------------------|--------------------------|
| Python               | modules, classes, functions, imports, call graph                                                    | `tree-sitter-python`     |
| TypeScript / JS      | exports, classes, imports                                                                            | `tree-sitter-typescript` |
| Go                   | exports, types, imports                                                                              | `tree-sitter-go`         |
| Rust                 | exports, types, imports                                                                              | `tree-sitter-rust`       |
| C#                   | types, methods, properties, attributes, namespaces, using dependencies                                | `tree-sitter-c-sharp`   |
| C++                  | exports, classes, imports, best-effort call graph with include-aware cross-file resolution           | `tree-sitter-cpp`        |
| ROS2                 | `ros_package`, `ros_interface`, `ros_node`, `ros_topic`, `ros_service`, `ros_action`, `ros_parameter`; `package.xml`, `CMakeLists.txt`, `.msg`/`.srv`/`.action`, `rclcpp`/`rclpy` node topology, literal launch entries | (reuses Python + C++)    |

C++ extraction is best-effort and does **not** require `clangd`, `libclang`,
or a `compile_commands.json`. See
[ADR 0017: C++ Extraction via Tree-sitter with a Best-effort Include Resolver](../docs/adrs/0017-cpp-tree-sitter-extraction.md)
for the limits (macros, templates, ADL, non-standard include layouts).

ROS2 extraction keys off the vocabulary described in the ROS2 connected-
structure schema ADR.
`wd init` detects ROS2 workspaces and wires every `ros2_*` strategy
automatically; for an existing workspace, the usual two-step flow works:

```bash
cd path/to/ros2_workspace           # any checkout with src/<pkg>/package.xml
wd init                             # writes .weld/discover.yaml with ros2_* entries
wd discover --output .weld/graph.json        # emits ros_package / ros_node / ros_topic / ... nodes
wd query "cmd_vel"                  # ask ROS2-shaped questions
wd context ros_node:demo_pkg/talker # inspect a specific node's topology
```

Dynamic names (topics assembled from parameters or remap arguments) are out
of scope — only statically extractable literal names land in the graph.

## Repo boundary

When Weld runs inside a Git repository, its discovery boundary follows the
Git-visible tree: tracked files plus untracked files that are not ignored.
Hard exclusions still apply for generated/cache paths such as `.weld/`,
`node_modules/`, Bazel outputs, and nested repo copies.

That means the `weld/` source tree is covered when discovery runs against this
repository, while consumer repositories that only use packaged Weld artifacts
do not grow internal source nodes by accident.

## Direction

The current roadmap is to turn Weld from an artifact catalog into an
agent-facing repository context system.

That means future work prioritizes:

1. agent semantics over raw parser breadth
2. portable toolkit ergonomics over standalone packaging
3. whole-repo coverage across code, docs, infra, build, policy, tests, and
   operations

See the direction ADR for the canonical statement of that roadmap:

- [ADR 0001: Plugin Strategy Architecture](docs/adr/0001-plugin-strategy-architecture.md)
- [ADR 0002: Tree-sitter as Optional Native Dependency](docs/adr/0002-tree-sitter-optional-dependency.md)
- [ADR 0003: Agenting Connected Structure Toolkit Direction](docs/adr/0003-agent-first-connected-work-toolkit-direction.md)

## Install

For a normal development workflow, install Weld into your environment and
use the `wd` command directly.

### Quick install (recommended)

For end users and agents, the fastest supported setup is the installer
script:

```bash
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
```

`install.sh` is a POSIX shell script that detects a compatible Python
(3.10–3.13) and installs via `uv`, `pipx`, or `pip --user`. It is
idempotent and honours a `.weld-version` file in the current directory
or any ancestor to pin a specific release tag. Use this when you just
want the `weld` CLI on your PATH without cloning the repo.

Weld is source/Git-first for now: `install.sh`, editable checkout installs,
and Git URL installs are the supported public paths. A package-index
publication path is not promised by this release.

### Local editable install

For local development on Weld itself, install from a checkout:

```bash
pip install -e ./weld
wd --help
```

To enable built-in enrichment providers, install an extra:

```bash
pip install -e "./weld[openai]"     # or [anthropic], [ollama], or [llm]
```

Agents can also enrich nodes without provider extras or API keys by reading the
relevant source or documentation and writing reviewed enrichment manually with
`wd add-node --merge`. Use `provider: manual` and `model: agent-reviewed` in
`props.enrichment`, refresh discovery first, and run `wd validate` plus
`wd stats` after edits because manual enrichment writes `.weld/graph.json`
directly.

### Install from GitHub

From another repository:

```bash
pip install "git+ssh://git@github.com/configflux/weld.git@main#subdirectory=weld"
wd --help
```

### Agent bootstrap

If an agent or tool needs to install and bootstrap `weld`:

```bash
pip install -e ./weld
wd --help
wd prime                      # check what needs to be done
wd prime --agent codex        # force the Codex row even if only Claude is configured
wd bootstrap claude           # if you're Claude Code
wd bootstrap codex            # if you're Codex (.codex/config.toml + skill)
wd bootstrap copilot          # if you're Copilot
```

`wd prime --agent {auto,claude,codex,copilot,all}` lets the active agent
surface itself in the matrix even when that framework has no files yet.
`auto` (default) infers from environment variables such as `CODEX_*`.

No external dependencies required (tree-sitter is optional).

### Raw source checkout compatibility

If you are working from a plain checkout without installing Weld first, the
module entrypoint remains available:

```bash
python -m weld --help
```

Runtime installs support Python 3.10 through 3.13. Contributor builds and
Bazel tests use the Python 3.12 toolchain pinned in `MODULE.bazel`.

## Quickstart

1. Bootstrap a starter config:

   ```bash
   wd init
   ```

   Optionally, check what needs to be done:

   ```bash
   wd prime
   ```

2. Tune `.weld/discover.yaml` so it reflects the repository's real code, docs,
   infra, policy, build, and verification surfaces.

3. Build or refresh discovery artifacts:

   ```bash
   wd discover --output .weld/graph.json
   wd build-index
   ```

4. Inspect the graph:

   ```bash
   wd query "stores page"
   wd context file:web/app/stores/page
   wd impact file:web/app/stores/page
   wd find footer
   wd viz --no-open
   wd stale
   ```

5. Inspect the repository's AI customization layer:

   ```bash
   wd agents discover
   wd agents list
   wd agents audit
   wd agents explain planner
   wd agents impact .github/agents/planner.agent.md
   wd agents plan-change "planner should always include test strategy"
   ```

   Agent Graph discovery writes `.weld/agent-graph.json` by default. Use
   `wd agents rediscover` to refresh it explicitly, and use `--json` on
   `list`, `explain`, `impact`, `audit`, and `plan-change` when an agent
   needs stable machine-readable output.

## CLI reference

| Command | Description |
|---|---|
| `wd init` | Bootstrap `.weld/discover.yaml` |
| `wd discover` | Run discovery, emit graph JSON |
| `wd agents discover` | Scan AI customization assets and write `.weld/agent-graph.json` |
| `wd agents rediscover` | Refresh `.weld/agent-graph.json` from a new static scan |
| `wd agents list` | List discovered AI customization assets from `.weld/agent-graph.json` |
| `wd agents explain <asset>` | Explain one AI customization asset and its graph relationships |
| `wd agents impact <asset>` | Show affected Agent Graph assets for a proposed customization change |
| `wd agents audit` | Audit AI customization assets for static consistency issues |
| `wd agents plan-change "<request>"` | Plan a static AI customization behavior change |
| `wd workspace status` | Show workspace child ledger and status |
| `wd build-index` | Regenerate file index |
| `wd query <term>` | Hybrid-ranked tokenized graph search |
| `wd find <term> [--limit N]` | Broad file-token search, separate from graph discovery; each hit carries an integer `score` (default `--limit 20`) |
| `wd context <id>` | Node + neighborhood |
| `wd impact <path-or-node>` | Reverse-dependency blast radius |
| `wd viz` | Local read-only browser graph explorer |
| `wd doctor` | Check setup health; exits 0 in directories that are not Weld projects yet |
| `wd bootstrap` | Agent onboarding files |
| `wd lint` | Lint graph edges, including custom `.weld/lint-rules.yaml` rules |

The repo includes a canonical Agent System Maintainer skill at
`.agents/skills/agent-system-maintainer/SKILL.md` and a GitHub Copilot
Agent Architect at `.github/agents/agent-architect.agent.md`. These files
are discovered like any other Agent Graph asset and provide the workflow for
safe future customization changes.

## Agent onboarding

To set up agent integration, run the bootstrap command for your framework:

```bash
wd bootstrap claude    # writes .claude/commands/weld.md
wd bootstrap codex     # writes .codex/skills/weld/SKILL.md + .codex/config.toml
wd bootstrap copilot   # writes .github/skills/weld/SKILL.md
```

All targets also write `.weld/README.md` and bootstrap `discover.yaml` if missing.
Run `wd discover` automatically only on repositories you trust: project-local
strategies are Python modules loaded at discovery time, and `external_json`
adapters execute configured commands from `discover.yaml`.

## Onboarding and extension

Use the following docs when adopting `weld` in a new project:

- [Onboarding Guide](docs/onboarding.md)
- [Agent Workflow](docs/agent-workflow.md) -- when to use each retrieval surface
- [Strategy Cookbook](docs/strategy-cookbook.md)
- [Glossary](docs/glossary.md)

The intended extension order is:

1. use bundled strategies where they fit
2. add project-local strategies under `.weld/strategies/` when repo-specific
   extraction is needed
3. use external adapter commands for tools like clang, custom build analyzers,
   or legacy repository scripts

Use `wd scaffold` to write the bundled starter templates into your repository:

```bash
wd scaffold local-strategy my_strategy
wd scaffold external-adapter my_adapter
```

## Planned next surfaces

The following are part of the direction but are not fully implemented yet:

- normalized authority, confidence, and role metadata
- new first-class node types for policy, runbooks, build targets, test
  targets, boundaries, and entrypoints
- `strategy: external_json` as the standard bridge for repo-local adapters

`wd brief` is now implemented as the high-level agent context packet. See
[Agent Workflow](docs/agent-workflow.md) for usage guidance.

## Design limits in this phase

This direction slice does **not** redesign:

- indexing
- storage format
- graph backend selection
- standalone packaging/distribution

Those are deferred until the agent-semantics and onboarding roadmap is in
place.
