# Weld MCP Server

Weld ships a stdio [Model Context Protocol](https://modelcontextprotocol.io)
server that exposes its connected structure as structured tool calls. Agents
(Claude Code, VS Code, Cursor, Codex, any MCP-capable client) can query the
graph, search files, trace impact, and enrich the graph without parsing CLI
output.

This document is the reference for configuring and using that server. For
the underlying discovery workflow, see the root `README.md`.

## Why MCP

The CLI (`wd query`, `wd context`, `wd brief`, ...) is the human interface.
The MCP server is the agent interface: same helpers, same return shapes,
delivered as structured JSON envelopes over stdio. An agent that has Weld
configured can look up the repository structure before editing instead of
rediscovering files each turn.

The server is a thin adapter over `weld.graph`, `weld.brief`, and
`weld.file_index`. Each handler loads a fresh `Graph` and delegates to the
same helper the CLI uses. No application code from the analyzed repository
is executed by the server.

Source of truth: [`weld/mcp_server.py`](../weld/mcp_server.py) (dispatch +
stdio entrypoint) and [`weld/_mcp_tools.py`](../weld/_mcp_tools.py) (tool
descriptors and JSON Schemas).

## Running the server

MCP config generation is available in the default install through
`wd mcp config`. Running the stdio server itself requires the optional
MCP SDK extra:

```bash
uv tool install "configflux-weld[mcp]"
python -m weld.mcp_server --help
```

The server is a regular Python module. In a Weld-aware checkout:

```bash
python -m weld.mcp_server          # current directory as root
python -m weld.mcp_server /path/to/repo
```

It runs over stdio and expects an MCP client on the other end. It does not
open a network socket.

If the `mcp` Python SDK is not installed, the server prints an install hint
and exits with status 2 -- the rest of the `weld` package stays usable
without it.

## Exposed tools

The server registers 14 tools. The list is defined in
`weld/_mcp_tools.py::build_tools` and is stable for test pinning. Each tool
has a JSON Schema `inputSchema` describing its parameters; the schemas below
summarise the required fields.

| Tool | Required input | Purpose |
|---|---|---|
| `weld_query` | `term` | Tokenized ranked search over the connected structure; returns matches, neighbors, and edges. By default `origin=unresolved` sentinel **matches** are dropped (parity with `wd query`); pass `include_speculative: true` to restore them. The neighborhood is also bounded by default: it is dieted (stdlib/unresolved/speculative-external neighbors dropped, fan-out capped) and a byte budget prunes the lowest-priority survivors to fit the tool cap, all reported in `omitted_neighbors` (including `size_capped`). Pass `full_neighborhood: true` to restore the full neighborhood, or `full_size: true` to keep the diet but skip the byte budget. |
| `weld_find` | `term` | Substring search over `.weld/file-index.json`; returns ranked file hits with matching tokens and a score. A single word is a literal substring match; a multi-word phrase is tokenized on whitespace and ranked by how many of the words a file's tokens hit (so `"mcp server"` surfaces `mcp_server.py`). |
| `weld_context` | `node_id` | Node plus its 1-hop neighborhood (bounded by default, same as `weld_query`; pass `full_neighborhood: true` for the full neighborhood or `full_size: true` to skip the byte budget). |
| `weld_path` | `from_id`, `to_id` | Shortest path between two nodes, with visited nodes and connecting edges. |
| `weld_brief` | `area` | Stable agent-facing brief (`BRIEF_VERSION=2`) for a task area: primary matches, interfaces, docs, build surfaces, boundaries. Bounded by default (edges de-dangled to emitted nodes; a byte budget prunes lowest-priority nodes, recorded in `warnings`); pass `full_size: true` for the unbounded brief. |
| `weld_stale` | -- | Advisory freshness check vs git HEAD; does not mutate the graph. |
| `weld_callers` | `symbol_id` | Direct (or transitive via `depth`) callers of a symbol by walking `calls` edges in reverse. |
| `weld_references` | `symbol_name` | Callers and file-index references for a bare symbol name. |
| `weld_export` | `format` | Export the graph (or a subgraph centered on `node_id`) to `mermaid`, `dot`, or `d2`. The `mermaid` output clusters nodes into per-file/module `subgraph` blocks, styles each node type via `classDef`, keeps human-readable labels, and annotates truncation with a visible note node. |
| `weld_trace` | `term` or `node_id` | Protocol-aware cross-boundary slice: service / interface / contract / boundary / verification. |
| `weld_impact` | `target` | Reverse-dependency blast radius for a node id or file path. |
| `weld_enrich` | -- | LLM-assisted semantic enrichment for a node or the full graph. See the [trust model](#trust-model) before enabling. |
| `weld_diff` | -- | Diff between previous and current discovery runs: added, removed, modified nodes and edges. |
| `weld_review` | `op` | Triage speculative edges. `op=list` returns pending edges; `op=show` returns one edge; `op=accept` promotes `speculative` -> `definite`; `op=reject` records a drop for the next discover. Mirrors `wd review`. |

In a polyrepo workspace (root with `.weld/workspaces.yaml`), tools that
operate on the graph run against a `FederatedGraph` that spans child repos.
The responses include a `children_status` field so agents can tell which
child repos are indexed, missing, uninitialized, or corrupt.

## Client configuration

The repo ships a minimal reference at [`.mcp.json`](../.mcp.json). Any
client that reads this format can use it verbatim; it is the source of
truth for the command and args.

### Agents that read `.mcp.json`

This covers Claude Code, Cursor, and most generic MCP-aware editors.

```json
{
  "mcpServers": {
    "weld": {
      "command": "python",
      "args": ["-m", "weld.mcp_server"]
    }
  }
}
```

Drop this in the project root as `.mcp.json` (or merge into an existing
one). The client starts the server on demand.

### Codex

Codex reads `.codex/config.toml`:

```toml
[mcp_servers.weld]
command = "python"
args = ["-m", "weld.mcp_server"]
```

`wd bootstrap codex` writes this file for you.

### VS Code and other clients

Point the MCP integration at `python -m weld.mcp_server`. Any client that
speaks stdio MCP and can launch a process works the same way. If your
client needs a different invocation (e.g. a virtualenv-aware wrapper),
substitute it in place of `python`.

### Generated per-client snippets

`wd mcp config --client={claude,vscode,cursor}` prints the ready-to-paste
JSON for each client. The shape differs per client (Claude Code and Cursor
use `mcpServers`; VS Code uses `servers`); the server invocation
(`python -m weld.mcp_server`) is identical.

```bash
wd mcp config --client=claude   # prints .mcp.json snippet
wd mcp config --client=vscode   # prints .vscode/mcp.json snippet
wd mcp config --client=cursor   # prints .cursor/mcp.json snippet
```

Pass `--write` to update the client-appropriate file in place. By default
the writer refuses to clobber an existing file whose content differs;
either pass `--merge` (preserves sibling servers, e.g. `context7`) or
`--force` (overwrites, with the previous content saved as
`<file>.bak`). `--dry-run` reports what would change without touching the
disk.

Unknown client names exit non-zero with a diagnostic listing the three
supported names.

## Using Weld from an agent

The most useful call patterns mirror the Weld workflow: understand before
editing, trace before changing, check freshness before trusting the graph.

### 1. Brief before editing a new area

Before touching an unfamiliar module, call `weld_brief` to get a stable
envelope of primary matches, interfaces, and boundaries for that area.

> I need to change how workspace bootstrap handles nested repositories.
> Before editing, call `weld_brief` with `area="workspace bootstrap"` and
> summarise the returned interfaces and authoritative docs. Propose a
> plan that respects the boundaries it reports.

### 2. Trace cross-service behaviour before changing it

When a change crosses a service or contract boundary, use `weld_trace` or
`weld_path` to see the slice first, so the agent understands where the
change is safe and where it cascades.

> I want to change the shape of the `graph.json` payload the discovery
> pipeline writes. First call `weld_trace` with `term="graph.json
> discovery"` and walk the returned service / interface / contract slice.
> Identify every reader of `graph.json` before editing the writer.

### 3. Expand context for a specific node before refactoring

Before refactoring a symbol, get its neighbourhood and callers from the
graph so the change plan accounts for every dependent.

> I'm refactoring `_load_strategy` in `weld.discover`. Call
> `weld_context` with
> `node_id="symbol:py:weld.discover:_load_strategy"` to see its
> neighbours, then `weld_callers` with the same `symbol_id` and
> `depth=2` to see transitive callers. List every caller you'll need to
> update.

### 4. Trust freshness -- reads self-heal

MCP reads inherit the same freshness contract as the CLI: a graph-backed
read **auto-refreshes before serving** when the repo has changed since the
last discovery run (incremental discovery, the same path `wd query` uses),
so you do not have to run `wd discover` by hand. Every graph-backed read
result also carries a small `freshness` object so you can see the state at a
glance:

```json
{ "...": "...", "freshness": { "stale": false, "commits_behind": 0 } }
```

- `stale` -- `true` when source files have drifted since the recorded
  discovery point. After an auto-refresh this is normally `false`; it stays
  `true` only when refresh is disabled (see below) or could not run.
- `commits_behind` -- how many commits the recorded graph SHA trails HEAD
  (`0` when current, `-1` when no SHA was recorded yet).

Disable auto-refresh by launching the server with `WELD_AUTO_REFRESH=0` in
its environment (for CI or read-only mirrors). With refresh off, reads still
serve and still carry `freshness`, so a `stale: true` field is your signal
that the answer may lag the working tree -- the server will not silently
rewrite the graph.

`weld_stale` remains the detailed, on-demand freshness probe; the per-read
`freshness` object is the cheap inline signal.

### 5. Estimate blast radius before risky edits

Use `weld_impact` to understand reverse-dependency risk before a change.

> I plan to delete the `weld.legacy_export` module. First call
> `weld_impact` with
> `target="weld/legacy_export.py"`, report the direct and transitive
> dependents, and quantify the risk before I approve the deletion.

## Result shapes

All tools return a JSON object. The MCP layer wraps the object in a
`TextContent` block whose `text` is the JSON string. Agents should parse
the text as JSON.

Shapes are tool-specific and follow the same envelopes the CLI emits:

- `weld_query`, `weld_context`, `weld_path` return `{matches, neighbors,
  edges, ...}` shapes produced by `weld.graph.Graph`. `weld_query` also drops
  `origin=unresolved` sentinel *matches* by default so its `matches` equal `wd
  query`'s (the shared `weld.read.read_query` command);
  `include_speculative: true` restores them. `weld_query` and
  `weld_context` additionally **bound the read envelope by default** (the
  product read command, `weld.read`, shared byte-for-byte with the CLI):
  neighbors whose `props.origin` is `stdlib` or `unresolved`, plus speculative
  external *symbols*, are dropped (external *package* nodes are kept), edges
  that would then dangle are removed, and the surviving fan-out is capped at 50
  with a deterministic sort; then a **byte budget** prunes the lowest-priority
  survivors (same total order) until the serialized envelope fits the agent tool
  cap. Omissions are never silent -- the envelope carries `neighbors_filtered:
  true` and `omitted_neighbors: {stdlib, unresolved, external_symbol,
  fanout_capped, size_capped}` counts. Pass `full_neighborhood: true` for the
  full, raw neighborhood (no annotation), or `full_size: true` to keep the diet
  but skip the byte budget.
- `weld_find` returns `{files: [{path, score, tokens}, ...]}`.
- `weld_brief` returns a versioned envelope (`BRIEF_VERSION=2`), bounded by the
  same read command: its edges are de-dangled to the nodes it emits and the byte
  budget applies (a `warnings` entry records any node dropped for size). Pass
  `full_size: true` for the unbounded brief.
- `weld_stale` returns `{stale, reasons, ...}`.
- `weld_callers` / `weld_references` return caller lists and, for
  references, a combined `files` list from the file index.
- `weld_export` returns `{format, output}` where `output` is a string in
  the requested graph-visualisation format. The `mermaid` serializer
  clusters nodes into `subgraph` blocks by file/module, applies per-type
  `classDef` styling, and past a node cap truncates deterministically with
  a visible note node (never a silent partial diagram).
- `weld_trace`, `weld_impact`, `weld_enrich`, `weld_diff` return the same
  envelopes documented for their CLI counterparts.

Graph-backed **read** tools (`weld_query`, `weld_context`, `weld_path`,
`weld_brief`, `weld_callers`, `weld_references`, `weld_trace`, `weld_impact`)
additionally carry a top-level `freshness` object,
`{stale: bool, commits_behind: int}` (see "Trust freshness" above). It is
additive -- the rest of each envelope is byte-identical to the CLI helper.
`weld_find` (file index, not the graph) and `weld_stale` (already the
freshness surface) are not stamped, and a structured error payload
(`error_code` present) never carries `freshness`.

Unknown tool names raise a dispatch error that the stdio server converts
to `{"error": "unknown weld MCP tool: <name>"}`.

### Structured error codes

Graph-load failures and node-lookup misses return a structured error
payload instead of crashing the transport (or silently returning an empty
result), carrying a machine-readable `error_code` plus a stable,
copy-pasteable `hint`. The same vocabulary is emitted by the CLI (as a
single `error[<code>]: <summary> | hint: <hint>` line on stderr with a
nonzero exit), so an agent can branch on the code regardless of surface:

| `error_code`     | Cause                                              | Hint (remediation)                                              |
| ---------------- | -------------------------------------------------- | --------------------------------------------------------------- |
| `graph_missing`  | `.weld/graph.json` is absent (first run)           | `Run: wd init (if no config), then wd discover.`                |
| `graph_corrupt`  | `graph.json` exists but is not valid JSON          | `graph.json is not valid JSON. Rebuild it: wd discover.`        |
| `schema_mismatch` | `meta.schema_version` is newer than this build    | Upgrade weld, or rebuild with this version: `wd discover`.      |
| `node_not_found` | A requested node id resolves to nothing            | `Check the node id (wd query <term> to find it).`               |

The MCP payload shape is `{"error", "error_code", "hint"}` (the
missing-graph case also carries a `retry` field). `node_not_found` is
stamped on `weld_context` and `weld_callers` results when the requested id
resolves to nothing; the only value echoed in `error` is the
caller-supplied id. `weld_path` reports a miss as `{"path": null,
"reason": ...}` and is intentionally **not** stamped (matching the CLI
`path` command), so an agent distinguishes "no such node" from "no route
between two real nodes". For `graph_corrupt`, the `error` summary localizes
the parse failure by **position only** (line / column / byte offset) and
never echoes the raw file bytes, so a secret living in a half-written graph
cannot leak into a tool result or terminal output.

## Trust model

Running the MCP server is safe to do against any repository. The server
itself does **not** execute discovered application code and does not open
network connections just to answer tool calls.

The read/write boundary to be aware of is **discovery**, not the MCP server:

- **Strategy plugins**: Project-local strategies under
  `.weld/strategies/` are Python modules imported at discovery time. Only
  run `wd discover` (and therefore only point an MCP client at) a
  repository whose `.weld/strategies/` you trust.
- **External adapters**: `strategy: external_json` entries in
  `.weld/discover.yaml` execute the configured command with the
  repository root as the working directory. Treat enabling an external
  adapter as the same trust decision as running that command directly.
- **Enrichment providers**: `weld_enrich` can call a configured LLM
  provider. It transmits graph metadata (node ids, descriptions,
  relationships) to that provider. Do not enable enrichment for
  repositories whose structure you cannot share with the provider you
  configured.

Clients decide when to call which tool. Most clients surface the tool
call before executing it; review the call and its arguments the same way
you review a shell command.

See [`SECURITY.md`](../SECURITY.md) for the repository security policy
and how to report a vulnerability.

## Troubleshooting

**`ImportError: No module named 'mcp'`**
The stdio entrypoint requires the optional `mcp` SDK. Install the Weld extra
in the same environment your client launches
(`pip install 'configflux-weld[mcp]'`), or point `command` at a Python
interpreter where it is already installed.

**Client reports zero tools**
Verify that `python -m weld.mcp_server` runs from the command line in the
same working directory the client uses. If the client launches from your
home directory but the Weld repo lives elsewhere, either pass the repo
path as an argument (`python -m weld.mcp_server /path/to/repo`) or set
the client's working directory to the repo root.

**`weld_stale` reports stale**
The on-disk `.weld/graph.json` trails the working tree. By default the next
graph-backed read auto-refreshes it for you (and reports `freshness.stale`
inline), so no manual step is needed. If the server was launched with
`WELD_AUTO_REFRESH=0`, refresh is disabled: run `wd discover` in a shell to
rebuild, or restart the server without the opt-out.

**`weld_query` or `weld_context` returns empty or surprising results**
The connected structure may be stale, or the search term may not be
tokenized the way you expect. Call `weld_stale` first. If the graph is
fresh and the search still misses, fall back to `weld_find` (substring
against the file index) to locate a seed, then call `weld_context` on
the resulting node id.

**Federated workspace missing a child**
In a polyrepo root, the response includes a `children_status` field that
reports which children are `present`, `missing`, `uninitialized`, or
`corrupt`. Run `wd workspace status` for the matching CLI view and
reinitialise missing children with `wd init` inside the child repo.

**Client cannot find `python`**
Some editors start MCP servers from a restricted PATH. Replace
`"command": "python"` with an absolute path to the interpreter (for
example, `"command": "/usr/bin/python3"` or the path to a virtualenv
Python) so the client launches it deterministically.

## See also

- [`README.md`](../README.md) -- full Weld user guide, CLI reference,
  agent integration overview
- [`SECURITY.md`](../SECURITY.md) -- security policy and reporting
- [`.mcp.json`](../.mcp.json) -- reference client configuration
- [`weld/mcp_server.py`](../weld/mcp_server.py) -- stdio entrypoint and
  dispatch
- [`weld/_mcp_tools.py`](../weld/_mcp_tools.py) -- tool descriptors and
  JSON Schemas
