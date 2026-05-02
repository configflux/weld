# Agent Graph

The Agent Graph is Weld's static, repo-local map of the AI customization
layer around a project: agents, skills, prompts, instructions, commands,
hooks, MCP servers, tool permissions, and the per-platform copies that
implement them.

Discovery reads the customization files that already live in your repository
and writes a deterministic graph to `.weld/agent-graph.json`. That graph is
what `wd agents` queries, audits, and uses to plan changes.

## What it is, and what it is not

**The Agent Graph is:**

- **Static.** Discovery reads files only. It never executes project code,
  hooks, commands, slash commands, LLM calls, MCP servers, or any other
  runtime behavior. The output is determined by file contents and the
  rules in this document.
- **Repo-local.** The graph is a single JSON file at
  `.weld/agent-graph.json`. There is no service, no daemon, and no
  network call. You commit the graph (or regenerate it) alongside your
  customization files.
- **Cross-platform.** One graph covers Claude Code, GitHub Copilot / VS
  Code, Codex, Cursor, Gemini CLI, OpenCode, generic `AGENTS.md` /
  `SKILL.md` files, and `.mcp.json`-style MCP configuration. Same-named
  agents on different platforms become explicit variants you can compare.
- **Deterministic.** Given the same set of files, discovery produces the
  same nodes, edges, and audit findings. There is no embedding, no
  ranking model, and no fuzzy retrieval involved in graph construction.

**The Agent Graph is not:**

- **A runtime.** It does not run agents, dispatch hooks, execute commands,
  or evaluate prompts. It only describes what is statically declared.
- **A type checker for prompts.** Audit findings are heuristic
  consistency checks (broken file references, duplicate names, drift
  between rendered copies, vague descriptions, etc.). They do not prove
  that an agent will behave correctly.
