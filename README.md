# Weld

[![CI](https://github.com/configflux/weld/actions/workflows/ci.yml/badge.svg)](https://github.com/configflux/weld/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/configflux-weld.svg)](https://pypi.org/project/configflux-weld/) [![Python versions](https://img.shields.io/pypi/pyversions/configflux-weld.svg)](https://pypi.org/project/configflux-weld/) [![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

A local codebase graph for AI coding agents. Weld scans code, docs, CI, build
files, runtime configs, and repo boundaries into a deterministic graph. Agents
can query this graph through CLI or MCP instead of rediscovering the repository
from scratch every session.

The graph lives on disk (`.weld/graph.json`), stays under your control, and
answers the questions agents and humans repeatedly ask about a codebase: where
a capability lives, which docs are authoritative, what build and test surfaces
a change touches, and what boundaries constrain the implementation.

**Try it in 5 minutes →** [docs/tutorial-5-minutes.md](docs/tutorial-5-minutes.md) walks through `wd init`, `discover`, `brief`, `query`, `context`, and `path` against demo workspaces. Spin up a clean demo with one command:

```bash
scripts/create-polyrepo-demo.sh /tmp/weld-polyrepo-demo
# or
scripts/create-monorepo-demo.sh /tmp/weld-monorepo-demo
```

Each script materializes a self-contained demo directory with seeded source files, `.weld` configs, and committed git history -- ready for `wd discover`. If you have Weld installed but no source checkout, the same demos are available through the CLI: `wd demo list`, `wd demo monorepo --init <dir>`, `wd demo polyrepo --init <dir>`.

## Use Weld when…

- your repo is too large for an agent to understand in one pass
- your system spans multiple repositories
- architecture is spread across code, docs, CI, configs, and service contracts
- you want reproducible repo context instead of ad-hoc chat memory

## When not to use Weld

- **Your repo is small (under ~50 files).** An agent can read it end-to-end;
  a graph adds overhead without payoff.
- **`grep` plus your IDE already answers your questions.** If nothing is
  missing from that workflow, Weld has nothing to add.
- **You only need symbol navigation.** Go-to-definition and find-references
  are an LSP job. Weld covers architecture, contracts, docs, and CI -- not
  IDE jump-to.
- **You expect compiler-grade static analysis.** Weld is a pragmatic graph,
  not a type checker or dataflow engine. It will not catch every reference
  or prove correctness.
- **You do not want repo-local configuration.** Weld lives in `.weld/`
  (config, graph, strategies) and expects to be committed alongside your
  code. If that is unacceptable, Weld is the wrong tool.

## How Weld compares

Weld is not a replacement for the tools below -- it sits alongside them and
gives agents a persistent, queryable map of the repository. Each of these
tools is excellent at what it does; Weld adds the connected structure they
were not designed to provide.

| Tool | Gives you | Weld adds |
|---|---|---|
| grep / ripgrep | Fast literal and regex search over file contents. | Typed nodes and edges -- a symbol, route, doc, or config is an addressable entity with neighbours, not a line of text. |
| ctags / LSP | Symbol navigation and go-to-definition inside one language. | A cross-language graph that also covers docs, CI, configs, service contracts, and repo boundaries -- surfaces an IDE was never meant to index. |
| Sourcegraph | Hosted code search and references across large fleets of repos. | A local, repo-local graph that lives next to your code. By default Weld tracks only config and lets you opt in (`wd init --track-graphs`) to commit the generated graph for warm-CI / warm-MCP setups. No server, no indexing fleet; agents query it offline through CLI or MCP. |
| vector DB / RAG | Embedding-based semantic recall over chunks of text. | Deterministic structure. Query results are exact nodes and edges with provenance, not top-k fuzzy matches, so agents can follow relationships instead of guessing. |
| Copilot / Claude Code / OpenCode | In-editor and agentic code generation and chat. | Shared repo context those agents can read through MCP -- the same graph across sessions and tools, instead of each agent rediscovering the repo on every run. |

## Key features

- **Whole-codebase discovery** — not just source code. Covers docs, config,
  CI workflows, infrastructure, and build files.
- **Config-driven** — point `.weld/discover.yaml` at your repo and tune
  what gets extracted.
- **Multi-language** — bundled tree-sitter strategies for Python, TypeScript/JS,
  Go, Rust, C#, C++, and ROS2.
- **Plugin architecture** — drop a `.py` file in `.weld/strategies/` to
  extract anything repo-specific.
- **Agent Graph** — discover agents, skills, prompts, commands, hooks,
  instructions, MCP servers, and platform-specific copies into
  `.weld/agent-graph.json`; see the
  [Agent Graph guide](docs/agent-graph.md) for node and edge types,
  authority/drift, and limitations, and the
  [platform support matrix](docs/platform-support.md) for tested surfaces.
- **Agent-native** — generates MCP config snippets by default and ships an
  optional stdio MCP server so Claude Code, Codex, and other agents can query
  the graph directly.
- **Zero external dependencies** — runs from a plain checkout with Python >= 3.10.
  Tree-sitter is optional.

## Quickstart

```bash
# Install (recommended — see the Install section for alternatives)
uv tool install configflux-weld

# Bootstrap config for your repo
wd init

# Run discovery and save the graph
wd discover --output .weld/graph.json

# Query the graph
wd query "authentication"
wd find "login"
wd context file:src/auth/handler
wd viz --no-open
wd stale
```

Try it on a real example: [examples/04-monorepo-typescript](examples/04-monorepo-typescript/) (monorepo) · [examples/05-polyrepo](examples/05-polyrepo/) (polyrepo federation).

Sample output (`wd query "auth"` — trimmed):

```json
{
  "query": "auth",
  "matches": [
    {
      "id": "symbol:src/auth/handler.py:authenticate",
      "label": "authenticate",
      "type": "function",
      "props": {
        "file": "src/auth/handler.py",
        "exports": ["authenticate"],
        "description": "Validate a bearer token and return the caller identity."
      }
    }
  ],
  "neighbors": [{"id": "route:/login", "type": "route"}],
  "edges": [
    {"from": "route:/login", "to": "symbol:src/auth/handler.py:authenticate", "type": "calls"}
  ]
}
```

See [Install](#install) for alternatives (local checkout, pip, raw source).

### Agent Graph for AI customizations

Weld also maps the AI customization layer around a repository: agents, skills,
instructions, prompts, commands, hooks, MCP servers, tool permissions, and
platform variants. The Agent Graph is static and repo-bound; discovery reads
known customization files and does not execute project code.

```bash
wd agents discover
wd agents list
wd agents audit
wd agents explain planner
wd agents impact .github/agents/planner.agent.md
wd agents plan-change "planner should always include test strategy"
```

Use `--json` on `list`, `explain`, `impact`, `audit`, and `plan-change` for
agent-friendly output. Use `wd agents rediscover` when you want an explicit
refresh of `.weld/agent-graph.json` before inspecting the persisted graph.
Static discovery and configuration generation are available for several
agent platforms; runtime validation is tracked per client in the
[platform support matrix](docs/platform-support.md). The
[Agent Graph guide](docs/agent-graph.md) documents node and edge types,
authority and drift, and the read-only-first policy.

### Agent-first onboarding

If an agent or coding assistant is driving setup, use the short bootstrap
path:

```bash
uv tool install configflux-weld   # recommended — see Install for alternatives
wd prime                  # show setup status + per-framework surface matrix
wd bootstrap claude       # writes .claude/commands/weld.md
wd bootstrap codex        # writes .codex/skills/weld/SKILL.md + .codex/config.toml
wd bootstrap copilot      # writes .github/skills/weld/SKILL.md + .github/instructions/weld.instructions.md
```

All three `wd bootstrap` frameworks accept opt-out flags:

- `--no-mcp` — skip the MCP pair (`.codex/config.toml` for codex; the `.mcp.json` guidance block for copilot/claude).
- `--no-enrich` — write the `.cli.md` variant that omits `wd enrich`.
- `--cli-only` — shorthand for `--no-mcp --no-enrich`.

To upgrade existing bootstrap files after pulling a new weld release, use
the diff-aware upgrade path:

- `wd bootstrap <framework> --diff` — print unified diffs between bundled
  templates and your on-disk copies without writing. Exits 1 when any
  file differs, 0 otherwise, so it composes with CI checks.
- `wd bootstrap <framework> --force` — overwrite targeted files while
  still honouring the opt-out (`--no-mcp`, `--no-enrich`, `--cli-only`)
  and federation template behaviour.

`wd prime` is idempotent and safe to re-run — it reports what is
already configured and what is still missing. Pass
`--agent {auto,claude,codex,copilot,all}` to force the active agent's row
into the matrix even when that framework has no files yet (e.g. a Codex user
in a Claude-only checkout sees `codex: skill no, mcp no -> wd bootstrap codex`
instead of silence). `auto` is the default and infers the agent from
environment variables such as `CODEX_*`.

## Trust model

Weld's trust posture is explicit and narrow:

- **Default**: bundled discovery reads source files and writes the local
  graph (`.weld/graph.json`). It does not execute discovered application
  code and does not open network connections.
- **Safe mode**: when enabled with `--safe`, safe mode disables
  project-local strategies (`.weld/strategies/`) and the `external_json`
  adapter for `wd discover`, and refuses network/LLM enrichment providers
  for `wd enrich`. Pass `wd discover --safe` to scan an untrusted
  repository without executing any code from it; pass `wd enrich --safe`
  to refuse network egress (every currently registered provider —
  Anthropic, OpenAI, Ollama — is refused). Safe mode produces a stable
  `[weld] safe mode: ...` stderr line for each refused path.
- **Advanced strategies**: project-local strategies are Python modules
  loaded at discovery time, and `strategy: external_json` executes
  configured commands from `discover.yaml`. Only enable these on
  repositories you trust.

See [SECURITY.md](SECURITY.md) for the full policy and reporting process.

## Supported languages

All language strategies use tree-sitter and degrade gracefully when the
grammar package is not installed.

| Language | Extraction surface | Grammar package |
|---|---|---|
| Python | modules, classes, functions, imports, call graph | `tree-sitter-python` |
| TypeScript / JS | exports, classes, imports | `tree-sitter-typescript` |
| Go | exports, types, imports | `tree-sitter-go` |
| Rust | exports, types, imports | `tree-sitter-rust` |
| C# | types, methods, properties, attributes, namespaces, using dependencies | `tree-sitter-c-sharp` |
| C++ | exports, classes, imports, best-effort call graph | `tree-sitter-cpp` |
| ROS2 | packages, nodes, topics, services, actions, parameters | (reuses Python + C++) |

To enable tree-sitter support:

```bash
uv tool install "configflux-weld[tree-sitter]"
```

To use the built-in semantic enrichment providers:

```bash
uv tool install "configflux-weld[openai]"     # or [anthropic], [ollama], or [llm]
```

For a source-checkout install (contributors editing Weld itself), see
[CONTRIBUTING.md](CONTRIBUTING.md).

Agents can also enrich nodes without provider extras or API keys by reading the
relevant source or documentation and writing reviewed enrichment manually:

```bash
wd stale
wd context "<node-id>"
wd add-node "<node-id>" --type "<node-type>" --label "<label>" --merge --props '{"description":"...","purpose":"...","enrichment":{"provider":"manual","model":"agent-reviewed","timestamp":"<ISO-8601 UTC timestamp>","description":"...","purpose":"...","suggested_tags":["lowercase","tags"]}}'
wd graph validate
wd graph stats
```

Manual enrichment writes `.weld/graph.json` directly and can be overwritten by
a later `wd discover --output .weld/graph.json`; refresh discovery before manual
edits. Manual inferred edges should use explicit provenance such as
`{"source": "manual"}` after the relationship is verified from source content.

Without tree-sitter, the built-in Python module strategy and non-language
strategies (markdown, YAML, config, frontmatter) still work.

## MCP

Weld generates MCP config snippets for Claude Code, VS Code, Cursor, and
Codex in the default install:

```bash
wd mcp config --client=claude
wd mcp config --client=vscode
wd mcp config --client=cursor
```

Running the stdio MCP server requires the optional MCP SDK extra:

```bash
uv tool install "configflux-weld[mcp]"
python -m weld.mcp_server --help
```

Point your client at `python -m weld.mcp_server`:

```json
{"mcpServers": {"weld": {"command": "python", "args": ["-m", "weld.mcp_server"]}}}
```

See **[docs/mcp.md](docs/mcp.md)** for the full tool reference, per-client
configs, example prompts, troubleshooting, and the exact dependency model. See
the [platform support matrix](docs/platform-support.md) for per-client support
status and runtime validation.

## Discovery configuration

Weld is driven by `.weld/discover.yaml`. Each entry maps a file pattern
to an extraction strategy:

```yaml
sources:
  - glob: "src/**/*.py"
    type: file
    strategy: python_module

  - glob: "docs/**/*.md"
    type: doc
    strategy: markdown

  - glob: ".github/workflows/*.yml"
    type: workflow
    strategy: yaml_meta
```

Run `wd init` to generate a starter config, or write one by hand. See
the [Strategy Cookbook](weld/docs/strategy-cookbook.md) for the full list
of bundled strategies.

### `.weld/.gitignore`

`wd init` and `wd workspace bootstrap` write a managed `.weld/.gitignore`
the first time they touch a `.weld/` directory (idempotent — never
overwrites an existing file). Three policies are available:

- **Default — config-only.** Tracks the source-of-truth config
  (`discover.yaml`, `workspaces.yaml`, `agents.yaml`, `strategies/`,
  `adapters/`, `README.md`) and ignores everything else weld writes,
  including the generated graphs (`graph.json`, `agent-graph.json`)
  and per-machine state (`discovery-state.json`, `graph-previous.json`,
  `workspace-state.json`, `workspace.lock`, `query_state.bin`). A
  fresh contributor gets a clean `git status` after the first run.
- **Track-graphs (opt-in).** Pass `--track-graphs` to widen the default
  so the canonical graphs are committed alongside config. Use this for
  warm-CI / warm-MCP workflows where every contributor should share a
  pre-built graph:

  ```bash
  wd init --track-graphs
  wd workspace bootstrap --track-graphs
  ```

- **Ignore-all (opt-in).** Pass `--ignore-all` for early experimentation
  or test installs where no weld state should be committed yet:

  ```bash
  wd init --ignore-all
  wd workspace bootstrap --ignore-all
  ```

  This writes a heavy-handed `*` / `!.gitignore` so every weld file is
  ignored.

`--track-graphs` and `--ignore-all` are mutually exclusive; passing both
is a usage error.

**Migration from earlier versions.** Pre-existing `.weld/.gitignore`
files written by older `wd init` / `wd workspace bootstrap` runs are
**not** rewritten — the helper is idempotent. To pick up the new
default, delete the file and re-run init:

```bash
rm .weld/.gitignore
wd init                  # config-only default, generated graphs ignored
# or wd init --track-graphs   to keep tracking the graphs as before
```

To opt out entirely, just delete `.weld/.gitignore` after init — the
skip-if-exists guard means it won't be recreated until the next init
or bootstrap.

### Custom strategies

Drop a Python file in `.weld/strategies/` to extract repo-specific
artifacts. The strategy signature:

```python
def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    ...
```

See [examples/02-custom-strategy](examples/02-custom-strategy/) for a
working example that extracts TODO comments as graph nodes.

## Polyrepo Federation

Weld supports federated polyrepo workspaces where a root directory contains
several child git repositories, each owning its own `.weld/` directory. The
root maintains a meta-graph of cross-repo relationships without duplicating
child content. Children remain portable and independently publishable.

### Prerequisites

- Each child repo has been initialized with `wd init` and has a
  `.weld/graph.json`.
- The workspace root directory contains the child repos as subdirectories
  (nested git repositories).

### Setting up a workspace

Run `wd init` at the workspace root. When nested git repositories are
detected, weld automatically scaffolds `.weld/workspaces.yaml` alongside
the usual `discover.yaml`:

```bash
cd ~/workspace-root
wd init                    # detects children, writes workspaces.yaml
wd init --max-depth 2      # limit scan depth for large directory trees
```

The `--max-depth` flag controls how many directory levels deep the scanner
looks for nested `.git` directories (default: 4).

### workspaces.yaml format

The workspace registry lists every child repo and declares which cross-repo
resolvers are active:

```yaml
version: 1
scan:
  max_depth: 4
  exclude_paths: [.worktrees, vendor]
children:
  - name: services-api
    path: services/api
    tags:
      category: services
  - name: services-auth
    path: services/auth
    tags:
      category: services
cross_repo_strategies: [service_graph]
```

- **version**: Schema version (currently `1`).
- **scan**: Controls automatic child detection. `max_depth` sets how deep
  the scanner walks; `exclude_paths` lists directories to skip.
- **children**: Each entry has a `path` (relative to the workspace root)
  and an optional `name` (auto-derived from the path if omitted, e.g.
  `services/api` becomes `services-api`). Optional `tags` provide
  category metadata; optional `remote` records a clone URL.
- **cross_repo_strategies**: Ordered list of resolvers that produce
  cross-repo edges in the root graph. Currently available: `service_graph`.

### Running discovery at the workspace root

```bash
cd ~/workspace-root
wd discover --output .weld/graph.json
```

When `workspaces.yaml` is present, `wd discover` operates in federation
mode. It reads each child's `.weld/graph.json`, builds `repo:<name>` nodes
for every present child, and runs the declared cross-repo resolvers to emit
edges between children. Children that are missing, uninitialized, or corrupt
degrade gracefully -- they are skipped and recorded in the workspace ledger
but do not block discovery.

Discovery is safe to run from a linked git worktree of the workspace root:
the federation pass falls back to the main worktree's checkout when sibling
child repos are not present at the worktree itself (ADR 0028). As a
defense-in-depth guard, federated discover refuses to overwrite an existing
non-empty `graph.json` with a 0-node meta-graph; pass `--allow-empty` to
intentionally tear the workspace graph down.

### Workspace status

Inspect the state of every registered child:

```bash
wd workspace status          # human-readable summary
wd workspace status --json   # raw JSON ledger
```

Example output:

```
Workspace status (3 children)
Counts: present=2, missing=1, uninitialized=0, corrupt=0
services-api: present (refs/heads/main a1b2c3d4e5f6)
services-auth: present dirty (refs/heads/feature-x 7890abcdef01)
services-worker: missing
```

Each child shows its lifecycle status, git branch, HEAD SHA prefix, and
whether the working tree is dirty.

### Sentinel files

Weld uses two sentinel files to distinguish workspace roots from
single-repo projects:

| File | Purpose |
|---|---|
| `.weld/workspaces.yaml` | Workspace registry -- lists children and cross-repo strategies |
| `.weld/workspace-state.json` | Workspace ledger -- lifecycle status, git SHA, graph hash per child |

The presence of `workspaces.yaml` activates federation mode in `wd discover`.
`workspace-state.json` is written automatically during discovery and read by
`wd workspace status`.

When `.weld/workspaces.yaml` is present at the bootstrap target, `wd bootstrap`
appends a federation paragraph to the copilot skill/instruction, codex skill,
and claude command directing agents to pick a child via `wd workspace status`
before querying inside it.

### Cross-repo resolvers

Resolvers are plugins that analyze child graphs and emit typed edges across
repo boundaries. They are declared in the `cross_repo_strategies` list in
`workspaces.yaml` and run in declaration order during root discovery.

| Resolver | Description |
|---|---|
| `service_graph` | Matches HTTP client call sites in one repo to API endpoint definitions in another. Emits `invokes` edges with host, port, and path metadata. |

Resolvers are read-only with respect to child graphs -- they never modify
a child's `.weld/graph.json`. Output edges are deterministic: identical
input produces byte-identical edges across runs.

### Rollback

To disable federation and return to single-repo behavior, delete the
workspace registry:

```bash
rm .weld/workspaces.yaml
```

This returns weld to legacy single-repo discovery at the root. Child
repositories are untouched -- each child's `.weld/` directory, graph, and
configuration remain intact and continue to work independently.

Optionally, remove the generated ledger as well:

```bash
rm .weld/workspace-state.json
```

## CLI reference

| Command | Description |
|---|---|
| `wd init` | Bootstrap `.weld/discover.yaml` (and `workspaces.yaml` when nested repos are detected); seed managed `.weld/.gitignore` (config-only default ignores generated graphs) |
| `wd init --max-depth N` | Limit nested repo scan depth during init (default: 4) |
| `wd init --track-graphs` | Seed `.weld/.gitignore` so canonical graphs (`graph.json` + `agent-graph.json`) stay tracked alongside config (warm-CI / warm-MCP workflow) |
| `wd init --ignore-all` | Write a fully-ignoring `.weld/.gitignore` instead of the config-only default; mutually exclusive with `--track-graphs` |
| `wd discover` | Run discovery, emit graph JSON (federation mode when `workspaces.yaml` is present) |
| `wd agents discover` | Scan AI customization assets and write `.weld/agent-graph.json` |
| `wd agents rediscover` | Refresh `.weld/agent-graph.json` from a new static scan |
| `wd agents list` | List discovered AI customization assets from `.weld/agent-graph.json` |
| `wd agents explain <asset>` | Explain one AI customization asset and its graph relationships |
| `wd agents impact <asset>` | Show affected Agent Graph assets for a proposed customization change |
| `wd agents audit` | Audit AI customization assets for static consistency issues |
| `wd agents plan-change "<request>"` | Plan a static AI customization behavior change |
| `wd workspace status` | Show workspace child ledger: lifecycle status, git ref, dirty state |
| `wd workspace status --json` | Emit the raw `workspace-state.json` payload |
| `wd workspace bootstrap` | One-shot polyrepo bootstrap: init root + every nested child, recurse-discover, rebuild root meta-graph (config-only `.weld/.gitignore` default) |
| `wd workspace bootstrap --track-graphs` | Bootstrap and seed `.weld/.gitignore` in root and every child to track canonical graphs alongside config |
| `wd workspace bootstrap --ignore-all` | Bootstrap and write a fully-ignoring `.weld/.gitignore` in root and every child; mutually exclusive with `--track-graphs` |
| `wd build-index` | Regenerate file index |
| `wd query <term>` | Hybrid-ranked tokenized graph search |
| `wd find <term> [--limit N]` | Broad file-token search, separate from graph discovery; each hit carries an integer `score` (default `--limit 20`) |
| `wd context <id>` | Node + neighborhood |
| `wd path <from> <to>` | Shortest path |
| `wd impact <path-or-node>` | Reverse-dependency blast radius |
| `wd callers <symbol>` | Direct/transitive callers |
| `wd viz` | Local read-only browser graph explorer |
| `wd stale` | Check graph freshness |
| `wd graph stats` | Graph statistics |
| `wd stats` | Backward-compatible alias for `wd graph stats` |
| `wd graph validate` | Validate graph against the contract |
| `wd validate` | Backward-compatible alias for `wd graph validate` |
| `wd doctor` | Check setup health; exits 0 in directories that are not Weld projects yet |
| `wd prime` | Setup status + per-framework agent surface matrix (skill / instruction / mcp) with fix commands; `--agent {auto,claude,codex,copilot,all}` forces an agent row even when its framework files are absent |
| `wd scaffold` | Write starter templates |
| `wd bootstrap` | Agent onboarding files |
| `wd brief` | Agent context briefing |
| `wd enrich` | LLM-assisted semantic enrichment |
| `wd lint` | Lint the graph for architectural violations |

`wd lint` also loads custom edge rules from `.weld/lint-rules.yaml` when
present:

```yaml
rules:
  - name: no-api-to-internal
    deny:
      from: { type: file, path_match: "api/**" }
      to: { type: file, path_match: "internal/**" }
```

Rules can add an `allow` block with the same `from` / `to` selectors to
exempt specific edges from a broader deny match.

Run `wd --help` for the full list.

The repository includes a canonical Agent System Maintainer skill at
`.agents/skills/agent-system-maintainer/SKILL.md` and a GitHub Copilot
Agent Architect at `.github/agents/agent-architect.agent.md`. They are
ordinary Agent Graph assets, so `wd agents discover`, `explain`, and
`impact` can inspect them before future customization changes.

### Edge provenance with `props.source`

`wd add-edge` accepts a strict set of edge types (see
`weld.contract.VALID_EDGE_TYPES`). When an agent, tool, or LLM emits an
edge, stamp its origin under `props.source` so downstream consumers can
filter, rank, or audit tool-generated relationships. The `--props` help
text carries the canonical example: `--props '{"source":"llm","confidence":"inferred"}'`.
The `source` value is free-form (agent name, tool name, `llm`,
`manual`, strategy id); `confidence` follows the existing vocabulary
(`definite`, `inferred`, `speculative`). This replaces the 0.3.0-era
`--source` and `--relation` flags.

## Examples

- [01-python-fastapi](examples/01-python-fastapi/) — discover a FastAPI
  project: routes, Pydantic models, module structure
- [02-custom-strategy](examples/02-custom-strategy/) — write a project-local
  strategy plugin that extracts TODO/FIXME comments
- [04-monorepo-typescript](examples/04-monorepo-typescript/) — discover a
  TypeScript monorepo: workspace packages, cross-package imports, shared types
- [05-polyrepo](examples/05-polyrepo/) — set up a federated polyrepo
  workspace: workspaces.yaml, cross-repo discovery, workspace status
- [agent-graph-demo](examples/agent-graph-demo/) — inspect mixed AI
  customization assets with `wd agents discover`, `list`, `audit`,
  `explain`, `impact`, and `plan-change`

For a tour of what each command above actually prints, see
[Graph visualization examples](docs/visualization-examples.md) — real
terminal snippets captured against `wd 0.11.0`.

## Install

### Recommended: `uv tool install`

```bash
uv tool install configflux-weld

# Verify
wd --version
```

This is the single recommended install path. `uv tool install` puts
`wd` on your `PATH` in an isolated environment, is fast, and gives you a
clear update story:

```bash
uv tool upgrade configflux-weld   # or: uv tool upgrade --all
```

Don't have `uv` yet? See the [uv install
instructions](https://docs.astral.sh/uv/getting-started/installation/).

To run the stdio MCP server, install the optional MCP extra:

```bash
uv tool install "configflux-weld[mcp]"
python -m weld.mcp_server --help
```

`wd mcp config` does not require the extra; only the server process does.

### Alternative install paths

The paths below are supported but secondary. Prefer `uv tool install` unless
you have a concrete reason to pick one of these.

#### `pipx` (if you already standardize on pipx)

```bash
pipx install configflux-weld
wd --version
```

Functionally equivalent to `uv tool install` for end users. Use whichever
tool manager your team already has.

#### `install.sh` (zero-dependency bootstrap)

```bash
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
```

`install.sh` is a POSIX shell script that detects a compatible Python (3.10
through 3.13) and installs via `uv`, `pipx`, or `pip --user`, in that order
of preference. Use it only when you don't have `uv` or `pipx` available and
can't install them first — for example, on a minimal CI image or a
locked-down host. It is idempotent (re-running upgrades an existing install)
and honours a `.weld-version` file in the current directory or any ancestor
to pin a specific release tag.

#### From a local checkout (development)

If you want to edit Weld itself, use a source-checkout install. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the full developer setup, including
editable installs and optional-extras commands for `tree-sitter`, `mcp`,
`openai`, `anthropic`, `ollama`, and `llm`.

#### From a Git URL

```bash
pip install "git+https://github.com/configflux/weld.git@main#subdirectory=weld"
```

Useful for pinning an unreleased commit or branch.

#### Raw source (no install)

If you cannot install anything, the module entrypoint works from a plain
checkout:

```bash
python -m weld --help
```

### Python compatibility

Runtime installs support Python 3.10 through 3.13. Contributor builds and
Bazel tests use the Python 3.12 toolchain pinned in `MODULE.bazel`, so the
development toolchain can be narrower than the runtime support window.

## Release policy

`main` is the source of truth for the next release: the version recorded
in [`VERSION`](VERSION) and `weld/pyproject.toml` matches the latest
`publish/vX.Y.Z` git tag, except during a deliberately-staged window
where `main` is bumped ahead of the latest tag.

The drift shape that produced the v0.9.0 and v0.10.1 incidents -- `main`
silently regressing below the latest published wheel -- is now caught
post-release by `tools/check_main_release_consistency.py` (ADR 0015
check 11; runs as part of `/release-audit`). To document a deliberate
"`main` is ahead of the latest tag" window, add a comment marker to
this README:

```html
<!-- release-lag: 0.11.0 staged for 2026-05-12 launch window -->
```

The check then turns the lag into a `WARN` and surfaces the reason
instead of failing. Remove the marker when the matching tag is cut.
See [`docs/release.md`](docs/release.md) for the full release
checklist (the post-release consistency check is step 9).

## Documentation

- [Full toolkit guide](weld/README.md) — architecture, design limits,
  roadmap
- [Onboarding guide](weld/docs/onboarding.md)
- [Agent workflow](weld/docs/agent-workflow.md) — when to use each
  retrieval surface
- [Agent Graph](docs/agent-graph.md) — static map of the AI
  customization layer (agents, skills, prompts, hooks, MCP servers)
- [Graph visualization examples](docs/visualization-examples.md) —
  real terminal output: monorepo graph, polyrepo `repo:` nodes,
  Agent Graph, MCP config snippet
- [Platform support matrix](docs/platform-support.md) — per-platform
  support and runtime-validation status
- [Performance notes](docs/performance.md) — discovery and query
  timings on synthetic 1k/10k/100k single repos and polyrepo workspaces,
  with a reproducible recipe
- [Strategy cookbook](weld/docs/strategy-cookbook.md)
- [Glossary](weld/docs/glossary.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Weld is currently maintainer-led.
Issues, bug reports, demo repos, documentation improvements, and strategy
proposals are welcome. For larger changes, please open an issue first so we
can align on scope before implementation.

## License

Apache License, Version 2.0 — see [LICENSE](LICENSE) for details.
