---
applyTo: ["**"]
---

# Repository Agent Instructions

Use mcp:github for repository metadata and read @docs/architecture/missing.md
before changing architecture-sensitive files.

## Path-scoped guidance

The instruction file applies repo-wide via the `applyTo` frontmatter above.
Tighter scopes live in adjacent files:

- `.cursor/rules/cpp.mdc` is scoped to `src/**` (Cursor only).
- `.github/instructions/cpp.instructions.md` is scoped to `src/**` (Copilot).
- `.github/instructions/testing.instructions.md` is scoped to `src/**`.

When the same path scope is asserted by multiple platforms, the audit's
`path_scope_overlap` finding flags the collision.
