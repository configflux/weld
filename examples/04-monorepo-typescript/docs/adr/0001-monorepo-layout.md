# ADR 0001: Monorepo Layout

Status: Accepted
Date: 2026-04-24
Related issues: demo-epic-3

## Context

We need a folder layout for the `@acme` monorepo that a new reader can
orient against quickly. The layout also needs to demonstrate the kinds
of cross-cutting edges weld surfaces: cross-package imports, service
-> shared-lib dependencies, and runtime/CI artifacts.

## Decision

Adopt a four-bucket layout:

- `apps/` -- user-facing applications (`web`).
- `packages/` -- front-end-facing libraries (`ui`, `api`).
- `libs/` -- polyglot/shared libraries consumed by both client and
  server (`shared-types`).
- `services/` -- long-running backend services (`orders-api`).

Supporting trees:

- `docker/` -- container runtime assets.
- `.github/workflows/` -- CI definitions.
- `docs/` -- architecture doc + ADR history.

## Consequences

- Clear mental model: "is this UI-facing, server-side, or shared?".
- `libs/shared-types` becomes the canonical domain model; both
  `@acme/api` and `@acme/orders-api` import from it.
- Weld discovery emits service, file, config, and workflow nodes out
  of the box without custom strategies.
- Adding a new backend service is a matter of creating
  `services/<name>/` with a `package.json` and a source entry in
  `.weld/discover.yaml`.
