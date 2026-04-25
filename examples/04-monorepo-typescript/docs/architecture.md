# @acme Monorepo Architecture

A small demo architecture that exercises the pieces weld is designed to
surface: apps, packages, services, shared libraries, container runtime,
CI, and ADR-backed decisions.

## Components

- **apps/web** (`@acme/web`): Next.js-style application. Consumes UI
  components from `@acme/ui` and data access from `@acme/api`.
- **packages/ui** (`@acme/ui`): Reusable React component library
  (`Button`, `Card`). Pure presentational.
- **packages/api** (`@acme/api`): Typed fetch client for the backend.
  Re-exports domain types from `@acme/shared-types`.
- **libs/shared-types** (`@acme/shared-types`): Canonical domain types
  (`Item`, `Order`, `ApiError`) shared between client and server.
- **services/orders-api** (`@acme/orders-api`): Minimal backend service
  exposing order CRUD. Imports `@acme/shared-types`.

## Runtime Topology

```
  +-----------+        +---------------+        +-----------------+
  |  web (UI) | -----> |  @acme/api    | -----> |  orders-api     |
  +-----------+        |  fetch client |        |  (HTTP service) |
        |              +---------------+        +--------+--------+
        v                       |                        |
  +-----------+                 v                        v
  | @acme/ui  |         @acme/shared-types        postgres:16
  +-----------+         (Item, Order, ApiError)
```

## Cross-Cutting Concerns

- **CI**: `.github/workflows/ci.yml` lints, builds, and tests on push
  and pull request.
- **Container runtime**: `docker/Dockerfile.orders-api` + `docker/docker-compose.yml`
  run the service and a local postgres for development.
- **Shared types**: `libs/shared-types` is the only source of truth for
  domain shapes. Both the client and the server import from it.

## Why This Shape

See [docs/adr/0001-monorepo-layout.md](./adr/0001-monorepo-layout.md)
for the decision record that locks in the apps/packages/libs/services
split.
