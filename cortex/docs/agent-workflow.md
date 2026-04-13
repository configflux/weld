# Cortex Agent Workflow: Retrieval Surfaces

This guide explains when and how to use each `cortex` retrieval command during
repository work. It is written for both human operators and LLM agents that
include `cortex` in their prompt context.

## The five retrieval surfaces

| Command | Purpose | Input | Output |
|---------|---------|-------|--------|
| `cortex brief` | One-shot context packet for a task | free-text term | Classified JSON: primary, docs, build, boundaries, edges, provenance |
| `cortex query` | Keyword search across the graph | free-text term | Ranked matches + neighbors + connecting edges |
| `cortex context` | Neighborhood of a known node | exact node ID | The node + all neighbors + connecting edges |
| `cortex path` | Shortest path between two nodes | two node IDs | Ordered node chain + edges |
| `cortex find` | File-index keyword search | free-text term | File paths with token hits from the inverted index |

Examples in this guide use the installed `cortex` command. If you are working
from a raw source checkout without installing `cortex` first, replace `cortex`
with `python -m cortex`.

## Start with `brief`

For most agent workflows, `cortex brief` is the right first call. It runs the same
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
cortex brief "stores page"
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
cortex query "user consent" --limit 30
```

`query` returns `matches`, `neighbors`, and `edges`. It does not classify nodes
into doc/build/boundary buckets -- that is what `brief` adds on top.

### `context` -- when you already know the node

Use `context` when:

- You have an exact node ID (from a prior `query`, `brief`, or `find`)
- You want the full neighborhood of that specific node
- You need to understand what a single node connects to

```bash
cortex context file:services/api/src/myapp/api/routers/user.py
```

`context` returns the node itself, all immediate neighbors, and all connecting
edges. It is the right tool for drilling into a specific artifact after
identifying it through `brief` or `query`.

### `path` -- when you need the relationship chain

Use `path` when:

- You know two nodes and want to understand how they connect
- You are investigating a dependency chain or ownership relationship
- You need to verify that two components are related in the graph

```bash
cortex path file:apps/web/app/stores/page.tsx entity:Store
```

`path` returns the shortest path as an ordered list of nodes plus the edges
connecting them, or `null` if no path exists. It is a targeted investigation
tool, not a discovery tool.

### `find` -- when you need file paths, not graph nodes

Use `find` when:

- You want to locate files by name, token, or path fragment
- You need to find files that may not have rich graph nodes yet
- You are looking for a file to open, not a graph relationship

```bash
cortex find "footer"
```

`find` searches the inverted file index (`.cortex/file-index.json`), not the graph.
It matches against path segments, exported symbols, class/function names, import
targets, and markdown headings. Use it when you need file paths rather than
graph-level context.

## Typical agent workflow

This is the recommended sequence for an agent beginning work on a task.

### Step 1: Check graph freshness

```bash
cortex stale
```

If the graph is stale (commits behind > 0), refresh before querying:

```bash
cortex discover > .cortex/graph.json
cortex build-index
```

### Step 2: Get oriented with `brief`

```bash
cortex brief "<task title or key terms>"
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
cortex context <node-id-from-brief>

# Understand how two nodes relate
cortex path <node-a> <node-b>
```

### Step 4: Find files to edit

If you need to locate actual files (not graph relationships):

```bash
cortex find "<term>"
```

This is especially useful when the graph node does not carry a `props.file`
field, or when you are looking for files by name pattern.

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

When writing agent prompts that use `cortex`:

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
