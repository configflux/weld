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

The server registers 13 tools. The list is defined in
`weld/_mcp_tools.py::build_tools` and is stable for test pinning. Each tool
has a JSON Schema `inputSchema` describing its parameters; the schemas below
summarise the required fields.

| Tool | Required input | Purpose |
|---|---|---|
| `weld_query` | `term` | Tokenized ranked search over the connected structure; returns matches, neighbors, and edges. |
| `weld_find` | `term` | Substring search over `.weld/file-index.json`; returns ranked file hits with matching tokens and a score. |
| `weld_context` | `node_id` | Node plus its 1-hop neighborhood. |
| `weld_path` | `from_id`, `to_id` | Shortest path between two nodes, with visited nodes and connecting edges. |
| `weld_brief` | `area` | Stable agent-facing brief (`BRIEF_VERSION=2`) for a task area: primary matches, interfaces, docs, build surfaces, boundaries. |
| `weld_stale` | -- | Advisory freshness check vs git HEAD; does not mutate the graph. |
| `weld_callers` | `symbol_id` | Direct (or transitive via `depth`) callers of a symbol by walking `calls` edges in reverse. |
| `weld_references` | `symbol_name` | Callers and file-index references for a bare symbol name. |
| `weld_export` | `format` | Export the graph (or a subgraph centered on `node_id`) to `mermaid`, `dot`, or `d2`. |
| `weld_trace` | `term` or `node_id` | Protocol-aware cross-boundary slice: service / interface / contract / boundary / verification. |
| `weld_impact` | `target` | Reverse-dependency blast radius for a node id or file path. |
| `weld_enrich` | -- | LLM-assisted semantic enrichment for a node or the full graph. See the [trust model](#trust-model) before enabling. |
| `weld_diff` | -- | Diff between previous and current discovery runs: added, removed, modified nodes and edges. |

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
disk. See ADR 0023 for the detail.

Unknown client names exit non-zero with a diagnostic listing the three
supported names.

## Using Weld from an agent

The most useful call patterns mirror the Weld workflow: understand before
editing, trace before changing, check freshness before trusting the graph.

### 1. Brief before editing a new area

Before touching an unfamiliar module, call `weld_brief` to get a stable
envelope of primary matches, interfaces, and boundaries for that area.

> I need to change how the publish audit enforces allowlist compliance.
> Before editing, call `weld_brief` with `area="publish audit"` and
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

### 4. Check freshness before trusting the graph

If the repo has changed since the last discovery run, the graph is stale
and MCP answers can be outdated.

> Before you run any other `weld_*` tool, call `weld_stale`. If it
> reports `stale: true`, stop and ask me to run `wd discover` before
> continuing.

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
  edges, ...}` shapes produced by `weld.graph.Graph`.
- `weld_find` returns `{files: [{path, score, tokens}, ...]}`.
- `weld_brief` returns a versioned envelope (`BRIEF_VERSION=2`).
- `weld_stale` returns `{stale, reasons, ...}`.
- `weld_callers` / `weld_references` return caller lists and, for
  references, a combined `files` list from the file index.
- `weld_export` returns `{format, output}` where `output` is a string in
  the requested graph-visualisation format.
- `weld_trace`, `weld_impact`, `weld_enrich`, `weld_diff` return the same
  envelopes documented for their CLI counterparts.

Unknown tool names raise a dispatch error that the stdio server converts
to `{"error": "unknown weld MCP tool: <name>"}`.

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
The on-disk `.weld/graph.json` is older than the current git HEAD. Run
`wd discover --output .weld/graph.json` in a shell to refresh before
continuing. The server will not auto-refresh.

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
