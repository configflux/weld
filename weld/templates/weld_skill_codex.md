# Weld — Repository Connected Structure

## What it is

`weld` is a connected structure toolkit that maps the entire repository — code,
docs, infra, build, policy, tests, and operations — into a queryable graph.
Use it to answer "where does this live?", "what depends on what?", and
"which docs or policies apply here?" without grepping across the codebase.

## When to use it

- Before starting work on a new area of the codebase
- When you need to understand dependencies, boundaries, or data flow
- When looking for authoritative documentation or policies
- When checking which build, test, or verification surfaces matter for a change

## Retrieval commands

Start with `wd brief` — it returns a ranked, classified context packet
designed for agent consumption.

| Command | Purpose |
|---------|---------|
| `wd brief <term>` | Default starting point — ranked context with docs, build surfaces, boundaries |
| `wd query <term>` | Broader tokenized search when brief is too narrow |
| `wd context <node-id>` | Deep dive — node details plus immediate neighborhood |
| `wd path <from> <to>` | Shortest path between two nodes (dependency/data-flow tracing) |
| `wd find <keyword>` | File-level keyword search using the inverted index |

### Usage pattern

```bash
# 1. Start with brief for structured context
wd brief "user authentication"

# 2. Drill into a specific node
wd context entity:User

# 3. Trace a path
wd path entity:User route:login

# 4. Find files by keyword
wd find "session"
```

## Maintenance commands

| Command | Purpose |
|---------|---------|
| `wd prime` | Check setup status and get next-step guidance |
| `wd stale` | Quick freshness check against git HEAD |
| `wd discover > .weld/graph.json` | Rebuild the graph from source |
| `wd build-index` | Rebuild the keyword file index |
| `wd init` | Bootstrap `.weld/discover.yaml` for a new project |
| `wd stats` | Graph summary (node/edge counts, description coverage) |

## When to refresh

- After significant code changes (new modules, renamed files, deleted surfaces)
- When `wd stale` reports the graph is behind HEAD
- When `wd prime` suggests a refresh
- Before starting a major implementation task in an unfamiliar area

## Setup

If `weld` is not yet installed:

```bash
pip install -e ./weld    # from the monorepo root
wd prime               # check what needs to be done
wd bootstrap codex     # set up Codex integration
```

Codex reads MCP servers from `.codex/config.toml`. `wd bootstrap codex`
writes that file with both the local `weld` MCP server and Context7.
