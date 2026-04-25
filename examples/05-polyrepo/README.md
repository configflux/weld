# Example 05: Polyrepo Federation

A federated polyrepo workspace with three in-tree children. Discovery
at the root reads each child's graph, emits `repo:<name>` nodes, and
runs the `service_graph` cross-repo resolver to surface a visible
call-edge between two sibling services. Target of the polyrepo half of
the 5-minute demo.

## What This Example Contains

```
05-polyrepo/
  .weld/
    discover.yaml            root-level discovery config
    workspaces.yaml          three children + service_graph resolver
  services/
    api/                     (services-api) -- FastAPI app with one
      .weld/discover.yaml    outbound httpx call to services-auth
      src/server.py
    auth/                    (services-auth) -- FastAPI app exposing
      .weld/discover.yaml    POST /tokens, the cross-repo match target
      src/app.py
  libs/
    shared-models/           (libs-shared-models) -- Pydantic models
      .weld/discover.yaml    shared by both services
      src/models.py
```

Each child owns its own `.weld/` directory and can be discovered
standalone. The root workspace stitches them together in federation
mode.

## Running the Demo

```bash
cd examples/05-polyrepo

# 1. Discover each child (produces the per-child graphs)
for child in services/api services/auth libs/shared-models; do
  (cd "$child" && wd discover --output .weld/graph.json)
done

# 2. Run federated discovery at the workspace root
wd discover --output .weld/graph.json

# 3. Inspect workspace child lifecycle
wd workspace status

# 4. Query the federated graph for the cross-repo edge
wd query "services-auth"
```

## Demo Narrative

1. **Three children, one workspace.** `wd workspace status` lists
   `services-api`, `services-auth`, `libs-shared-models` -- three
   independent repo-like folders stitched together by
   `.weld/workspaces.yaml`.
2. **Federated root graph.** The root `wd discover` run reads each
   child's `.weld/graph.json` (never modifying them), emits a
   `repo:<name>` node per child, and records the lifecycle ledger in
   `.weld/workspace-state.json`.
3. **Cross-repo edge is visible.** `services/api/src/server.py`
   contains `httpx.post("http://services-auth:8080/tokens", ...)`.
   `services/auth/src/app.py` declares `@app.post("/tokens")`. The
   `service_graph` resolver matches the two and emits one
   `cross_repo:calls` edge in the root graph, connecting the client
   call site to the sibling's endpoint node.
4. **Cross-repo query.** `wd query "cross_repo:calls"` (or
   `wd context` on the API call-site node) surfaces the edge plus both
   endpoints, demonstrating a single query that spans two repos.

## Step 1: Initialize each child

Each child repo needs its own `.weld/` directory with a discovery config
and a graph:

```bash
cd services/api
wd discover --output .weld/graph.json

cd ../auth
wd discover --output .weld/graph.json

cd ../../libs/shared-models
wd discover --output .weld/graph.json
```

In a greenfield polyrepo, `wd init` inside each child generates a
starter `discover.yaml`. This example ships the configs pre-committed
so the demo is deterministic.

## Step 2: Initialize the workspace root

At the workspace root, `wd init` detects nested `.weld/` directories
and writes `discover.yaml` plus `workspaces.yaml`:

```bash
cd examples/05-polyrepo
wd init
```

Expected output:

```
Wrote /path/to/examples/05-polyrepo/.weld/discover.yaml
Wrote /path/to/examples/05-polyrepo/.weld/workspaces.yaml
```

To limit how deep the scanner looks for nested repos:

```bash
wd init --max-depth 2
```

This example ships a pre-committed `workspaces.yaml` so the demo does
not depend on init output. See the file for the canonical schema.

## Step 3: Review workspaces.yaml

The pre-committed registry lists three children and enables
`service_graph`:

```yaml
version: 1
scan:
  max_depth: 3
  exclude_paths: [.worktrees, vendor]
children:
  - name: services-api
    path: services/api
    tags:
      category: services
  - name: services-auth
    path: services/auth
    tags:
      category: services
  - name: libs-shared-models
    path: libs/shared-models
    tags:
      category: libs
cross_repo_strategies: [service_graph]
```

Child names are auto-derived from paths (`services/api` becomes
`services-api`). Override them by editing the file. The host segment
of the outbound URL in `services/api/src/server.py` is
`services-auth` -- it names a sibling by child name, which is what
lets `service_graph` resolve the call statically.

