# Runtime Validation Records

This document tracks **live-client runtime validation** for platforms listed in
the [platform support matrix](platform-support.md). Static fixture tests live
in the Bazel test suite; this page records the separate, harder evidence that
a real client was installed, configured against Weld, and observed working.

## Why this exists

Static discovery and config generation are validated by automated fixture
tests. Those tests prove that Weld parses and emits the expected files, but
they do **not** prove that a downstream client (Claude Code, Codex, VS Code,
Cursor, Copilot, Gemini, etc.) actually consumes those files at runtime and
behaves as expected.

Before any platform is claimed as **Supported** for broad public launch, at
least one real-client validation record must appear below.

Until that happens, public-facing claims should:

- Use the per-row **Public status** in the
  [platform support matrix](platform-support.md), not unqualified
  blanket-support wording.
- Reference this document so readers can see what has and has not been
  validated against a live client.

## Record format

The schema below is a **stable contract** for runtime-validation records.
Field names and the set of required fields are fixed; new optional fields
may be added later but existing fields will not be removed or renamed
without a deprecation window.

Each runtime validation entry follows this YAML-style block:

```yaml
platform: <platform key from the matrix>
client_version: "<version of the downstream client tested>"
date_tested: "YYYY-MM-DD"
tested_by: "<handle or display name>"
os: "<OS name and version>"
scenario:
  - <step 1>
  - <step 2>
  - ...
result: pass | partial | fail | pending
notes: "<observations, follow-ups, screenshots reference>"
```

Required fields: `platform`, `client_version`, `date_tested`, `tested_by`,
`os`, `scenario`, `result`. `notes` is optional but strongly recommended for
any partial or fail result.

`result: pending` marks a stub block that holds a row open against an
upcoming live-client test. Stubs must be clearly tagged (record heading
ends with "(stub, pending live test)") and must include an HTML comment
above the YAML block naming the human action and the steps to fill in.
The launch-copy guard (`tools/runtime_claims_lint.py` -> launch rule) will
allow `docs/launch.md` to reference a row backed by a `pending` stub --
swap the stub for a real `pass`/`partial`/`fail` record once the live
test runs.

A scenario should cover, at minimum: install Weld, configure the integration
(MCP, skill, AGENTS.md, etc.), invoke the asset, and observe expected output
or behavior.

## Validation status by platform

The table below mirrors the public-status column of the
[platform support matrix](platform-support.md) and records whether at least
one real-client runtime validation record exists for each row.

| Platform / format | Public status | Runtime validation evidence | Required for broad launch? |
|---|---|---|---|
| Generic `AGENTS.md` | Supported | Static instruction asset; no live client required. Fixture coverage is sufficient. | No — static asset. |
| Generic `SKILL.md` / Agent Skills | Supported | None yet (TBD). Fixture-tested only. | Yes — at least one validation record below before broad launch. |
| GitHub Copilot repository instructions | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |
| GitHub Copilot / VS Code custom agents | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |
| VS Code MCP | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |
| Claude Code skills/subagents | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |
| Codex `AGENTS.md` | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |
| Codex skills | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |
| Cursor rules | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |
| Cursor skills/subagents/hooks | Experimental | Fixture-tested only. | No — Experimental relies on fixture tests. |
| OpenCode `AGENTS.md` | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |
| OpenCode agents/commands/config | Experimental | Fixture-tested only. | No — Experimental relies on fixture tests. |
| Gemini `GEMINI.md` | Experimental | Fixture-tested only. | No — Experimental relies on fixture tests. |
| Gemini custom commands/subagents | Planned | Not implemented. | No — not yet shipped. |
| Generic MCP clients | Partial | Fixture-tested only. | No — Partial relies on fixture tests. |

### Fixture-only acknowledgement

For every row above whose **Public status** is **Partial**, **Experimental**,
or **Planned**, the project explicitly relies on fixture tests only. Those
rows do not require a runtime validation record before the next launch
window. They will need one before being upgraded to **Supported**, at which
point this page must list a record meeting the format above.

### Supported rows that still need a record

The following Supported rows currently lack a live-client validation record
and must obtain one before any wider public-launch claim:

- `Generic SKILL.md` / Agent Skills — generated copies need client
  validation.

`Generic AGENTS.md` is treated as a static instruction asset (the file is
read by humans and by clients in the same way as a README). Its fixture
coverage is considered sufficient evidence and it does not require a live
client record.

