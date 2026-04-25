# Agent Graph Workflow

Use this workflow before changing repository agent customizations.

1. Run `wd agents discover` to refresh `.weld/agent-graph.json`.
2. Run `wd agents audit` to capture existing consistency findings.
3. Run `wd agents explain <name-or-path>` for the asset being changed.
4. Run `wd agents impact <name-or-path>` to find related variants, references, and downstream assets.
5. For behavior changes, run `wd agents plan-change "<request>"`.
6. Edit the smallest authoritative set of customization files.
7. Re-run `wd agents discover` and `wd agents audit`.
8. Report changed files, checked relationships, resolved conflicts, and remaining risks.

The workflow is static. It reads known AI customization files and does not execute project scripts, hooks, commands, LLM calls, or network calls.

## Change Boundaries

- Instructions provide always-on repository or path-specific guidance.
- Prompts and commands package reusable task requests.
- Skills package portable workflows and reference material.
- Agents define persistent personas, model hints, and tool boundaries.
- Hooks provide deterministic lifecycle automation and need explicit safety notes.
- MCP configuration connects external tools and data sources.

Prefer changing canonical assets first. If no canonical source is declared, update all related platform variants or explicitly document why a platform-specific override should drift.
