# .cortex/ — Knowledge Graph Working Directory

This directory is managed by the [`cortex` toolkit](https://github.com/your-org/cortex).
It contains the discovery configuration, built graph, and optional project-local
extensions for the repository's knowledge graph.

## Files

| File | Purpose | Rebuild command |
|------|---------|-----------------|
| `discover.yaml` | Declarative discovery config — defines which files, strategies, and topology feed the graph | `cortex init` (bootstrap) or edit manually |
| `graph.json` | Built knowledge graph (nodes, edges, metadata) | `cortex discover > .cortex/graph.json` |
| `file-index.json` | Inverted keyword-to-file index for `cortex find` | `cortex build-index` |

## Optional directories

| Directory | Purpose |
|-----------|---------|
| `strategies/` | Project-local extraction strategies (override or extend bundled ones) |
| `adapters/` | External adapter scripts for legacy or non-standard sources |

## Maintenance workflow

```bash
# 1. Check what needs attention
cortex prime

# 2. Bootstrap or regenerate the discovery config
cortex init              # first time — scans the project and writes discover.yaml
# Then edit discover.yaml to tune sources, topology, and entity packages.

# 3. Build the graph and index
cortex discover > .cortex/graph.json
cortex build-index

# 4. Query the graph
cortex brief "search term"     # agent-facing context packet
cortex query "search term"     # tokenized search
cortex context node:id         # node + neighborhood
cortex find "keyword"          # file-index keyword search

# 5. Check freshness
cortex stale                   # compare graph to current git HEAD
cortex prime                   # full status check with next-step guidance
```

## Adding project-local extensions

```bash
cortex scaffold local-strategy my_strategy    # write to .cortex/strategies/my_strategy.py
cortex scaffold external-adapter my_adapter   # write to .cortex/adapters/my_adapter.py
```

Then wire the new strategy or adapter into `discover.yaml`.

## Git tracking

Commit `discover.yaml` to version control so the discovery config is shared.
Whether to commit `graph.json` and `file-index.json` is a project decision:
committing them gives agents instant access without a rebuild step; omitting
them keeps the repo lighter and avoids merge noise on large graphs.
