# Codex Reviewer

This file is read by Codex when invoked from `AGENTS.override.md`. The
demo intentionally keeps `.codex/agents/*` undiscovered by the static
graph: there is no platform rule for this directory yet (slice-3 will
add one). For now the file appears in the graph as a `references_file`
target reachable from the override, which is enough for the demo to
prove that real Codex projects have content in `.codex/`.

## Responsibilities

- Read the diff and surface critical issues.
- Defer to skill:security-review for dependency changes.
