# Codex Local Override

This override layers on top of `AGENTS.md` for Codex sessions. It captures
Codex-only conventions that the generic agent file should not impose on every
platform.

## Read-only by default

Prefer read-only analysis unless the user explicitly asks for edits. Codex
sessions running in CI may not have a writable working tree.

## Platform pointers

- See @.codex/agents/reviewer.md for the Codex reviewer entry point.
- Use mcp:filesystem for repository file context (declared in @.mcp.json).
- Architecture-sensitive changes follow @docs/architecture/principles.md.
