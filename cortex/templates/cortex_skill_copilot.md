---
name: cortex
description: >
  Repository knowledge graph toolkit. Maps code, docs, infra, build, policy,
  tests, and operations into a queryable graph. Use it to answer
  "where does this live?", "what depends on what?", and "which docs or
  policies apply here?" without grepping across the codebase.
allowed-tools:
  - shell
---

# Cortex -- Repository Knowledge Graph

## When to use it

- Before starting work on a new area of the codebase
- When you need to understand dependencies, boundaries, or data flow
- When looking for authoritative documentation or policies
- When checking which build, test, or verification surfaces matter for a change

## Retrieval commands

Start with `cortex brief` -- it returns a ranked, classified context packet
designed for agent consumption.

| Command | Purpose |
|---------|---------|
| `cortex brief <term>` | Default starting point -- ranked context with docs, build surfaces, boundaries |
| `cortex query <term>` | Broader tokenized search when brief is too narrow |
| `cortex context <node-id>` | Deep dive -- node details plus immediate neighborhood |
| `cortex path <from> <to>` | Shortest path between two nodes (dependency/data-flow tracing) |
| `cortex find <keyword>` | File-level keyword search using the inverted index |

### Usage pattern

```bash
# 1. Start with brief for structured context
cortex brief "user authentication"

# 2. Drill into a specific node
cortex context entity:User

# 3. Trace a path
cortex path entity:User route:login

# 4. Find files by keyword
cortex find "session"
```

## Maintenance commands

| Command | Purpose |
|---------|---------|
| `cortex prime` | Check setup status and get next-step guidance |
| `cortex stale` | Quick freshness check against git HEAD |
| `cortex discover > .cortex/graph.json` | Rebuild the graph from source |
| `cortex build-index` | Rebuild the keyword file index |
| `cortex init` | Bootstrap `.cortex/discover.yaml` for a new project |
| `cortex stats` | Graph summary (node/edge counts, description coverage) |

## When to refresh

- After significant code changes (new modules, renamed files, deleted surfaces)
- When `cortex stale` reports the graph is behind HEAD
- When `cortex prime` suggests a refresh
- Before starting a major implementation task in an unfamiliar area

## Setup

If `cortex` is not yet installed:

```bash
pip install -e ./cortex    # from the monorepo root
cortex prime               # check what needs to be done
cortex bootstrap copilot   # set up Copilot integration
```
