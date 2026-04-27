---
name: weld
description: >
  Repository connected structure toolkit (weld / wd). Use for workspace graph
  queries, discovery wave runs, repo map browsing, and query graph lookups.
  Activates on mentions of weld, wd, workspace graph, discovery wave, repo
  map, query graph, federation, and polyrepo workspaces. Maps code, docs,
  infra, build, policy, tests, and operations into a queryable graph. Use it
  to answer "where does this live?", "what depends on what?", and "which
  docs or policies apply here?" without grepping across the codebase.
allowed-tools:
  - shell
---

# Weld -- Repository Connected Structure

## When to use it

- Before starting work on a new area of the codebase
- When you need to understand dependencies, boundaries, or data flow
- When looking for authoritative documentation or policies
- When checking which build, test, or verification surfaces matter for a change

## Retrieval commands

<!-- weld-managed:start name=retrieval-commands -->
Start with `wd brief` -- it returns a ranked, classified context packet
designed for agent consumption.

| Command | Purpose |
|---------|---------|
| `wd brief <term>` | Default starting point -- ranked context with docs, build surfaces, boundaries |
| `wd query <term>` | Broader tokenized search when brief is too narrow |
| `wd context <node-id>` | Deep dive -- node details plus immediate neighborhood |
| `wd path <from> <to>` | Shortest path between two nodes (dependency/data-flow tracing) |
| `wd find <keyword>` | File-level keyword search using the inverted index |
<!-- weld-managed:end name=retrieval-commands -->

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

<!-- weld-managed:start name=maintenance-commands -->
| Command | Purpose |
|---------|---------|
| `wd prime` | Check setup status and get next-step guidance |
| `wd stale` | Quick freshness check against git HEAD |
| `wd discover --output .weld/graph.json` | Rebuild the graph from source |
| `wd build-index` | Rebuild the keyword file index |
| `wd init` | Bootstrap `.weld/discover.yaml` for a new project |
| `wd graph stats` | Graph summary (node/edge counts, description coverage) |
<!-- weld-managed:end name=maintenance-commands -->

## Trust boundary

<!-- weld-managed:start name=trust-boundary -->
Run `wd discover` automatically only on repositories you trust. Project-local
strategies under `.weld/strategies/` are Python modules loaded at discovery
time, and `strategy: external_json` executes configured commands from
`discover.yaml`.
<!-- weld-managed:end name=trust-boundary -->

## When to refresh

- After significant code changes (new modules, renamed files, deleted surfaces)
- When `wd stale` reports the graph is behind HEAD
- When `wd prime` suggests a refresh
- Before starting a major implementation task in an unfamiliar area

## Setup

If `weld` is not yet installed:

```bash
uv tool install configflux-weld          # README has install alternatives
wd prime                                 # check what needs to be done
wd bootstrap copilot --cli-only          # set up Copilot integration (CLI only)
```