### Required-before-broad-launch records

Three live-client runtime-validation records must land here before the next
broad-launch communication ships. Each record is captured by exercising a
real client install end-to-end:

- [~] **Codex `AGENTS.md` + skill**: Codex CLI installed, `wd bootstrap codex`
  applied, `.codex/config.toml` and `.codex/skills/weld/SKILL.md` consumed at
  runtime, the embedded Weld skill invoked, and the resulting tool call
  observed in Codex. *Bootstrap-and-asset evidence recorded
  2026-04-26 (`result: partial`); live-client run still required.*
- [ ] **Claude Code MCP + skill/subagent**: Claude Code installed,
  `.mcp.json` and `wd bootstrap claude` applied, the Weld MCP server reachable
  via stdio, the generated `.claude/commands/weld.md` invoked, and the
  resulting MCP tool call observed in Claude Code. *Stub recorded
  (`result: pending`); live tester action needed.*
- [ ] **VS Code / Copilot custom instructions**: VS Code with GitHub Copilot
  installed, `wd bootstrap copilot` applied, `.github/copilot-instructions.md`
  and `.github/instructions/weld.instructions.md` consumed by Copilot at
  runtime, and the documented behavior observed. *Stub recorded
  (`result: pending`); live tester action needed.*

A row stays unchecked until a YAML record with `result: pass` (or
`partial`/`fail` with a remediation plan) appears under **Records** below.
A `pending` stub keeps the row on this page so the launch-copy guard
allows references, but does not satisfy the broad-launch requirement.

### Checklist before merging a record

- The `platform` field uses the exact wording from the platform support
  matrix.
- All required fields (`platform`, `client_version`, `date_tested`,
  `tested_by`, `os`, `scenario`, `result`) are populated.
- `client_version` matches what the client itself reports.
- `scenario` covers install, configuration, invocation, and observation.
- `result` is one of `pass`, `partial`, `fail`, or `pending` (the last is
  reserved for stubs awaiting a live-client test).
- If `result` is `partial` or `fail`, `notes` explains why and points at any
  follow-up issue. If `result` is `pending`, the block is preceded by an
  HTML comment naming the human action and steps to fill in.
- If the record changes the row's effective Public status (for example,
  Partial → Supported), the [platform support matrix](platform-support.md)
  is updated in the same change set.

## Records

Newest record first. Stubs (`result: pending`) are explicitly tagged: they
hold the row open against an upcoming live-client test and are surfaced by
the launch-copy guard so the matrix can be referenced in launch material
without claiming a pass that has not happened yet.

<!-- runtime-pending: codex (live-client run still required; bootstrap-and-asset evidence only) -->

### 2026-04-26 — Codex `AGENTS.md` + skill (automated, partial — pending live-client run)

```yaml
platform: Codex `AGENTS.md`
client_version: "n/a (no Codex CLI installed; weld 0.10.1 only)"
date_tested: "2026-04-26"
tested_by: "automated (claude-code session)"
os: "linux (devcontainer)"
scenario:
  - run `wd bootstrap codex` in a fresh git scratch repo
  - confirm `.codex/config.toml` registers the `weld` MCP server with
    `command = "python"` and `args = ["-m", "weld.mcp_server"]`
  - confirm `.codex/skills/weld/SKILL.md` is generated and parses as
    Markdown with the expected `## What it is`, `## When to use it`, and
    `## Retrieval commands` sections
  - run `wd discover --output .weld/graph.json` and `wd query weld`
    against the scratch repo to confirm the underlying Weld surface the
    skill points at is functional
result: partial
notes: |
  Observed locally from a non-interactive session:
    - bootstrap succeeds and writes the documented file set
    - the generated SKILL.md is well-formed and references the same `wd`
      commands that are documented in the matrix's Codex `AGENTS.md` row
    - `wd discover` + `wd query` work end-to-end against the scratch repo
  Not observed (genuinely requires a live Codex client, which this
  session cannot drive):
    - Codex CLI loading `.codex/config.toml` and starting the `weld` MCP
      server over stdio
    - the embedded skill being invoked from a Codex turn and producing a
      tool-call back into the local Weld MCP surface
  Treat this as bootstrap-and-asset evidence rather than end-to-end
  Codex-client evidence; the row stays `Partial` on the matrix until a
  live Codex run is recorded.
