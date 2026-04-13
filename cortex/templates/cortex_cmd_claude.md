Query the repository knowledge graph for codebase context.

## Input

$ARGUMENTS — search term, node ID, or a maintenance keyword.

---

## Behavior

### If argument is "prime"

Check setup status and print next-step guidance:

```bash
cortex prime
```

### If argument is "discover"

Re-run discovery and refresh the graph:

```bash
cortex discover > .cortex/graph.json
cortex build-index
cortex stats
```

Report the updated node and edge counts.

### If argument looks like a node ID (contains ":")

Run a context query for the full neighborhood:

```bash
cortex context "$ARGUMENTS"
```

### Otherwise (search term)

Start with a brief for structured, ranked context:

```bash
cortex brief "$ARGUMENTS"
```

If the brief returns few results, fall back to a broader search:

```bash
cortex query "$ARGUMENTS"
```

For the top matches, expand context on the most relevant one.

---

## Retrieval surfaces

| Command | When to use |
|---------|-------------|
| `cortex brief <term>` | Default starting point — ranked, classified context packet |
| `cortex query <term>` | Broader tokenized search when brief is too narrow |
| `cortex context <id>` | Deep dive into a specific node and its neighbors |
| `cortex path <from> <to>` | Trace dependency or data-flow paths |
| `cortex find <keyword>` | File-level keyword search (uses inverted index) |

---

## Maintenance

When the graph feels stale or incomplete:

```bash
cortex prime          # status check with actionable guidance
cortex stale          # quick freshness check against git HEAD
```

---

## Examples

- `/cortex Store` — find everything related to Store
- `/cortex entity:Offer` — full context for the Offer entity
- `/cortex stage:extraction` — get extraction stage context
- `/cortex discover` — refresh the graph from the codebase
- `/cortex prime` — check setup status
