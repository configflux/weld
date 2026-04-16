# .weld/ — Connected Structure Working Directory

This directory is managed by the [`weld` toolkit](https://github.com/your-org/weld).
It contains the discovery configuration, built graph, and optional project-local
extensions for the repository's connected structure.

## Files

| File | Purpose | Rebuild command |
|------|---------|-----------------|
| `discover.yaml` | Declarative discovery config — defines which files, strategies, and topology feed the graph | `wd init` (bootstrap) or edit manually |
| `graph.json` | Built connected structure (nodes, edges, metadata) | `wd discover > .weld/graph.json` |
| `file-index.json` | Inverted keyword-to-file index for `wd find` | `wd build-index` |

## Optional directories

| Directory | Purpose |
|-----------|---------|
| `strategies/` | Project-local extraction strategies (override or extend bundled ones) |
| `adapters/` | External adapter scripts for legacy or non-standard sources |

## Maintenance workflow

```bash
# 1. Check what needs attention
wd prime

# 2. Bootstrap or regenerate the discovery config
wd init              # first time — scans the project and writes discover.yaml
# Then edit discover.yaml to tune sources, topology, and entity packages.

# 3. Build the graph and index
wd discover > .weld/graph.json
wd build-index

# 4. Query the graph
wd brief "search term"     # agent-facing context packet
wd query "search term"     # tokenized search
wd context node:id         # node + neighborhood
wd find "keyword"          # file-index keyword search

# 5. Check freshness
wd stale                   # compare graph to current git HEAD
wd prime                   # full status check with next-step guidance
```

## Adding project-local extensions

```bash
wd scaffold local-strategy my_strategy    # write to .weld/strategies/my_strategy.py
wd scaffold external-adapter my_adapter   # write to .weld/adapters/my_adapter.py
```

Then wire the new strategy or adapter into `discover.yaml`.

## Git tracking

Commit `discover.yaml` to version control so the discovery config is shared.
Whether to commit `graph.json` and `file-index.json` is a project decision:
committing them gives agents instant access without a rebuild step; omitting
them keeps the repo lighter and avoids merge noise on large graphs.