```

<!-- runtime-pending: claude-code (awaiting live-client session) -->

### TBD — Claude Code MCP + skill/subagent (stub, pending live-client session)

<!--
Tester: open Claude Code on a repo where this Weld repo is on PATH.
Steps:
  1. From the project root, run `wd bootstrap claude` and confirm
     `.claude/commands/weld.md` is written.
  2. Ensure `.mcp.json` registers the `weld` MCP server (see project
     root `.mcp.json` for the canonical shape).
  3. Start Claude Code and verify the `weld` MCP server attaches
     (look for `weld_query`, `weld_find`, `weld_context` in the tool
     list).
  4. From a Claude Code turn, invoke `/weld <term>` (the bundled
     command) and confirm a `weld_query` tool call returns matches
     against the local graph.
Fill in `client_version`, `date_tested`, `tested_by`, `os`, the
observed `result` (pass / partial / fail), and detailed notes. Replace
the `result: pending` line with the real outcome.
-->

```yaml
platform: Claude Code skills/subagents
client_version: "TBD (e.g. claude-code 0.x.y as reported by `claude --version`)"
date_tested: "TBD"
tested_by: "TBD (human handle)"
os: "TBD"
scenario:
  - run `wd bootstrap claude` in a real repo
  - confirm `.claude/commands/weld.md` and a working `.mcp.json` are
    in place
  - launch Claude Code and confirm the `weld` MCP server attaches
  - invoke `/weld <term>` from a Claude Code turn
  - observe a `weld_query` tool call returning matches from the local
    graph
result: pending
notes: |
  Stub. No live Claude Code client has been driven against this
  repository yet. Replace `result` with `pass` / `partial` / `fail`
  once the steps above have been executed against a real install.
```

<!-- runtime-pending: vscode-copilot (awaiting live-client session) -->

### TBD — VS Code / Copilot custom instructions (stub, pending live-client session)

<!--
Tester: open VS Code with the GitHub Copilot extension installed and
authenticated. Steps:
  1. From the project root, run `wd bootstrap copilot` and confirm
     `.github/copilot-instructions.md`,
     `.github/instructions/weld.instructions.md`, and
     `.github/skills/weld/SKILL.md` are written.
  2. Reload the VS Code window so Copilot picks up the new
     instructions files.
  3. From the Copilot Chat panel, ask a project question that should
     trigger the Weld instructions (for example: "Where is X
     defined?"). Verify Copilot's reply references the language /
     idioms from `.github/instructions/weld.instructions.md`.
Fill in `client_version`, `date_tested`, `tested_by`, `os`, the
observed `result`, and notes describing what Copilot did and did not
respect. Replace the `result: pending` line with the real outcome.
-->

```yaml
platform: GitHub Copilot repository instructions
client_version: "TBD (e.g. Copilot extension version + VS Code version)"
date_tested: "TBD"
tested_by: "TBD (human handle)"
os: "TBD"
scenario:
  - run `wd bootstrap copilot` in a real repo
  - confirm `.github/copilot-instructions.md` and
    `.github/instructions/weld.instructions.md` are written
  - reload VS Code so Copilot picks up the new instructions
  - ask Copilot a project question via Copilot Chat
  - observe Copilot honoring the wording / scope from the instructions
    files
result: pending
notes: |
  Stub. No live VS Code + Copilot session has been driven against this
  repository yet. Replace `result` with `pass` / `partial` / `fail`
  once the steps above have been executed against a real install.
```

## How to add a record

1. Pick the matching `platform` key from the
   [platform support matrix](platform-support.md). Use the same wording for
   the platform name so the two documents stay in sync.
2. Run the scenario against a real client install of the platform. Note the
   client version exactly as the client reports it.
3. Append a new section under **Records** with the YAML block filled in.
4. If the result moves a row's effective status (for example, Partial →
   Supported), open a follow-up to update the
   [platform support matrix](platform-support.md) in the same change set so
   the two pages stay consistent.
5. Run the repository's configured local verification before landing.

## Cross-references

- [Platform support matrix](platform-support.md) — per-row public status,
  capability columns, and short notes.
- [Agent graph](agent-graph.md) — how Weld discovers and links agent assets
  across platforms; runtime claims are governed by this page.
- [Launch material](launch.md) — public-facing copy. Launch posts should
  reference the platform support matrix and avoid claims that are not yet
  backed by a record here.
