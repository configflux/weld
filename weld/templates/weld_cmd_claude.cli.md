Query the repository connected structure for codebase context.

## Input

$ARGUMENTS — search term, node ID, or a maintenance keyword.

---

## Behavior

### If argument is "prime"

Check setup status and print next-step guidance:

```bash
wd prime
```

### If argument is "discover"

Re-run discovery and refresh the graph:

```bash
wd discover > .weld/graph.json
wd build-index
wd stats
```

Report the updated node and edge counts.

### If argument looks like a node ID (contains ":")

Run a context query for the full neighborhood:

```bash
wd context "$ARGUMENTS"
```

### Otherwise (search term)

Start with a brief for structured, ranked context:

```bash
wd brief "$ARGUMENTS"
```

If the brief returns few results, fall back to a broader search:

```bash
wd query "$ARGUMENTS"
```

For the top matches, expand context on the most relevant one.

---

## Retrieval surfaces

| Command | When to use |
|---------|-------------|
| `wd brief <term>` | Default starting point — ranked, classified context packet |
| `wd query <term>` | Broader tokenized search when brief is too narrow |
| `wd context <id>` | Deep dive into a specific node and its neighbors |
| `wd path <from> <to>` | Trace dependency or data-flow paths |
| `wd find <keyword>` | File-level keyword search (uses inverted index) |

---

## Trust boundary

Run `wd discover` automatically only on repositories you trust. Project-local
strategies under `.weld/strategies/` are Python modules loaded at discovery
time, and `strategy: external_json` executes configured commands from
`discover.yaml`.

---

## Maintenance

When the graph feels stale or incomplete:

```bash
wd prime          # status check with actionable guidance
wd stale          # quick freshness check against git HEAD
```

---

## Examples

- `/weld Store` — find everything related to Store
- `/weld entity:Offer` — full context for the Offer entity
- `/weld stage:extraction` — get extraction stage context
- `/wd discover` — refresh the graph from the codebase
- `/wd prime` — check setup status
