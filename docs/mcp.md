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
same helper the CLI uses. The repository being served is not on the server's
import path, so it cannot supply modules to the process reading it -- see
[trust model](#trust-model) for the exact boundary.

Source of truth: [`weld/mcp_server.py`](../weld/mcp_server.py) (dispatch +
stdio entrypoint) and [`weld/_mcp_tools.py`](../weld/_mcp_tools.py) (tool
descriptors and JSON Schemas).

## Running the server

MCP config generation is available in the default install through
`wd mcp config`. Running the stdio server itself requires the optional
MCP SDK extra, which pins `mcp>=2,<3` -- the server targets the MCP SDK
2.x handler API and cannot run on a pre-2.0 SDK:

```bash
uv tool install "configflux-weld[mcp]"
wd mcp serve --help
```

`wd mcp serve` is the launch form to point clients at:

```bash
wd mcp serve                       # resolved from the current directory
wd mcp serve /path/to/repo
```

Without an argument the root is resolved the way `wd` resolves it: the
nearest enclosing directory holding a `.weld/`, bounded by the current git
worktree, else that worktree's root. Starting the server from a
subdirectory therefore serves the checkout you are in, not the one
directory you happened to start in.

It runs over stdio and expects an MCP client on the other end. It does not
open a network socket.

`wd` being a console script matters twice over. A script's `sys.path[0]` is
the script's own directory, so the directory a client launches the server in
-- the repository being served -- is never on the module search path at all.
And the interpreter is fixed in the script's shebang, so the server always
runs in the environment weld was installed into, rather than whatever
`python` happens to resolve to in the client's environment.

The same server is also reachable as a module, `python -m weld.mcp_server
[ROOT]`. That form is supported and tested, and it is the right one when you
are running from a source checkout, because it serves the checkout rather
than an installed copy. It is not what clients should be pointed at: `-m`
puts the launch directory on the module search path before any weld code can
run. See [trust model](#the-repository-is-not-on-the-servers-import-path)
for exactly what that costs and what is done about it.

In the `initialize` reply the server identifies itself as `weld` with the
installed `configflux-weld` version, so clients that log or display server
identity show the same version as `wd --version`. Running from a checkout
with neither distribution metadata nor a `VERSION` file reports `0.0.0`.

If the `mcp` Python SDK is unusable, the server prints a hint and exits
with status 2 -- the rest of the `weld` package stays usable without it.
The hint names the remedy for the case at hand: install the extra when no
`mcp` SDK is present, or `pip install -U "mcp>=2"` when one is installed
but does not provide the 2.x API.

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
| `weld_stale` | -- | Advisory freshness check vs git HEAD. Unlike the graph-backed reads it does **not** auto-refresh first: it reports the state it finds and never mutates the graph. |
| `weld_callers` | `symbol_id` | Direct (or transitive via `depth`) callers of a symbol by walking `calls` edges in reverse. `symbol_id` may be a bare name resolving to several same-named seeds; every response carries a top-level `seeds` list naming the resolved id(s), and at `depth: 1` each caller also carries a `targets` list naming which seed(s) it was found calling directly (not populated beyond depth 1, where a caller can be reachable via more than one seed's chain and attributing it would not be a recorded fact). Bounded by the same byte budget as `weld_query`; drops are counted in a `size_capped` object, a top-level `budget_exceeded` flags a payload still over budget after pruning everything droppable, and `full_size: true` returns every caller. |
| `weld_references` | `symbol_name` | What points at a thing, plus file-index references. Takes a bare symbol name or a full node id. A symbol reports its callers; any other node type (build target, tool, doc) reports every node with an edge into it, since nothing *calls* those. A bare name can resolve to several `matches`; each entry in `callers` carries a `targets` list naming which match id(s) it was actually found under, so two same-named matches with different callers stay distinguishable instead of merging into one undifferentiated list. An unknown id returns an `error` rather than an empty result. Bounded by the byte budget, dropping file hits before resolved callers and resolved `matches` last; counts in `size_capped`, `full_size: true` for the unbounded union. A top-level `budget_exceeded` flags a payload still over budget after pruning everything droppable. |
| `weld_export` | `format` | Export the graph (or a subgraph centered on `node_id`) to `mermaid`, `dot`, or `d2`. The `mermaid` output clusters nodes into per-file/module `subgraph` blocks, styles each node type via `classDef`, keeps human-readable labels, and annotates truncation with a visible note node. |
| `weld_trace` | `term` or `node_id` | Protocol-aware cross-boundary slice: service / interface / contract / boundary / verification. Bounded by the byte budget (a `warnings` entry records any node dropped for size); `full_size: true` returns the whole slice. |
| `weld_impact` | `target` | Reverse-dependency blast radius for a node id or file path. `affected_surfaces` buckets the radius into `cli_commands`, `mcp_tools`, `repo_tools`, `api_endpoints`, `entrypoints`, `boundaries` and `tests`; only the published surfaces set `risk_level`, so `repo_tools` (the repo's own scripts) is reported without raising it. Bounded by the byte budget: dependents and `affected_surfaces` members are pruned farthest-hop-first in one pass and counted in `warnings.size_capped`, while `risk_level` and `affected_surface_counts` always describe the **full** radius -- so a bounded payload never reports a smaller radius or a lower risk than the change carries. `warnings.budget_exceeded` (always present) is `true` when everything droppable was dropped and the payload is still over budget. `full_size: true` returns every dependent. On a `repo:<name>` target the payload carries `measured_by` -- the cross-repo resolvers whose recorded pass measured the result -- when discovery ran any; without it the answer is `risk_level: UNKNOWN` plus a `cannot_answer` reason rather than a fabricated zero. |
| `weld_enrich` | -- | LLM-assisted semantic enrichment for a node or the full graph. See the [trust model](#trust-model) before enabling. Pass `agent_direct: true` to get the work plan for enriching it yourself instead of a provider call -- no credentials, no network, no write; see [Enriching without a provider](#enriching-without-a-provider). |
| `weld_diff` | -- | Diff between previous and current discovery runs: added, removed, modified nodes and edges. |
| `weld_review` | `op` | Triage speculative edges. `op=list` returns pending edges; `op=show` returns one edge; `op=accept` promotes `speculative` -> `definite`; `op=reject` records a drop for the next discover. Mirrors `wd review`. |

In a polyrepo workspace (root with `.weld/workspaces.yaml`), tools that
operate on the graph run against a `FederatedGraph` that spans child repos.
The responses include a `children_status` field so agents can tell which
child repos are indexed, missing, uninitialized, or corrupt. Entries are
ordered non-present states first (`missing` / `uninitialized` / `corrupt`),
then `present`, alphabetical by child name within each class -- so a
problem child is never pushed out of a byte-capped response by a run of
ordinary present ones (see "Bounded reads" below).

### Answering from another checkout

A server is launched once, but an agent often works in a git worktree
created after that. Every read tool in the table above -- that is, all of
them except `weld_enrich` and `weld_review` -- therefore accepts an optional
`root`, the same capability `wd --root` gives on the command line:

```json
{"name": "weld_query", "arguments": {"term": "store", "root": "/repo/feature-branch"}}
```

The rules are deliberately narrow:

- **Same repository only.** `root` must be an existing directory belonging
  to the same repository as the root the server was started against -- a
  linked worktree, the main checkout, or a subdirectory of either.
  Repository identity is git's, so a separate clone is out of bounds even
  when its contents are identical. Anything else is refused with
  `root_out_of_bounds` and the call answers nothing.
- **Reads only.** `weld_enrich` and `weld_review` write, so they take no
  `root` and always act on the server's own root. The server refuses the
  argument for those two itself rather than trusting the client to validate
  the schema, so a write can never be steered at another checkout.
- **Omitting it keeps today's behaviour**: the server's own root answers --
  which may not be the checkout you are editing (see "Trust freshness"
  below for how to tell).
- **Self-describing.** The `root` parameter's own schema description points
  a client at `freshness.branch` before it ever calls the tool, so the "which
  checkout answered" signal is discoverable from the schema alone, not only
  from this doc.
- **Per-request, not per-session.** Nothing is remembered between calls,
  and each root keeps its own cached graph, so alternating between
  checkouts is safe.
- A checkout that has no graph yet is bootstrapped on first use, exactly as
  the CLI bootstraps it, so a freshly created worktree answers without a
  separate setup step. Child repos of a polyrepo workspace are not
  addressable this way -- point the server at the workspace root instead.

## Client configuration

`wd mcp config --client=<name>` generates the snippet for you; the shapes
below are what it emits. (The repo's own [`.mcp.json`](../.mcp.json) is
deliberately *not* this shape -- weld's own checkout serves itself via
`python -m weld.mcp_server`, for the source-checkout reason given above.)

### Agents that read `.mcp.json`

This covers Claude Code, Cursor, and most generic MCP-aware editors.

```json
{
  "mcpServers": {
    "weld": {
      "command": "wd",
      "args": ["mcp", "serve"]
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
command = "wd"
args = ["mcp", "serve"]
```

`wd bootstrap codex` writes this file for you.

### VS Code and other clients

Point the MCP integration at `wd mcp serve`. Any client that speaks stdio
MCP and can launch a process works the same way. If `wd` is not on the
`PATH` your client searches, give the absolute path to the console script
(`/path/to/venv/bin/wd`) rather than substituting a bare `python -m` --
an absolute script path keeps both properties the console script is chosen
for.

### Generated per-client snippets

`wd mcp config --client={claude,vscode,cursor}` prints the ready-to-paste
JSON for each client. The shape differs per client (Claude Code and Cursor
use `mcpServers`; VS Code uses `servers`); the server invocation
(`wd mcp serve`) is identical.

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
{ "...": "...", "freshness": { "stale": false, "commits_behind": 0, "branch": "main" } }
```

- `stale` -- `true` when source files have drifted since the recorded
  discovery point. After an auto-refresh this is normally `false`; it stays
  `true` only when refresh is disabled (see below) or could not run.
- `commits_behind` -- how many commits the recorded graph SHA trails HEAD
  (`0` when current, `-1` when no SHA was recorded yet).
- `branch` -- the branch checked out at the served root, read live at
  response time. `null` outside a git checkout and on a detached `HEAD`.
  Use it to confirm an answer came from the checkout you meant: if you are
  working in a worktree on `feature-x` and reads report `main`, the server
  is answering from a different root. `wd stale` reports the live branch and
  the branch the graph was discovered on side by side.

Disable auto-refresh by launching the server with `WELD_AUTO_REFRESH=0` in
its environment (for CI or read-only mirrors). With refresh off, reads still
serve and still carry `freshness`, so a `stale: true` field is your signal
that the answer may lag the working tree -- the server will not silently
rewrite the graph.

`weld_stale` remains the detailed, on-demand freshness probe; the per-read
`freshness` object is the cheap inline signal.

**`weld_stale` itself never refreshes.** It is the one graph tool that
measures rather than heals: it reports the state it finds, never rewrites the
graph, and returns exactly what `wd stale --json` prints at the same root. A probe
that refreshed before answering could only ever report `stale: false`, and at
a polyrepo root it would re-discover every drifted child before telling you
one had drifted. So `stale: true` from `weld_stale` is a real observation,
not a prediction: the next graph-backed read will still self-heal, and that
read is what makes the graph fresh again.

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
- `weld_impact`, `weld_callers`, `weld_references` and `weld_trace` are bounded
  by that same budget, shared with `wd impact --json` / `wd callers --json` /
  `wd references --json` / `wd trace`. These are the reads that grow without
  limit on a well-connected node — a hub module's blast radius or a hot symbol's
  caller list — so each prunes its lowest-priority items until the payload fits
  the agent tool cap, drops the edges that would then dangle, and **reports the
  count**: `warnings.size_capped` for `weld_impact` (plus a `warnings.messages`
  entry), a top-level `size_capped` object for `weld_callers` /
  `weld_references`, and a `warnings` entry for `weld_trace`. The report field
  is always present, so a client never has to probe for it. Retention order is
  per surface: `weld_impact` keeps nearer hops, `weld_references` keeps resolved
  `matches` over textual file hits, the rest use the shared node-quality order.
  `full_size: true` skips the budget on any of them.

  Two fields are deliberately **not** pruned: `weld_impact`'s
  `affected_surfaces` and `risk_level` are computed over the full blast radius
  and are byte-identical whether or not the budget fired. A bounded payload must
  never come back reporting a smaller radius, or a lower risk, than the change
  actually carries.
- `weld_stale` returns `{stale, source_stale, sha_behind, coverage_stale,
  graph_sha, current_sha, commits_behind, stale_sources, stale_sources_omitted,
  branch, graph_branch, ...}` -- the same payload `wd stale --json` prints
  (plus a `reason` string when staleness cannot be computed, e.g. a non-git
  root). `coverage_stale` is true when a
  file discovery would resolve at this commit is missing from the graph's own
  inventory; unlike the other two signals it does not need HEAD to have moved,
  so it is what catches a graph that never ingested a shipped module. It is
  folded into `source_stale` (and therefore `stale`), and reported separately
  so an empty result can be told apart from a genuine absence. When
  `source_stale` is true, `stale_sources` is a `[{path, reason}]` list naming
  which tracked source(s) tripped it -- `reason` is one of `changed since last
  discovery`, `content differs`, `ingested file vanished`, or `in-scope file
  never ingested` -- capped at 50 entries with `stale_sources_omitted` giving
  the elided count (`0` when nothing was capped). Some stale states (no
  recorded `git_sha`, unreachable history) have no single file to name and
  leave `stale_sources` empty; those are exactly the states `graph_sha` /
  `commits_behind` already distinguish. `branch` is the branch live at the answering
  root and `graph_branch` the one checked out when the graph was discovered;
  when they differ, the answer came from a graph built on another checkout.
  Both are `null` on a detached HEAD, a non-git root, or a graph with no
  recorded branch. One optional key, `seed_blocked_reason`, appears only
  alongside `reason: no graph` in a linked worktree of a repository that does
  not track `.weld/discover.yaml`: seeding can never fire there, so `wd
  discover` is not the remedy and the string names the repository-wide
  `git add -f` that is. It is absent everywhere else -- including a plain
  clone, the main checkout, and a polyrepo root -- so no other payload
  changes. In a polyrepo workspace the payload adds `children` -- a
  name-sorted list of `{name, state, reason, commits_behind}` covering every
  registered child -- plus `root_source_stale` and `root_sha_behind`, which
  carry the root's own two signals unmixed. Top-level `stale` is
  `root_stale OR any(child.stale)`, so gating on it at a workspace root
  catches a child whose graph has drifted even when the root's own graph is
  current. A child that is missing, uninitialized, or corrupt reports that
  word in `state` and `reason` and is never counted stale -- absent is not
  the same as behind.
- `weld_callers` / `weld_references` return caller lists and, for
  references, a combined `files` list from the file index.
- `weld_export` returns `{format, output}` where `output` is a string in
  the requested graph-visualisation format. The `mermaid` serializer
  clusters nodes into `subgraph` blocks by file/module, applies per-type
  `classDef` styling, and past a node cap truncates deterministically with
  a visible note node (never a silent partial diagram).
- `weld_trace`, `weld_impact`, `weld_enrich`, `weld_diff` return the same
  envelopes documented for their CLI counterparts.

### Enriching without a provider

Enrichment normally calls a configured LLM provider, which needs an optional
extra installed and credentials available to the server. `weld_enrich` with
`agent_direct: true` needs neither: instead of calling a provider it returns
the **work plan** for doing the enrichment yourself, on the premise that the
agent holding the tool is already a language model.

```json
{"name": "weld_enrich", "arguments": {"agent_direct": true, "limit": 25}}
```

The payload is the same one `wd enrich --agent-direct --json` prints, from the
same builder, so the two surfaces cannot drift:

- `pending` — the nodes still lacking a valid enrichment record, each with its
  `id`, `type`, `label`, and source `file` (`null` when the node has none, in
  which case read its neighborhood instead).
- `counts` — `scope_total`, `pending_total`, `returned`, `remaining`. `limit`
  caps the list but never the accounting, so a batched caller can always tell
  progress from completion.
- `record_contract` — the required and optional fields of a `props.enrichment`
  record, the recommended provenance values, and what happens to a record that
  omits a required field.
- `command_template` — the `wd add-node ... --merge --props '{...}'` call that
  lands one record.
- `verification` and `notes` — how to check the result, and how concurrent
  writers serialize.

The mode is read-only: no provider is resolved, no network call is made, and
the graph is not written. It is therefore safe under any trust posture, and it
is the answer when the [trust model](#trust-model) reasons below are why no
provider is configured in the first place.

Two parameters shape the plan and require the mode: `node_type` (list only
nodes of one type) and `limit`. The provider-side parameters — `provider`,
`model`, `max_tokens`, `max_cost` — mean the opposite and are refused when
combined with it, rather than ignored; a dropped `provider` would otherwise
read as an unattended provider run that never happened. `node_id` and `force`
work in both modes. Both surfaces consult one rule for this, so the refusal
message names the equivalent `wd enrich` flag (`--type`, `--agent-direct`)
rather than the MCP parameter.

Because the ids, labels, and paths in the plan are read out of the scanned
repository, treat them as data, not instructions — the payload says so itself,
in its `preamble`.

Graph-backed **read** tools (`weld_query`, `weld_context`, `weld_path`,
`weld_brief`, `weld_callers`, `weld_references`, `weld_trace`, `weld_impact`)
additionally carry a top-level `freshness` object,
`{stale: bool, commits_behind: int, branch: string|null}` (see "Trust
freshness" above). It is additive -- the rest of each envelope is
byte-identical to the CLI helper.
`weld_find` (file index, not the graph) and `weld_stale` (already the
freshness surface) are not stamped, and a structured error payload
(`error_code` present) never carries `freshness`.

The byte budget (see "Bounded reads" above) accounts for this: "fits the
budget" means the bytes this tool call actually returns, `freshness` and
`children_status` included, not an intermediate value before those stamps
land. The bounded surfaces reserve a fixed slice of the budget for exactly
this, so a payload that reports itself as fitting always is. At a federated
root `children_status` (below) gets the same treatment as any other bounded
list: past a workspace's registered-child count where it can no longer fit
its reserve, the map is capped to the names that do and a sibling
`children_status_omitted` count reports how many were left out -- present
and `0` whenever `children_status` is, so a client never has to probe for it.
The map is ordered non-present states first so the cap sheds ordinary
`present` entries before an actionable `missing` / `uninitialized` /
`corrupt` one -- a workspace with more registered children than fit in the
reserve still surfaces its problem children first.

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
| `graph_corrupt`  | `graph.json` exists but is not valid JSON, or the path exists but is not a regular file (e.g. a directory) | `graph.json is not valid JSON. Rebuild it: wd discover.`        |
| `schema_mismatch` | `meta.schema_version` is newer than this build    | Upgrade weld, or rebuild with this version: `wd discover`.      |
| `node_not_found` | A requested node id resolves to nothing            | `Check the node id (wd query <term> to find it).`               |
| `root_out_of_bounds` | A request named a `root` outside the served repository | Name a checkout of the same repository, or omit `root`.     |
| `file_index_missing` | `.weld/file-index.json` is absent, so `find` has nothing to search (at a polyrepo root: neither it nor any child has one) | `Run: wd discover.` |

The MCP payload shape is `{"error", "error_code", "hint"}` (the
missing-graph and missing-file-index cases also carry a `retry` field). `node_not_found` is
stamped on `weld_context` and `weld_callers` results when the requested id
resolves to nothing; the only value echoed in `error` is the
caller-supplied id. `weld_path` reports a miss as `{"path": null,
"reason": ...}` and is intentionally **not** stamped (matching the CLI
`path` command), so an agent distinguishes "no such node" from "no route
between two real nodes". For `graph_corrupt`, the `error` summary localizes
a JSON parse failure by **position only** (line / column / byte offset) and
never echoes the raw file bytes, so a secret living in a half-written graph
cannot leak into a tool result or terminal output; the not-a-regular-file
case (e.g. a directory at the graph path) carries a fixed summary with no
file content or path at all. `root_out_of_bounds` is
the same shape of promise: its message is a constant that never repeats the
path that was asked for, and it reads identically whether the path was in
another repository, was a file, or did not exist -- so a refused call
answers nothing about the server's filesystem.

`weld_find` reads the file index rather than the graph, so a missing graph is
not a cannot-answer condition for it and it is exempt from the `graph_missing`
guard. It is not exempt from having a precondition: a root where the search
has no index to read at all — its own `.weld/file-index.json`, and at a
polyrepo root every registered child's — returns `file_index_missing` rather
than an empty `files` list. An index that exists and matches nothing is a real
negative answer and still comes back as `{"query", "files": []}` with no
`error_code`, and `weld_find` never builds an index: the remedy is `wd
discover`, run deliberately. At a polyrepo root the index that answers is
each child's, so the remedy there is discovery where the children are — a
root whose children are not checked out has nothing to search and says so.

`graph_missing` always leads its `error` with `No Weld graph found.`, and
when the answering checkout is one that can *never* build a graph on its
own, a second line names why. The case that has one is a linked git
worktree of a repository that gitignores `.weld/discover.yaml`: seeding
reads the worktree's own config, and git only puts that file in a new
checkout when the repository tracks it, so every worktree of such a
repository arrives unable to seed and no retry changes that. The remedy
(`git add -f .weld/discover.yaml`) is a repository-wide decision, which is
why the payload names it rather than leaving an agent to infer it -- the
same line `wd` prints, so both surfaces say the same thing. `error_code`,
`hint`, and `retry` are unchanged in every case; only `error` gains the
line, and its text is a fixed constant that never echoes the path.
`file_index_missing` behaves identically, and for the same reason: the seed
that would have supplied the index is the one that would have supplied the
graph, so the prerequisite is the same and the sentence is the same one.

## Trust model

Running the MCP server is safe to do against any repository. The server
itself does **not** execute discovered application code and does not open
network connections just to answer tool calls.

### The repository is not on the server's import path

The concern is that a repository could answer imports the server makes. A
file named `json.py` or a directory named `mcp/` next to the code being
analyzed would be imported -- and importing a module executes it -- during
startup, if the directory the server was launched in were on Python's module
search path. For an MCP client that directory is the project directory.

**`wd mcp serve` never puts it there.** A console script's `sys.path[0]` is
the script's own directory, so there is no launch-directory entry to remove
and nothing to get right in what order. Launched from a directory carrying a
shadow for every module the server touches, it executes none of them.

**`python -m weld.mcp_server` has a floor it cannot get under**, because
`-m` places the launch directory at the front of the search path, ahead of
the standard library. The server removes that entry before it imports
anything of its own -- the removal is the very first thing the entry point
does, ahead of even the `from __future__` line a Python module would
normally carry at its top -- so no *weld* import can be answered by the
repository. But the removal cannot precede the interpreter. Python's own
module runner imports a handful of modules before any weld code runs, and
those are still resolved against the launch directory. On CPython 3.12 that
is eight -- `collections`, `functools`, `keyword`, `operator`, `reprlib`,
`threading`, `types`, `warnings` -- and the set varies by interpreter
version, so treat the count as a floor rather than a fixed list. The floor is
true of `python -m` generally, not of weld specifically: every `-m` target
pays it, weld's or the standard library's.

This is why `wd mcp serve` is the form clients are pointed at, and why the
`-m` form is documented as the way to serve a *source checkout* -- a tree you
are developing in and therefore already trust.

Two further details are worth knowing:

- A `PYTHONPATH` that names the repository is left in place. That entry is
  your own declaration about your own environment, so weld does not edit it.
  This applies to both launch forms: a console script has no launch-directory
  entry, but it still honours `PYTHONPATH`.
- If you must use the `-m` form against a repository you do not trust at all,
  launch it with `PYTHONSAFEPATH=1` in its environment (Python 3.11+), which
  removes the entry at interpreter startup and closes the floor too:

  ```json
  {
    "mcpServers": {
      "weld": {
        "command": "python",
        "args": ["-m", "weld.mcp_server"],
        "env": {"PYTHONSAFEPATH": "1"}
      }
    }
  }
  ```

  With `PYTHONSAFEPATH=1` the interpreter will not find `weld` in the launch
  directory either, so use it with an installed weld rather than a source
  checkout you are running from in place -- at which point `wd mcp serve`
  gets you the same result with nothing to configure.

### Discovery is the boundary that does run code

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
  configured. `agent_direct: true` is the mode that resolves no provider
  and opens no socket ([details](#enriching-without-a-provider)), so it
  stays available when this is the reason you configured none.

Clients decide when to call which tool. Most clients surface the tool
call before executing it; review the call and its arguments the same way
you review a shell command.

See [`SECURITY.md`](../SECURITY.md) for the repository security policy
and how to report a vulnerability.

## Troubleshooting

**`the 'mcp' Python SDK is not installed`** (`ImportError: No module named 'mcp'`)
The stdio entrypoint requires the optional `mcp` SDK. Install the Weld extra
in the same environment your client launches
(`pip install 'configflux-weld[mcp]'`), or point `command` at a Python
interpreter where it is already installed.

**`does not provide the MCP SDK 2.x API`**
The SDK is installed but unusable. Nearly always it predates 2.0: Weld
targets the MCP SDK 2.x handler API, so the extra requires `mcp>=2,<3`.
Upgrade in place:

```bash
pip install -U "mcp>=2"          # or: pip install -U "configflux-weld[mcp]"
```

Upgrade the same environment your client launches the server from -- a
client configured with an absolute interpreter path, or with its own
virtualenv, will keep loading the old SDK no matter what your shell
reports. The message includes the version it found, so compare that against
`pip show mcp` in the environment you upgraded to confirm they match.
`wd mcp serve` narrows this: the console script's shebang fixes the
interpreter to the one weld was installed with, so the only way to get a
different environment is to invoke a different `wd`.

If the versions already match, read the `Detail:` tail of the message. It
names the import that failed, which is the only thing separating a genuinely
old SDK from something else on `sys.path` answering to `mcp` -- a
`PYTHONPATH` entry, a vendored copy, or a partial install. Rename or remove
whatever else provides the name, or launch the server from an environment
where only the SDK does. The server's own launch directory is not a
candidate: `wd mcp serve` never places it on the module search path, and the
`-m` form removes it before the SDK is imported (see
[trust model](#trust-model)).

**Client reports zero tools**
Verify that `wd mcp serve` runs from the command line in the same working
directory the client uses. If the client launches from your home directory
but the Weld repo lives elsewhere, either pass the repo path as an argument
(`wd mcp serve /path/to/repo`) or set the client's working directory to the
repo root.

**`weld_stale` reports stale**
The on-disk `.weld/graph.json` trails the working tree. `weld_stale` reports
this without fixing it -- it is the probe, not a refresh trigger. By default
the next graph-backed read auto-refreshes for you (and reports
`freshness.stale` inline), so no manual step is needed; call `weld_query` (or
any other graph-backed read) and the graph is rebuilt on the way to the
answer. If the server was launched with `WELD_AUTO_REFRESH=0`, refresh is
disabled everywhere: run `wd discover` in a shell to rebuild, or restart the
server without the opt-out.

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

**Client cannot find `wd`**
Some editors start MCP servers from a restricted PATH. Replace
`"command": "wd"` with an absolute path to the console script (for example
`/usr/local/bin/wd`, or `<venv>/bin/wd` for a virtualenv install) so the
client launches it deterministically. `wd --version` in your own shell
prints nothing useful here; use `which wd` -- or, for a `uv tool` install,
`uv tool dir` -- to get the path the client needs. Prefer the absolute
script over falling back to `python -m weld.mcp_server`: the script keeps
both the fixed interpreter and the clean module search path.

## See also

- [`README.md`](../README.md) -- full Weld user guide, CLI reference,
  agent integration overview
- [`SECURITY.md`](../SECURITY.md) -- security policy and reporting
- [`.mcp.json`](../.mcp.json) -- this repo's own client configuration
  (source-checkout form; run `wd mcp config --client=claude` for the shape
  an installed weld should be given)
- [`weld/mcp_server.py`](../weld/mcp_server.py) -- stdio entrypoint and
  dispatch
- [`weld/_mcp_tools.py`](../weld/_mcp_tools.py) -- tool descriptors and
  JSON Schemas
