# Example: Monorepo TypeScript Discovery

A realistic, small-but-complete TypeScript monorepo that exercises the
things weld is designed to surface in a 5-minute demo: cross-package
imports, service-to-shared-lib dependencies, container runtime, CI
workflows, architecture docs, and ADRs.

## What This Example Contains

```
04-monorepo-typescript/
  package.json                (root workspace)
  apps/
    web/                      (@acme/web) -- Next.js-style app
  packages/
    ui/                       (@acme/ui) -- React component library
    api/                      (@acme/api) -- typed fetch client,
                              re-exports types from @acme/shared-types
  libs/
    shared-types/             (@acme/shared-types) -- canonical
                              domain types (Item, Order, ApiError)
  services/
    orders-api/               (@acme/orders-api) -- backend service,
                              depends on @acme/shared-types
  docker/
    Dockerfile.orders-api     multi-stage build for the service
    docker-compose.yml        orders-api + postgres dev stack
  .github/
    workflows/
      ci.yml                  lint -> build -> test pipeline
                              (illustrative / non-runnable -- see note below)
  docs/
    architecture.md           component + runtime topology overview
    adr/
      0001-monorepo-layout.md ADR locking in the four-bucket layout
  .weld/
    discover.yaml             weld discovery configuration
```

## Running Discovery

```bash
cd examples/04-monorepo-typescript
wd discover
```

## Demo Narrative

This example is the target of the 5-minute tutorial. The narrative
weld tells against this repo:

1. **Packages and apps** -- `wd query "Button"` surfaces the `@acme/ui`
   component, its consumers in `apps/web`, and the package boundary.
2. **Cross-package edges** -- `wd context file:web/App` shows
   `apps/web/src/App.tsx` importing from `@acme/ui` and `@acme/api`.
3. **Shared types across client and server** -- both `@acme/api` and
   `@acme/orders-api` import from `@acme/shared-types`. Weld renders
   this as a hub in the graph.
4. **Runtime and CI context** -- `wd query "orders-api"` returns
   source files, the Dockerfile that builds it, the compose service,
   and the CI workflow that tests it.
5. **Decisions with provenance** -- `wd query "monorepo layout"` turns
   up the architecture doc and `docs/adr/0001-monorepo-layout.md`.

## What the Graph Contains

After running discovery, the output JSON graph includes:

- **File nodes** for every TypeScript source, scoped by package via
  `id_prefix` (e.g., `file:ui/Button`, `file:api/client`,
  `file:shared-types/index`, `file:orders-api/server`,
  `file:web/App`).
- **Exported symbol lists** on each file node (`exports` property).
- **Import tracking** via `imports_from` -- the load-bearing edge for
  cross-package demos (e.g., `orders-api/server` imports
  `@acme/shared-types`).
- **Package containment edges** linking package/lib/service nodes
  (`pkg:ui`, `pkg:api`, `pkg:web`, `lib:shared-types`,
  `service:orders-api`) to their files.
- **Config nodes** for every `package.json`, plus `compose` and
  `gh_workflow` nodes for the runtime and CI surfaces.
- **File nodes for Dockerfiles** with stages, base images, and
  exposed ports extracted by the `dockerfile` strategy.
- **Doc nodes** for `docs/architecture.md` and the ADR history under
  `docs/adr/`.

## Key Discovery Configuration Patterns

### Per-Package Scoping

Each workspace package gets its own source entry with `id_prefix` and
`package` fields. This scopes node IDs by package and creates
containment edges:

```yaml
- glob: "packages/ui/src/**/*.{ts,tsx}"
  strategy: typescript_exports
  id_prefix: ui
  package: "pkg:ui"
```

Without `id_prefix`, files with the same name across packages (e.g.,
`index.ts`) would produce colliding node IDs.

### Cross-Package Dependencies

When tree-sitter is available, the `typescript_exports` strategy
captures import sources in each node's `imports_from` property. Key
cross-package edges in this example:

- `apps/web/src/App.tsx` imports from `@acme/ui` and `@acme/api`.
- `packages/api/src/types.ts` re-exports from `@acme/shared-types`.
- `services/orders-api/src/server.ts` imports from `@acme/shared-types`.

### Runtime and CI

`dockerfile`, `compose`, and `gh_workflow` strategies fold container
and CI definitions into the same connected structure as the source
code, so a single query can span "who builds this" and "what tests
it" alongside "what imports it".

> **Note**: `.github/workflows/ci.yml` in this example is a discovery
> fixture, not a runnable pipeline. The workspace ships no eslint or
> test runner config, so the `npm run lint` and `npm run test` steps
> would fail if executed -- the workflow is here purely so the
> `gh_workflow` strategy has something to extract. The file's header
> comment repeats this caveat for anyone who finds it independently.

### Docs and ADRs

The `firstline_md` strategy turns `docs/architecture.md` and every
file under `docs/adr/` into doc nodes. This is what lets weld answer
"why is the layout this shape?" by surfacing the ADR next to the code.

## Customizing

- Add more workspace packages by creating new directories under
  `apps/`, `packages/`, `libs/`, or `services/` and adding matching
  source entries to `.weld/discover.yaml`.
- Use `exclude` in source entries to skip generated files:
  `exclude: ["**/dist/**", "**/*.d.ts"]`.
- Combine with the `config_file` strategy to discover `tsconfig.json`
  files as configuration nodes.