- **A renderer.** Weld ships a **preview** `wd agents render` command
  that can produce rendered copies from a canonical source, but the
  command is dry-run by default and intentionally minimal. See
  [Render and export status](#render-and-export-status).

## Supported asset types and platforms

The full per-platform support matrix lives in
[platform-support.md](platform-support.md). It records which surfaces are
**Supported**, **Partial**, **Experimental**, **Planned**, or **Not
supported** on each platform, and which are runtime-validated against a
real client.

The Agent Graph specifically discovers these file shapes:

| Pattern | Platform | Asset type |
|---|---|---|
| `AGENTS.md` | Generic | instruction |
| `AGENTS.override.md` | Codex | instruction |
| `CLAUDE.md` | Claude Code | instruction |
| `GEMINI.md` | Gemini CLI | instruction |
| `.github/copilot-instructions.md` | GitHub Copilot | instruction |
| `.github/instructions/*.instructions.md` | GitHub Copilot | instruction |
| `.github/prompts/*` | GitHub Copilot | prompt |
| `.github/agents/*` | GitHub Copilot | agent |
| `.github/skills/<name>/SKILL.md` | GitHub Copilot | skill |
| `.claude/agents/*` | Claude Code | agent |
| `.claude/skills/<name>/SKILL.md` | Claude Code | skill |
| `.claude/settings.json` | Claude Code | config (plus derived hook nodes) |
| `.cursor/rules/*` | Cursor | instruction |
| `.gemini/agents/*` | Gemini CLI | agent |
| `opencode.json` | OpenCode | config (plus derived agent / command / hook / mcp-server nodes) |
| `.mcp.json` | Generic | config (plus derived mcp-server nodes) |
| `<dir>/SKILL.md` | Generic | skill |

For runtime-behavior support claims (does the platform actually consume
these files when the client runs?), use the
[platform support matrix](platform-support.md).

## Node and edge types

A persisted graph has three top-level keys: `meta`, `nodes`, and `edges`.
Nodes are keyed by deterministic IDs of the form
`<type>:<platform>:<name>`. Edges always have a `type` and `props`
(including `provenance` with the source file and line).

### Node types

- `agent` — a per-platform agent definition (e.g. `.claude/agents/foo.md`,
  `.github/agents/foo.agent.md`, an `agents` entry in `opencode.json`).
- `subagent` — referenced but not yet defined as a first-class file.
- `skill` — a packaged skill (`SKILL.md` or platform-specific skill
  variants).
- `prompt` — a reusable prompt file (e.g. `.github/prompts/*`).
- `instruction` — guidance that scopes agent behavior
  (`AGENTS.md`, `CLAUDE.md`, Cursor rules, Copilot instructions).
- `command` — a slash-command-like entry, typically declared in
  `opencode.json`.
- `config` — a static configuration file
  (`.claude/settings.json`, `opencode.json`, `.mcp.json`).
- `hook` — a derived node for one entry in a config's `hooks:` block.
- `mcp-server` — a derived node for one entry in a config's `mcpServers:`
  / `mcp:` block, or an MCP server referenced from another asset.
- `tool` — a referenced tool name from a `tools` / `allowed_tools` /
  `permissions.allow` / `permissions.deny` field.
- `scope` — a path glob declared in `applyTo` / `paths` / `globs` fields
  on instructions or rules.
- `file` — a repository file referenced from any other asset (Markdown
  link, `@file` syntax, or a bare path).

### Edge types

- `provides_tool` — an asset declares that an agent / skill / config has
  access to a tool or MCP server.
- `restricts_tool` — an asset declares a deny-list entry for a tool.
- `handoff_to` — an agent declares it hands off to another agent.
- `invokes_agent` — text or config invokes another agent
  (`agent:planner` style references).
- `uses_skill` — an agent, command, prompt, or instruction explicitly
  references a skill (`skill:architecture-decision`, a `skills:` metadata
  list, or `Skill(skill="architecture-decision")` call syntax).
- `uses_command` — an asset references a slash-command-style entry.
- `applies_to_path` — an instruction or rule scopes itself to a file
  glob. Instruction files without an explicit `applyTo:` / `globs:` /
  `path_globs:` declaration default to repo-wide scope and emit one
  inferred edge to `**` (ADR 0021 Amendment 2). Explicit declarations
  always win and suppress the implicit edge.
- `references_file` — text contains a Markdown link, `@file` reference,
  or repository-relative path. Each such target also becomes a `file`
  node, with `props.exists` recording whether the target resolves.
- `configures` — a config file produces a derived child node (e.g.
  `opencode.json` configures one of its `agents:` entries).
- `triggers_on_event` — a config produces a derived `hook` child node.
- `generated_from` — a rendered or generated copy points back to its
  canonical source. See [Authority and drift](#authority-and-drift).

Each edge carries a `confidence` (`definite` for parsed structural
references, `inferred`/`speculative` reserved for future heuristics) and a
`provenance` block recording the source file, line number, and the raw
text that produced the edge.

### Skill Reference Boundaries

`uses_skill` is an explicit static relationship. Weld does not infer a
skill edge just because a platform might route to a skill at runtime based
on the skill description, file name, or surrounding task context. Runtime
selection behavior is outside the static graph contract and varies by
client.

The audit may suppress `unused_skill` when an agent or instruction mentions
the skill name in prose, because that often documents instruction-mediated
usage. That suppression reduces noise; it is not an edge and is not proof
that a runtime loaded the skill.

## Example graph

The repository ships an
[`examples/agent-graph-demo/`](../examples/agent-graph-demo/) workspace
that intentionally contains broken references, duplicate planner variants,
permission conflicts, overlapping review responsibilities, a hook without
safety notes, a vague skill description, and platform drift, so audits
return real findings.

```bash
cd examples/agent-graph-demo
wd agents discover
wd agents list
wd agents explain planner
wd agents audit
```

A trimmed `wd agents explain planner` output:

```text
planner
Type: agent
Status: canonical
Platforms:
  - Claude Code: .claude/agents/planner.md
  - Gemini CLI: .gemini/agents/planner.md
  - GitHub Copilot / VS Code: .github/agents/planner.agent.md
  - OpenCode: opencode.json#/agents/planner
Source files:
  - .github/agents/planner.agent.md
Outgoing references:
  - handoff_to -> agent:reviewer
  - provides_tool -> tool:editFiles
  - provides_tool -> tool:search
  - references_file -> file:docs/architecture/principles.md
  - uses_skill -> skill:architecture-decision
Incoming references:
  - generated_from -> agent:planner (Claude Code copy)
  - generated_from -> agent:planner (Gemini copy)
  - invokes_agent -> prompt:create-plan
```

The canonical planner here is the GitHub Copilot agent file. The Claude
Code and Gemini copies are marked `generated`, and the audit will warn if
their descriptions drift from the canonical source.

## Commands

All commands accept `--root <path>` to point at a different repository, and
all read or write commands accept `--json` for stable, agent-friendly
output.

| Command | What it does |
|---|---|
| `wd agents discover` | Scan known AI customization files and write `.weld/agent-graph.json`. Use `--no-write` to scan without persisting. Text mode prints a per-code diagnostic count breakdown after the summary; pass `--show-diagnostics` to dump every diagnostic inline (severity, code, path:line, message). Exits non-zero if any diagnostic has `severity=error`; warnings keep exit code 0. |
| `wd agents rediscover` | Refresh `.weld/agent-graph.json` from a new static scan. Same flags as `discover`. |
| `wd agents list` | List persisted assets. Filter with `--type <node-type>` or `--platform <platform>`. |
| `wd agents explain <asset>` | Show one asset's purpose, platforms, source file, and incoming and outgoing graph relationships. The query may be an asset name, a node ID, or a source path. |
| `wd agents impact <asset>` | Show what other assets are affected by changing one asset, including same-name platform variants and same-purpose variants, with a recommended change checklist. |
| `wd agents audit` | Run static consistency checks over the persisted graph. |
| `wd agents plan-change "<request>"` | Rank assets by relevance to a free-text request and emit a deterministic primary / secondary / validation plan. |
| `wd agents viz` | Open a local read-only browser explorer for the persisted Agent Graph. |

`wd agents discover` writes `.weld/agent-graph.json` by default, so other
`wd agents` commands operate on that file. If the file does not exist
they exit with a clear error pointing at `wd agents discover`.
`wd agents viz` follows the same rule: run `wd agents discover` first, then
start the browser explorer.

### Audit findings

`wd agents audit` returns a list of findings. Each has a stable `code`,
a `severity` (`info` or `warning`), and the nodes involved. The current
codes are:

- `broken_reference` — a Markdown link, `@file`, or path reference points
  at a file that does not exist.
- `duplicate_name` — multiple assets of the same type share a name (often
  expected when the same agent exists on several platforms; the next
  checks help decide whether that is intentional).
- `responsibility_overlap` — multiple assets share the same description.
- `path_scope_overlap` — multiple instructions apply to the same path
  scope.
- `permission_conflict` — same-name assets disagree about whether a tool
  is allowed or denied.
- `unsafe_hook` — a hook lacks any mention of risk, rollback, or safety
  in its description.
- `vague_description` — an agent, skill, or subagent description is
  shorter than three words or uses a generic placeholder.
- `platform_drift` — same-name variants on different platforms have
  different descriptions.
- `ambiguous_canonical` — multiple assets with the same name are all
  marked `authority: canonical`.
- `rendered_copy_drift` — a `generated_from` (or same-name canonical /
  derived) pair has different descriptions.
- `missing_render_target` — a canonical asset's `renders:` list points at
  a file that does not exist.
- `unused_skill` — a skill has no incoming explicit `uses_skill`
  references and is not mentioned by name in an agent or instruction body
  (`info`-level).
- `unreachable_subagent` — a subagent has no incoming
  `invokes_agent` / `handoff_to` references.
- `missing_agent` — a command references an agent that no platform
  defines.
- `missing_mcp_config` — an MCP server is referenced but is not
  configured anywhere in the graph.

## Authority and drift

Many teams keep the same agent on several platforms. Without authority
metadata it is unclear which file is the source of truth and which files
are derived copies. The Agent Graph supports two ways to declare authority:

**Per-file frontmatter.** Add a `weld:` block to an asset's YAML
frontmatter:

```yaml
---
name: planner
description: Produces implementation plans before edits.
weld:
  authority: true
  renders: [.claude/agents/planner.md, .gemini/agents/planner.md]
---
```

**Sidecar `agents.yaml`.** Centralize authority in `.weld/agents.yaml`:

```yaml
agents:
  planner:
    canonical: .github/agents/planner.agent.md
    renders:
      - .claude/agents/planner.md
      - .gemini/agents/planner.md
```

Either form sets `props.authority` to `canonical` on the canonical node
and `derived` on the rendered copies, and emits a `generated_from` edge
from each rendered copy back to its canonical source. Files that begin
with a `Generated by Weld` marker are tagged `generated` automatically.

The audit then watches for `rendered_copy_drift` and
`missing_render_target` so descriptions and target paths cannot quietly
diverge from the canonical source.

## Read-only-first policy

The Agent Graph is **read-only by default** for the assets it describes.
The `wd agents` commands fall into three groups:

- **Read-only:** `list`, `explain`, `impact`, `audit`, `plan-change`.
  These never write outside `.weld/`.
- **Graph-only writes:** `discover`, `rediscover`. These write only
  `.weld/agent-graph.json` (atomically). They never modify the
  customization files they discover.
- **Customization-file writes:** only `wd agents render` (preview)
  writes into discovered customization files, and only when the operator
  passes `--write`. It is dry-run by default, refuses to clobber an
  existing rendered file without `--force`, and stamps a provenance
  header on every file it produces. Bootstrap-style writers
  (`wd bootstrap`, `wd scaffold`) are separate commands, write only
  into the directories they document, and are not invoked by Agent Graph
  workflows.

The general convention used elsewhere in Weld -- dry-run / diff by
default, an explicit `--write` flag for changes, and a `--force` flag
required to overwrite existing files -- is the exact contract enforced
by `wd agents render`. See
[Render and export status](#render-and-export-status) for details.

## Render and export status

Weld models the `canonical -> rendered` relationship between an agent
and its per-platform copies, and `wd agents audit` reports drift between
them. The renderer that produces those copies is **preview** today; the
command, its flags, and its output may change before v1.0.

### Preview: `wd agents render`

For each `canonical -> rendered` pair declared in `.weld/agents.yaml`,
`wd agents render`:

- **Defaults to dry-run.** Without flags, the command prints a unified
  diff for every pair that would change (or is missing) and exits
  non-zero if any pair is not in sync. It never touches the filesystem.
- **Requires `--write` to apply.** With `--write`, it creates rendered
  files that do not yet exist and is a no-op for pairs that are already
  in sync. There is no env variable or interactive prompt that bypasses
  this flag.
- **Requires `--force` to clobber.** With `--write`, it refuses to
  overwrite an existing rendered file whose bytes differ from the
  renderer's output. `--write --force` is the only way to overwrite.
- **Stamps a provenance header.** Every rendered file begins with a
  comment that names the canonical source and the regenerate command.
  The marker is one of the strings recognised by
  `agent_graph_authority._GENERATED_MARKERS`, so subsequent
  `wd agents discover` runs annotate the rendered node with
  `generated: true` automatically.
- **Strips frontmatter.** The renderer drops a leading `---` ... `---`
  YAML frontmatter block from the canonical body before applying the
  provenance header.

The audit additionally emits a `rendered_copy_content_drift` finding when
a rendered copy's on-disk bytes differ from a fresh render. That check
complements the existing description-level `rendered_copy_drift` finding,
which only compares the `description:` strings.

### Why preview?

The renderer is intentionally minimal: it strips frontmatter and prepends
a header. There is no Jinja templating, no per-platform variable
substitution, and no path remapping. The platform support matrix records
render/export as **Experimental** for every platform that supports it
for the same reason. Do not depend on the exact output format yet.

There is no `wd agents export` command. The word *export* remains
reserved for the existing `wd export` graph visualization command.

## Limitations and known gaps

- **No runtime behavior.** The graph cannot tell you whether an agent
  actually ran, whether a hook fired, or whether a tool call succeeded.
  Pair the graph with whatever runtime telemetry your platform provides.
- **Pattern-driven discovery.** Only the file shapes listed in
  [Supported asset types and platforms](#supported-asset-types-and-platforms)
  are scanned. New formats need a discovery rule before they appear in
  the graph.
- **Heuristic text references.** `references_file`, `invokes_agent`,
  `uses_skill`, and similar edges come from regex-based extraction over
  Markdown and config text. Edges record their `provenance` so you can
  audit them, but they are best-effort.
- **Preview renderer only.** `wd agents render` is preview. It strips
  frontmatter and adds a provenance header; richer transforms are not
  supported. See [Render and export status](#render-and-export-status).
- **Per-platform runtime claims need their own validation.** Static
  discovery says nothing about whether a real client successfully
  consumed an asset. Use the
  [platform support matrix](platform-support.md) for runtime claims.
- **Single-repo today.** The Agent Graph is built per repository.
  Polyrepo federation for `wd agents` is not implemented.

## See also

- [`examples/agent-graph-demo/`](../examples/agent-graph-demo/) — a
  worked example with deliberate inconsistencies.
- [Platform support matrix](platform-support.md) — per-platform support
  levels for discovery, audit, render, MCP, and runtime validation.
- [Tutorial: Weld in 5 minutes](tutorial-5-minutes.md) — the
  code-graph counterpart to this document.