## Step 4: Run discovery at the workspace root

```bash
cd examples/05-polyrepo
wd discover --output .weld/graph.json
```

In federation mode, `wd discover`:

1. Reads each child's `.weld/graph.json` (read-only -- child graphs are
   never modified).
2. Emits a `repo:<name>` node for every present child.
3. Runs each declared cross-repo resolver to produce edges between
   children (here: `service_graph`).
4. Writes `workspace-state.json` with the current lifecycle ledger.

Children that are missing (directory not found), uninitialized (no
`.weld/graph.json`), or corrupt (invalid JSON) degrade gracefully --
they are skipped and recorded in the ledger but do not block
discovery.

## Step 5: Check workspace status

```bash
wd workspace status
```

Expected output (HEAD info is workspace-dependent):

```
Workspace status (3 children)
Counts: present=3, missing=0, uninitialized=0, corrupt=0
services-api: present
services-auth: present
libs-shared-models: present
```

For the raw JSON ledger:

```bash
wd workspace status --json
```

This emits `workspace-state.json` content, which includes per-child
fields: `status`, `head_sha`, `head_ref`, `is_dirty`, `graph_path`,
`graph_sha256`, and `last_seen_utc`.

## Step 6: Verify the cross-repo edge

With `service_graph` enabled and the matching pair present in
`services-api` and `services-auth`, the root graph now contains one
`cross_repo:calls` edge. You can surface it with either the CLI or
the MCP tools:

```bash
wd query "cross_repo:calls"
wd query "services-auth"
```

Or via MCP:

```bash
weld_query("cross_repo:calls")
weld_context("repo:services-api")
```

The edge carries provenance props (`method`, `path`, `host`, `port`,
`source_strategy=service_graph`), so downstream consumers can trace
the match back to the resolver that produced it.

## Running the auth service

The graph-discovery demo above is fully static. The auth child can also be
launched as a real ASGI app -- handy for poking at the `POST /tokens`
endpoint or pairing the demo with a sibling that needs a live target.

From the polyrepo root:

```bash
uvicorn services.auth.src.app:app --port 8001
```

The service binds to `127.0.0.1:8001`. `app.py` resolves
`libs/shared-models/src` onto `sys.path` at import time so
`shared_models.models` is importable without an editable install. Requires
`fastapi`, `uvicorn`, and `pydantic` in the active environment. This is a
demo runner only -- real auth issuance is out of scope.

## Running the api service

The api child is the cross-repo *caller* -- its `POST /login` handler
forwards to `http://services-auth:8080/tokens`. Run it alongside auth to
exercise the demo end-to-end (the static cross-repo edge in the graph
mirrors this runtime call).

From the polyrepo root:

```bash
uvicorn services.api.src.server:app --port 8000
```

The service binds to `127.0.0.1:8000`. `server.py` resolves
`libs/shared-models/src` onto `sys.path` at import time so
`shared_models.models` is importable without an editable install. Requires
`fastapi`, `uvicorn`, `httpx`, and `pydantic` in the active environment.
The outbound URL is `http://services-auth:8080/tokens` -- a real wire
call would need DNS or a hosts entry mapping `services-auth` to the auth
service's address (e.g. `127.0.0.1` if you launched auth on `--port 8001`
and patched the URL). This is a demo runner only; real authentication is
out of scope.

## Rollback

To disable federation and return to single-repo behavior:

```bash
rm .weld/workspaces.yaml
```

This returns weld to standard single-repo discovery at the root. Each
child's `.weld/` directory remains intact -- children continue to
work independently. Optionally, also remove the generated ledger:

```bash
rm .weld/workspace-state.json
```

## Key Points

- **Children are portable.** Each child owns its `.weld/` and can be
  moved, published, or used standalone.
- **Missing children degrade gracefully.** A child that is not cloned
  does not crash discovery. Clone it later and re-run `wd discover`.
- **Discovery is deterministic.** Running `wd discover` twice on the
  same input produces byte-identical `graph.json` output.
- **Resolvers are read-only.** Cross-repo resolvers never modify child
  graphs. They only read child data and emit edges into the root
  graph.
- **Static cross-repo edges.** `service_graph` matches only when the
  client URL's host names a sibling child and its `(method, path)`
  exactly matches a sibling endpoint. No guessing, no normalisation.
- **Rollback is one delete.** Remove `workspaces.yaml` to return to
  single-repo mode.
