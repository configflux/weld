# Weld

Weld the pieces of agent work into one connected structure.

ConfigFlux Weld is a small agent-first tool for linking context, actions, and
knowledge so work can continue without losing structure.

Weld helps people and agents:

- Where does this capability live?
- Which docs or policies are authoritative for this area?
- What build, test, or operational surfaces matter for this change?
- What boundaries or entrypoints constrain the implementation?

## Key features

- **Whole-codebase discovery** — not just source code. Covers docs, config,
  CI workflows, infrastructure, and build files.
- **Config-driven** — point `.weld/discover.yaml` at your repo and tune
  what gets extracted.
- **Multi-language** — bundled tree-sitter strategies for Python, TypeScript/JS,
  Go, Rust, C#, C++, and ROS2.
- **Plugin architecture** — drop a `.py` file in `.weld/strategies/` to
  extract anything repo-specific.
- **Agent-native** — ships an MCP server so Claude Code, Codex, and other
  agents can query the graph directly.
- **Zero external dependencies** — runs from a plain checkout with Python >= 3.10.
  Tree-sitter is optional.

## Quickstart

```bash
# Install (curl | sh — detects uv/pipx/pip automatically)
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh

# Bootstrap config for your repo
wd init

# Run discovery and save the graph
wd discover > .weld/graph.json

# Query the graph
wd query "authentication"
wd find "login"
wd context file:src/auth/handler
wd viz --no-open
wd stale
```

