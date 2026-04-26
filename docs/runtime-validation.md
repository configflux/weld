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
result: pass | partial | fail
notes: "<observations, follow-ups, screenshots reference>"
```

Required fields: `platform`, `client_version`, `date_tested`, `tested_by`,
`os`, `scenario`, `result`. `notes` is optional but strongly recommended for
any partial or fail result.

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

- [ ] **Codex `AGENTS.md` + skill**: Codex CLI installed, `wd bootstrap codex`
  applied, `.codex/config.toml` and `.codex/skills/weld/SKILL.md` consumed at
  runtime, the embedded Weld skill invoked, and the resulting tool call
  observed in Codex.
- [ ] **Claude Code MCP + skill/subagent**: Claude Code installed,
  `.mcp.json` and `wd bootstrap claude` applied, the Weld MCP server reachable
  via stdio, the generated `.claude/commands/weld.md` invoked, and the
  resulting MCP tool call observed in Claude Code.
- [ ] **VS Code / Copilot custom instructions**: VS Code with GitHub Copilot
  installed, `wd bootstrap copilot` applied, `.github/copilot-instructions.md`
  and `.github/instructions/weld.instructions.md` consumed by Copilot at
  runtime, and the documented behavior observed.

A row stays unchecked until its YAML record appears under **Records** below.

### Checklist before merging a record

- The `platform` field uses the exact wording from the platform support
  matrix.
- All required fields (`platform`, `client_version`, `date_tested`,
  `tested_by`, `os`, `scenario`, `result`) are populated.
- `client_version` matches what the client itself reports.
- `scenario` covers install, configuration, invocation, and observation.
- `result` is one of `pass`, `partial`, or `fail`.
- If `result` is `partial` or `fail`, `notes` explains why and points at any
  follow-up issue.
- If the record changes the row's effective Public status (for example,
  Partial → Supported), the [platform support matrix](platform-support.md)
  is updated in the same change set.

## Records

No live-client runtime validation records have been recorded yet. Add new
entries below using the template at the top of this document. Place the
newest record first.

<!-- Example template (uncomment and fill in when adding a real record):

### YYYY-MM-DD — <platform key>

```yaml
platform: <platform key from the matrix>
client_version: "..."
date_tested: "YYYY-MM-DD"
tested_by: "..."
os: "..."
scenario:
  - install weld
  - configure the integration
  - invoke the agent or skill
  - verify Weld tool call or generated asset behavior
result: pass
notes: "..."
```

-->

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
5. Run the local task gate (`./local-task-gate --scope=auto`) before
   landing.

## Cross-references

- [Platform support matrix](platform-support.md) — per-row public status,
  capability columns, and short notes.
- [Agent graph](agent-graph.md) — how Weld discovers and links agent assets
  across platforms; runtime claims are governed by this page.
- [Launch material](launch.md) — public-facing copy. Launch posts should
  reference the platform support matrix and avoid claims that are not yet
  backed by a record here.
