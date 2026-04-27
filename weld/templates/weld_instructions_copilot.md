---
applyTo: "**"
description: >
  Always-on discovery primer for the weld repository connected structure.
  Triggers on weld, wd, workspace graph, discovery wave, repo map,
  query graph, federation, polyrepo, and related topics. Points agents at
  the weld skill for on-demand deep-dives.
---

# Weld Discovery Primer

This repository ships a repository connected structure managed by the
external `wd` CLI. Use it to learn the codebase before grepping.

## Before you search code

<!-- weld-managed:start name=before-you-search -->
1. Check that the tool is installed -- `wd --version`. If the command is
   missing, tell the user; do not fall back to installing it silently.
2. Prefer `wd brief "<topic>"` over `grep` / `rg` as your first move. It
   returns a ranked, classified context packet tuned for agents.
3. Use `wd query "<term>"` when `brief` is too narrow, and
   `wd context "<node-id>"` to drill into a specific node.
<!-- weld-managed:end name=before-you-search -->

<!-- weld-managed:start name=trust-boundary -->
Run `wd discover` automatically only on repositories you trust. Project-local
strategies under `.weld/strategies/` are Python modules loaded at discovery
time, and `strategy: external_json` executes configured commands from
`discover.yaml`.
<!-- weld-managed:end name=trust-boundary -->

## Federation awareness

If a `.weld/workspaces.yaml` file exists at the discovery root, the root is
a **polyrepo workspace** and discovery operates in federation mode across
nested child repos. Mention this to the user when relevant; do not assume a
single-repo layout.

## Do not assume MCP

Treat weld as a CLI-first tool. An MCP server may or may not be wired into
your host. Read `wd --help` to confirm available commands instead of
guessing, and fall back to the CLI when in doubt.

## Ask before writing to the graph

Never run `wd` subcommands that write graph data (for example
`wd add-node`, `wd add-edge`, `wd enrich`, `wd remember`) without asking
the user first. Reads (`wd query`, `wd context`, `wd brief`, `wd find`,
`wd --help`) are safe; writes must be explicitly authorized for the task
at hand.

## Deep-dives

For the full command catalog, manual enrichment workflow, and maintenance
cadence, load the `weld` skill. This instruction file is intentionally
short; the skill carries the details.