See [Install](#install) for alternatives (local checkout, pip, raw source).

### Agent-first onboarding

If an agent or coding assistant is driving setup, use the short bootstrap
path:

```bash
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
wd prime                  # show setup status + per-framework surface matrix
wd bootstrap claude       # writes .claude/commands/weld.md
wd bootstrap codex        # writes .codex/skills/weld/SKILL.md + .codex/config.toml
wd bootstrap copilot      # writes .github/skills/weld/SKILL.md + .github/instructions/weld.instructions.md
```

All three `wd bootstrap` frameworks accept opt-out flags:

- `--no-mcp` — skip the MCP pair (`.codex/config.toml` for codex; the `.mcp.json` guidance block for copilot/claude).
- `--no-enrich` — write the `.cli.md` variant that omits `wd enrich`.
- `--cli-only` — shorthand for `--no-mcp --no-enrich`.

`wd prime` is idempotent and safe to re-run — it reports what is
already configured and what is still missing.

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
pip install -e "weld/[tree-sitter]"
```

To use the built-in semantic enrichment providers:

```bash
pip install -e "weld/[openai]"     # or [anthropic], [ollama], or [llm]
```

Agents can also enrich nodes without provider extras or API keys by reading the
relevant source or documentation and writing reviewed enrichment manually:

```bash
wd stale
wd context "<node-id>"
wd add-node "<node-id>" --type "<node-type>" --label "<label>" --merge --props '{"description":"...","purpose":"...","enrichment":{"provider":"manual","model":"agent-reviewed","timestamp":"<ISO-8601 UTC timestamp>","description":"...","purpose":"...","suggested_tags":["lowercase","tags"]}}'
wd validate
wd stats
```

Manual enrichment writes `.weld/graph.json` directly and can be overwritten by
a later `wd discover > .weld/graph.json`; refresh discovery before manual
edits. Manual inferred edges should use explicit provenance such as
`{"source": "manual"}` after the relationship is verified from source content.

Without tree-sitter, the built-in Python module strategy and non-language
strategies (markdown, YAML, config, frontmatter) still work.

## Agent integration

Weld ships an MCP server that exposes the connected structure as structured
tool calls:

| Tool | Description |
|---|---|
| `weld_query(term)` | Hybrid-ranked tokenized graph search |
| `weld_find(term)` | File-index substring search |
| `weld_context(node_id)` | Node + 1-hop neighborhood |
| `weld_path(from, to)` | Shortest path between nodes |
| `weld_impact(target)` | Reverse-dependency blast-radius analysis |
| `weld_enrich(node_id?, provider?)` | Built-in semantic enrichment with provider-backed LLMs |
| `weld_brief(area)` | High-level agent context packet |
| `weld_stale()` | Graph freshness check |

### Setup for an agent

For Codex, add MCP servers in `.codex/config.toml` (or run `wd bootstrap codex`):

```toml
[mcp_servers.weld]
command = "python"
args = ["-m", "weld.mcp_server"]
```

For agents that read `.mcp.json`, use:

```json
{
  "mcpServers": {
    "weld": {
      "command": "python",
      "args": ["-m", "weld.mcp_server"]
    }
  }
}
```

Then bootstrap onboarding files for your agent framework:

```bash
wd bootstrap claude     # writes .claude/commands/weld.md
wd bootstrap codex      # writes .codex/skills/weld/SKILL.md + .codex/config.toml
wd bootstrap copilot    # writes .github/skills/weld/SKILL.md + .github/instructions/weld.instructions.md
```

Each target also writes `.weld/README.md` and bootstraps
`.weld/discover.yaml` if missing. Run `wd prime` afterwards to
confirm setup.

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
wd discover > .weld/graph.json
```

When `workspaces.yaml` is present, `wd discover` operates in federation
mode. It reads each child's `.weld/graph.json`, builds `repo:<name>` nodes
for every present child, and runs the declared cross-repo resolvers to emit
edges between children. Children that are missing, uninitialized, or corrupt
degrade gracefully -- they are skipped and recorded in the workspace ledger
but do not block discovery.

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
| `wd init` | Bootstrap `.weld/discover.yaml` (and `workspaces.yaml` when nested repos are detected) |
| `wd init --max-depth N` | Limit nested repo scan depth during init (default: 4) |
| `wd discover` | Run discovery, emit graph JSON (federation mode when `workspaces.yaml` is present) |
| `wd workspace status` | Show workspace child ledger: lifecycle status, git ref, dirty state |
| `wd workspace status --json` | Emit the raw `workspace-state.json` payload |
| `wd build-index` | Regenerate file index |
| `wd query <term>` | Hybrid-ranked tokenized graph search |
| `wd find <term>` | File-index keyword search |
| `wd context <id>` | Node + neighborhood |
| `wd path <from> <to>` | Shortest path |
| `wd impact <path-or-node>` | Reverse-dependency blast radius |
| `wd callers <symbol>` | Direct/transitive callers |
| `wd viz` | Local read-only browser graph explorer |
| `wd stale` | Check graph freshness |
| `wd stats` | Graph statistics |
| `wd prime` | Setup status + per-framework agent surface matrix (skill / instruction / mcp) with fix commands |
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

## Examples

- [01-python-fastapi](examples/01-python-fastapi/) — discover a FastAPI
  project: routes, Pydantic models, module structure
- [02-custom-strategy](examples/02-custom-strategy/) — write a project-local
  strategy plugin that extracts TODO/FIXME comments
- [05-polyrepo](examples/05-polyrepo/) — set up a federated polyrepo
  workspace: workspaces.yaml, cross-repo discovery, workspace status

## Install

### Quick install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
```

`install.sh` is a POSIX shell script that detects a compatible Python
(3.10 through 3.13) and installs via `uv`, `pipx`, or `pip --user`, in
that order of preference. It is idempotent — re-running upgrades an
existing installation — and honours a `.weld-version` file in the
current directory or any ancestor to pin a specific release tag. This is
the fast path for end users and agents.

### From a local checkout (development)

Use this when you want to edit Weld locally or contribute changes:

```bash
pip install -e weld/
wd --help
```

For tree-sitter language support (Go, Rust, TypeScript, C++):

```bash
pip install -e "weld/[tree-sitter]"
```

### From GitHub

```bash
pip install "git+ssh://git@github.com/configflux/weld.git@main#subdirectory=weld"
```

### Raw source (no install)

If you cannot install anything, the module entrypoint works from a plain
checkout:

```bash
python -m weld --help
```

### When to use which

| Path | Use when |
|---|---|
| `install.sh` | End users and agents — the quickest supported setup. |
| `pip install -e weld/` | Local development on weld itself. |
| `pip install "git+ssh://..."` | Reproducible installs from a branch or tag, without running a shell script. |
| `python -m weld` | A plain checkout with no install step, e.g. inside a locked-down container. |

## Documentation

- [Full toolkit guide](weld/README.md) — architecture, design limits,
  roadmap
- [Onboarding guide](weld/docs/onboarding.md)
- [Agent workflow](weld/docs/agent-workflow.md) — when to use each
  retrieval surface
- [Strategy cookbook](weld/docs/strategy-cookbook.md)
- [Glossary](weld/docs/glossary.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). This project is maintainer-driven
and is not currently accepting external pull requests. Bug reports and
feature requests are welcome as GitHub issues.

## License

Apache License, Version 2.0 — see [LICENSE](LICENSE) for details.
