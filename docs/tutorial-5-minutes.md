# Weld in 5 Minutes

A linear walk-through that takes a fresh install to two live demos -- one
monorepo, one polyrepo -- and leaves you with a queryable graph of each.
Every command below runs against demos that ship in this tree. No prior
Weld knowledge required.

## 0. Install (30 seconds)

```bash
uv tool install configflux-weld
wd --help
```

```
Usage: wd <command> [args]

Core commands:
  init           Bootstrap .weld/discover.yaml for the current repo
  discover       Run discovery and emit graph JSON to stdout
  ...
Retrieval commands:
  brief          Agent-facing context briefing (stable JSON contract)
  query          Tokenized graph search
  find           File-index keyword search
  context        Node + immediate neighborhood
  path           Shortest path between nodes
  ...
```

`curl | sh`, `pipx install configflux-weld`, and `pip install configflux-weld`
are equivalent. See the [README Quickstart](../README.md#quickstart) for
every install path.

Everything below assumes `wd` is on your PATH.

---

## 1. Monorepo demo -- cross-package queries (2 minutes)

The target is a realistic TypeScript monorepo with apps, packages, libs, a
service, Docker runtime, CI, and ADRs. It ships at
[`examples/04-monorepo-typescript`](../examples/04-monorepo-typescript).

```bash
cd examples/04-monorepo-typescript
wd init                       # one-time; this demo ships a pre-committed config
wd discover --output .weld/graph.json
wd build-index                # enables `wd find` substring search
```

`wd init` scans the repo and writes `.weld/discover.yaml`. On a fresh
repo it prints progress to stderr (skipped here because this demo ships
a pre-committed config):

```
Scanning for files...
Found 42 files total
  Found 18 TypeScript files
  ...
Wrote .weld/discover.yaml
```

`wd discover` scans the tree per `.weld/discover.yaml` and writes a
deterministic `graph.json`. On this demo it produces ~35 nodes covering
packages, source files, Dockerfiles, docker-compose services, the CI
workflow, architecture docs, and ADRs:

```
[weld] notice: no graph.json found, running full discovery
```

`wd build-index` regenerates the substring file index so `wd find`
works without tree-sitter:

```
Indexed 42 files -> .weld/file-index.json
```

### Query 1: locate a symbol across packages

```bash
wd find "Button"
```

What to look for: the file under `packages/ui/src/`, matched tokens
including the React component name and its props interface, and a
numeric relevance score. One command, one package boundary crossed.

```
{
  "query": "Button",
  "files": [
    { "path": "packages/ui/src/Button.tsx",
      "tokens": ["Button", "ButtonProps"], "score": 2 }
  ]
}
```

`wd find` reads the file-index; it works without tree-sitter. Use
`wd query` for graph-level matches (build targets, configs, docs).

### Query 2: inspect a package's exported symbols

```bash
wd context file:shared-types/src/index
```

What to look for: the file node's `exports` list -- the canonical domain
types that the web client **and** the backend service both depend on.

```
{
  "node": { "id": "file:shared-types/src/index",
    "props": {
      "exports": ["Item", "Order", "ApiError"],
      "file": "libs/shared-types/src/index.ts"
    },
    "type": "file"
  }
}
```

Both `@acme/api` (client) and `@acme/orders-api` (server) import from
this file. In a tree-sitter-enabled install (`pip install
'configflux-weld[tree-sitter]'`), the same command returns neighbors and
`imports` edges, making the cross-package dependency explicit.

### Query 3: one service, one query, every surface

```bash
wd query "orders-api"
```

What to look for: the single query returns the service's source files,
its `package.json` config, its build and test targets, **and** the
Dockerfile that packages it. Code, config, and runtime surfaces in one
response -- the breadth `grep` and LSP cannot give you.

```
file:orders-api/src/server        exports: [createOrder, getOrder, listOrders]
file:orders-api/src/routes        exports: [postOrders, getOrders, getOrderById]
config:services_orders-api_package_json   file: services/orders-api/package.json
dockerfile:Dockerfile                     file: docker/Dockerfile.orders-api
build-target:npm:@acme/orders-api:build
build-target:npm:@acme/orders-api:start
test-target:npm:@acme/orders-api:lint
test-target:npm:@acme/orders-api:test
```

Try `wd query "architecture"` or `wd query "monorepo layout"` to surface
`docs/architecture.md` and `docs/adr/0001-monorepo-layout.md` -- docs
and ADRs are first-class nodes in the same graph as the code.

---

## 2. Polyrepo demo -- cross-repo federation (2 minutes)

The target is a three-child workspace stitched together by a
`workspaces.yaml` registry. It ships at
[`examples/05-polyrepo`](../examples/05-polyrepo) with two FastAPI
services (`api`, `auth`) and a shared Pydantic library.

Federation requires each child to be a git repo. The in-tree demo ships
without child `.git/` directories; add them so federation sees the
children:

```bash
cd ../05-polyrepo
for child in services/api services/auth libs/shared-models; do
  (cd "$child" && git init --quiet && git add -A && \
     git commit --quiet -m "demo seed" 2>/dev/null || true)
done
```

Now discover each child and federate at the root:

```bash
# 1. Discover each child (produces per-child graphs)
for child in services/api services/auth libs/shared-models; do
  (cd "$child" && wd discover --output .weld/graph.json)
done

# 2. Federate at the workspace root
wd discover --output .weld/graph.json

# 3. Inspect the workspace ledger
wd workspace status
```

Per-child discovery is quiet on success; the federated root run prints a
one-line notice the first time it builds a workspace graph:

```
[weld] notice: no graph.json found, running full discovery
```

Expected `wd workspace status` output (branch and sha values are host-specific):

```
Workspace status (3 children)
Counts: present=3, missing=0, uninitialized=0, corrupt=0
libs-shared-models: present dirty (refs/heads/<branch> <sha>)
services-api:       present dirty (refs/heads/<branch> <sha>)
services-auth:      present dirty (refs/heads/<branch> <sha>)
```

> If any child shows `missing`, it lacks a `.git/` directory. Running
> `git init` inside the child folder moves it to `present`.

### Query 1: see both sides of a cross-repo HTTP call

`services/api/src/server.py` makes `POST http://services-auth:8080/tokens`,
and `services/auth/src/app.py` registers the matching FastAPI endpoint.
A single federated query surfaces both sides at once:

```bash
wd query "tokens"
```

What to look for: two matches from two different repos -- one outbound
RPC node in `services-api`, one inbound route node in `services-auth` --
namespaced with their child names.

```
services-api/rpc:http:out:POST:http://services-auth:8080/tokens
  url: http://services-auth:8080/tokens
  source_strategy: http_client
services-auth/route:POST:/tokens
  source_strategy: fastapi
```

No grep across checkouts, no manual URL-to-route matching. One query
spans both repos.

### Query 2: inspect a child repo's surface

```bash
wd context repo:services-auth
```

What to look for: the `repo:` node for `services-auth`, its declared
path in the workspace, and any workspace-level tags. In federation mode
every child is a first-class node the agent can ground against.

```
{
  "node": { "id": "repo:services-auth",
    "props": { "path": "services/auth", "tags": {"category": "services"},
      "source_strategy": "federation_root" },
    "type": "repo"
  }
}
```

The `service_graph` resolver in this workspace is configured to match
the outbound call in `services-api` against the `POST /tokens` endpoint
in `services-auth` and emit a `cross_repo:calls` edge between them. See
[`examples/05-polyrepo/README.md`](../examples/05-polyrepo/README.md)
for the full federation model, resolver provenance, and how missing or
corrupt children are handled.

---

## 3. Next steps

- **Use it from an agent.** [`docs/mcp.md`](mcp.md) shows how to wire
  Weld into Claude Code, Cursor, Codex, or any MCP-capable client.
  `weld_query`, `weld_context`, `weld_path`, and `weld_trace` become
  tool calls instead of shell commands.
- **Point it at your own repo.** `wd init` in your repo root writes a
  starter `.weld/discover.yaml`. Tune the source globs, re-run
  `wd discover`, and you have the same graph over your codebase.
- **Unlock cross-package edges.** Install the tree-sitter extra
  (`pip install 'configflux-weld[tree-sitter]'`) so `typescript_exports`,
  `python_imports`, and the other parser-backed strategies emit `imports`
  edges between files and packages.
- **Go deeper.** The [README](../README.md) covers strategies, polyrepo
  workspace rules, enrichment, MCP tools, and the agent-graph workflow.

If you got here in five minutes, you have a working Weld graph over
both shapes Weld is designed to handle -- monorepo and polyrepo
federation. That is the full on-ramp.
