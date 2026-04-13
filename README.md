# Cortex

A portable knowledge-graph toolkit that maps your entire codebase — code,
docs, config, infrastructure, build targets — into a queryable graph.

Cortex helps humans and LLM agents answer questions like:

- Where does this capability live?
- Which docs or policies are authoritative for this area?
- What build, test, or operational surfaces matter for this change?
- What boundaries or entrypoints constrain the implementation?

## Key features

- **Whole-codebase discovery** — not just source code. Covers docs, config,
  CI workflows, infrastructure, and build files.
- **Config-driven** — point `.cortex/discover.yaml` at your repo and tune
  what gets extracted.
- **Multi-language** — bundled tree-sitter strategies for Python, TypeScript/JS,
  Go, Rust, C++, and ROS2.
- **Plugin architecture** — drop a `.py` file in `.cortex/strategies/` to
  extract anything repo-specific.
- **Agent-native** — ships an MCP server so Claude Code, Codex, and other
  agents can query the graph directly.
- **Zero external dependencies** — runs from a plain checkout with Python >= 3.10.
  Tree-sitter is optional.

## Quickstart

```bash
# Install
pip install -e cortex/

# Bootstrap config for your repo
cortex init

# Run discovery and save the graph
cortex discover > .cortex/graph.json

# Query the graph
cortex query "authentication"
cortex find "login"
cortex context file:src/auth/handler
cortex stale
```

## Supported languages

All language strategies use tree-sitter and degrade gracefully when the
grammar package is not installed.

| Language | Extraction surface | Grammar package |
|---|---|---|
| Python | modules, classes, functions, imports, call graph | `tree-sitter-python` |
| TypeScript / JS | exports, classes, imports | `tree-sitter-typescript` |
| Go | exports, types, imports | `tree-sitter-go` |
| Rust | exports, types, imports | `tree-sitter-rust` |
| C++ | exports, classes, imports, best-effort call graph | `tree-sitter-cpp` |
| ROS2 | packages, nodes, topics, services, actions, parameters | (reuses Python + C++) |

To enable tree-sitter support:

```bash
pip install -e "cortex/[tree-sitter]"
```

Without tree-sitter, the built-in Python module strategy and non-language
strategies (markdown, YAML, config, frontmatter) still work.

## Agent integration

Cortex ships an MCP server that exposes the knowledge graph as structured
tool calls:

| Tool | Description |
|---|---|
| `cortex_query(term)` | Ranked tokenized search |
| `cortex_find(term)` | File-index substring search |
| `cortex_context(node_id)` | Node + 1-hop neighborhood |
| `cortex_path(from, to)` | Shortest path between nodes |
| `cortex_brief(area)` | High-level agent context packet |
| `cortex_stale()` | Graph freshness check |

### Setup for Claude Code

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "cortex": {
      "command": "python",
      "args": ["-m", "cortex.mcp_server"]
    }
  }
}
```

Then bootstrap agent onboarding files:

```bash
cortex bootstrap claude    # writes .claude/commands/cortex.md
cortex bootstrap codex     # writes .codex/skills/cortex/SKILL.md
```

## Discovery configuration

Cortex is driven by `.cortex/discover.yaml`. Each entry maps a file pattern
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

Run `cortex init` to generate a starter config, or write one by hand. See
the [Strategy Cookbook](cortex/docs/strategy-cookbook.md) for the full list
of bundled strategies.

### Custom strategies

Drop a Python file in `.cortex/strategies/` to extract repo-specific
artifacts. The strategy signature:

```python
def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    ...
```

See [examples/02-custom-strategy](examples/02-custom-strategy/) for a
working example that extracts TODO comments as graph nodes.

## CLI reference

| Command | Description |
|---|---|
| `cortex init` | Bootstrap `.cortex/discover.yaml` |
| `cortex discover` | Run discovery, emit graph JSON |
| `cortex build-index` | Regenerate file index |
| `cortex query <term>` | Tokenized graph search |
| `cortex find <term>` | File-index keyword search |
| `cortex context <id>` | Node + neighborhood |
| `cortex path <from> <to>` | Shortest path |
| `cortex callers <symbol>` | Direct/transitive callers |
| `cortex stale` | Check graph freshness |
| `cortex stats` | Graph statistics |
| `cortex prime` | Setup status and next steps |
| `cortex scaffold` | Write starter templates |
| `cortex bootstrap` | Agent onboarding files |
| `cortex brief` | Agent context briefing |
| `cortex enrich` | LLM-assisted semantic enrichment |

Run `cortex --help` for the full list.

## Examples

- [01-python-fastapi](examples/01-python-fastapi/) — discover a FastAPI
  project: routes, Pydantic models, module structure
- [02-custom-strategy](examples/02-custom-strategy/) — write a project-local
  strategy plugin that extracts TODO/FIXME comments

## Install

### From a local checkout

```bash
pip install -e cortex/
cortex --help
```

### From GitHub

```bash
pip install "git+ssh://git@github.com/configflux/cortex.git@main#subdirectory=cortex"
```

### Raw source (no install)

```bash
python -m cortex --help
```

## Documentation

- [Full toolkit guide](cortex/README.md) — architecture, design limits,
  roadmap
- [Onboarding guide](cortex/docs/onboarding.md)
- [Agent workflow](cortex/docs/agent-workflow.md) — when to use each
  retrieval surface
- [Strategy cookbook](cortex/docs/strategy-cookbook.md)
- [Glossary](cortex/docs/glossary.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). This project is maintainer-driven
and is not currently accepting external pull requests. Bug reports and
feature requests are welcome as GitHub issues.

## License

Apache License, Version 2.0 — see [LICENSE](LICENSE) for details.
