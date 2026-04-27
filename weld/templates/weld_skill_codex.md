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

<!-- weld-managed:start name=retrieval-commands -->
Start with `wd brief` — it returns a ranked, classified context packet
designed for agent consumption.

| Command | Purpose |
|---------|---------|
| `wd brief <term>` | Default starting point — ranked context with docs, build surfaces, boundaries |
| `wd query <term>` | Broader tokenized search when brief is too narrow |
| `wd context <node-id>` | Deep dive — node details plus immediate neighborhood |
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

## Manual enrichment

If provider extras or API keys are unavailable, agents can manually enrich a
node after reading the underlying content. Start by checking freshness and
loading the node:

```bash
wd stale
wd context "<node-id>"
```

Read `props.file` when present, or use the node's real neighboring docs,
config, or source when no file is attached. Then preserve the node ID, type,
and label from `wd context` and merge reviewed enrichment:

```bash
wd add-node "<node-id>" --type "<node-type>" --label "<label>" --merge --props '{
  "description": "One concise factual sentence describing what the node is.",
  "purpose": "One concise factual sentence describing why it exists.",
  "enrichment": {
    "provider": "manual",
    "model": "agent-reviewed",
    "timestamp": "<ISO-8601 UTC timestamp>",
    "description": "One concise factual sentence describing what the node is.",
    "purpose": "One concise factual sentence describing why it exists.",
    "suggested_tags": ["lowercase", "tags"]
  }
}'
```

Optional enrichment fields follow the provider-backed schema:
`complexity_hint` may be `low`, `medium`, or `high`, and `suggested_tags`
should be lowercase strings. Manual inferred edges must use explicit
provenance such as `{"source": "manual"}` and only be added after verifying
the relationship from source content.

Manual enrichment writes `.weld/graph.json` directly and can be overwritten by
a later `wd discover --output .weld/graph.json`. Refresh discovery first, then
validate after manual edits:

```bash
wd graph validate
wd graph stats
```

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
