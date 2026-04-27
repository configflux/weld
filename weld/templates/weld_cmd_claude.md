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
wd discover --output .weld/graph.json
wd build-index
wd graph stats
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

<!-- weld-managed:start name=retrieval-surfaces -->
| Command | When to use |
|---------|-------------|
| `wd brief <term>` | Default starting point — ranked, classified context packet |
| `wd query <term>` | Broader tokenized search when brief is too narrow |
| `wd context <id>` | Deep dive into a specific node and its neighbors |
| `wd path <from> <to>` | Trace dependency or data-flow paths |
| `wd find <keyword>` | File-level keyword search (uses inverted index) |
<!-- weld-managed:end name=retrieval-surfaces -->

---

## Trust boundary

<!-- weld-managed:start name=trust-boundary -->
Run `wd discover` automatically only on repositories you trust. Project-local
strategies under `.weld/strategies/` are Python modules loaded at discovery
time, and `strategy: external_json` executes configured commands from
`discover.yaml`.
<!-- weld-managed:end name=trust-boundary -->

---

## Manual enrichment

If provider extras or API keys are unavailable, enrich a node manually only
after reading the underlying content:

```bash
wd stale
wd context "<node-id>"
```

Read `props.file` when present, or use the node's real neighboring docs,
config, or source when no file is attached. Preserve the node ID, type, and
label from `wd context`:

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

Manual inferred edges must use explicit provenance such as
`{"source": "manual"}` and only be added after verifying the relationship from
source content. Manual enrichment writes `.weld/graph.json` directly and can be
overwritten by a later `wd discover --output .weld/graph.json`, so validate afterward:

```bash
wd graph validate
wd graph stats
```

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
