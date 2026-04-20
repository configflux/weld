# Weld Agent Workflow: Retrieval Surfaces

This guide explains when and how to use each `weld` retrieval command during
repository work. It is written for both human operators and LLM agents that
include `weld` in their prompt context.

## The five retrieval surfaces

| Command | Purpose | Input | Output |
|---------|---------|-------|--------|
| `wd brief` | One-shot context packet for a task | free-text term | Classified JSON: primary, docs, build, boundaries, edges, provenance |
| `wd query` | Keyword search across the graph | free-text term | Ranked matches + neighbors + connecting edges |
| `wd context` | Neighborhood of a known node | exact node ID | The node + all neighbors + connecting edges |
| `wd path` | Shortest path between two nodes | two node IDs | Ordered node chain + edges |
| `wd find` | File-index keyword search | free-text term | File paths with token hits from the inverted index |

Examples in this guide use the installed `weld` command. If you are working
from a raw source checkout without installing `weld` first, replace `weld`
with `python -m weld`.

## Start with `brief`

For most agent workflows, `wd brief` is the right first call. It runs the same
tokenized search as `query` but classifies results into buckets that map
directly to the questions agents need answered:

- **primary** -- implementation nodes that match the task
- **docs** -- authoritative docs, ADRs, policies, runbooks
- **build** -- build targets, test targets, gates
- **boundaries** -- system boundaries, entrypoints

Each bucket is ranked by authority and confidence so that canonical, definite
nodes appear before derived or speculative ones. The output includes provenance
(graph SHA, timestamp) so the agent knows how fresh the data is.

```bash
wd brief "stores page"
```

Use `brief` when you need a broad orientation on a topic and want pre-classified
context without manual filtering.

## When to drop to low-level surfaces

`brief` covers the common case. Drop to a specific command when you need
something `brief` does not provide.

### `query` -- when you need raw ranked matches

Use `query` when:

- You want to see all matching nodes without classification
- You need to tune the `--limit` for a broader or narrower search
- You are exploring a term and want to inspect raw results before deciding
  what to do next

```bash
wd query "user consent" --limit 30
```

`query` returns `matches`, `neighbors`, and `edges`. It keeps the
all-tokens-must-match filter, then ranks candidates with BM25 lexical
relevance, optional enrichment-text similarity, in-degree centrality, and
authority metadata.
Confidence and node ID remain deterministic tie-breakers. It does not classify
nodes into doc/build/boundary buckets -- that is what `brief` adds on top.

At a workspace root with `.weld/workspaces.yaml`, child matches are namespaced
as `<child>\x1f<node-id>`. The CLI JSON also includes `display_id` and
`*_display` fields rendered as `<child>::<node-id>` for easier reading.

### `context` -- when you already know the node

Use `context` when:

- You have an exact node ID (from a prior `query`, `brief`, or `find`)
- You want the full neighborhood of that specific node
- You need to understand what a single node connects to

```bash
wd context file:services/api/src/myapp/api/routers/user.py
```

`context` returns the node itself, all immediate neighbors, and all connecting
edges. It is the right tool for drilling into a specific artifact after
identifying it through `brief` or `query`.

In federated workspaces, `context` accepts either the canonical `\x1f`-prefixed
ID or the display form shown in `display_id`.

### `path` -- when you need the relationship chain

Use `path` when:

- You know two nodes and want to understand how they connect
- You are investigating a dependency chain or ownership relationship
- You need to verify that two components are related in the graph

```bash
wd path file:apps/web/app/stores/page.tsx entity:Store
```

`path` returns the shortest path as an ordered list of nodes plus the edges
connecting them, or `null` if no path exists. It is a targeted investigation
tool, not a discovery tool.

In federated workspaces, `path` also accepts the display form
`<child>::<node-id>` for convenience.

### `find` -- when you need file paths, not graph nodes

Use `find` when:

- You want to locate files by name, token, or path fragment
- You need to find files that may not have rich graph nodes yet
- You are looking for a file to open, not a graph relationship

```bash
wd find "footer"
```

`find` searches the inverted file index (`.weld/file-index.json`), not the graph.
It covers common source, config, build, and documentation text surfaces and
matches against path segments, known language tokens, and generic file tokens.
Use it when you need file paths rather than graph-level context. Graph
discovery remains driven by `.weld/discover.yaml`, so broad `find` hits do not
mean those files have rich graph nodes.

## Trust boundary for refreshes

Run `wd discover` automatically only on repositories you trust. Project-local
strategies under `.weld/strategies/` are Python modules loaded at discovery
time, and `strategy: external_json` executes configured commands from
`discover.yaml` with the repository root as the working directory.

## Typical agent workflow

This is the recommended sequence for an agent beginning work on a task.

### Step 1: Check graph freshness

```bash
wd stale
```

If the graph is stale (commits behind > 0), refresh before querying:

```bash
wd discover > .weld/graph.json
wd build-index
```

### Step 2: Get oriented with `brief`

```bash
wd brief "<task title or key terms>"
```

Read the classified output. The `primary` section tells you which
implementation nodes are relevant. The `docs` section points to authoritative
documentation. The `build` section identifies verification surfaces. The
`boundaries` section shows system seams you might cross.

Check `provenance` to confirm the graph is fresh enough for your task.
Check `warnings` for any diagnostic messages (e.g., no matches found).

### Step 3: Drill into specifics

Based on what `brief` returned, drill into individual nodes:

```bash
# Inspect a specific node's neighborhood
wd context <node-id-from-brief>

# Understand how two nodes relate
wd path <node-a> <node-b>
```

### Step 4: Find files to edit

If you need to locate actual files (not graph relationships):

```bash
wd find "<term>"
```

This is especially useful when the graph node does not carry a `props.file`
field, or when you are looking for files by name pattern.

## Manual enrichment

Provider-backed `wd enrich` is optional. If provider extras or API keys are
unavailable, an agent can manually enrich a node after reading the underlying
content.

Start by checking freshness and loading the node:

```bash
wd stale
wd context "<node-id>"
```

Read `props.file` when present, or use the node's real neighboring docs,
config, or source when no file is attached. Preserve the node ID, type, and
label from `wd context`, then merge reviewed enrichment:

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
a later `wd discover > .weld/graph.json`. Refresh discovery before manual
edits, then validate after writing:

```bash
wd validate
wd stats
```

## Decision matrix

| Question | Command |
|----------|---------|
| What does the graph know about this topic? | `brief` |
| Which nodes match these keywords? | `query` |
| What connects to this specific node? | `context` |
| How do these two nodes relate? | `path` |
| Where is the file for this? | `find` |
| Is the graph up to date? | `stale` |

## Tips for prompt authors

When writing agent prompts that use `weld`:

1. **Default to `brief`** -- it handles the 80% case and returns structured,
   pre-classified output that is easy to parse.

2. **Check staleness** -- always call `stale` before relying on graph data.
   A stale graph can mislead an agent about what exists or where it lives.

3. **Pass exact node IDs** -- `context` and `path` require exact node IDs,
   not fuzzy terms. Get node IDs from `brief` or `query` output first.

4. **Use `find` for file discovery** -- when the goal is "find me the file,"
   `find` searches a broader token space than graph node IDs alone.

5. **Keep queries focused** -- multi-word terms are tokenized, and all tokens
   must match. Broader queries (fewer tokens) return more results; narrow
   queries (more tokens) return more precise results.

6. **Respect the JSON contract** -- `brief` output follows a stable versioned
   contract (`brief_version: 1`). Parse the structured fields rather than
   scraping text.
