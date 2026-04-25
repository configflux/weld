# Platform Support Matrix

Weld discovers common AI customization formats and exposes them through a
local graph. Runtime behavior is validated per client; static discovery support
does not imply that a client has executed the generated assets.

Support levels:

- **Supported**: fixture-tested, documented, and expected to work for the
  stated surface.
- **Partial**: some surfaces work, but coverage is incomplete.
- **Experimental**: early support; behavior may change.
- **Planned**: intended but not implemented.
- **Not supported**: not in scope for the stated surface.

For capability columns, **Not supported** also covers surfaces that do not
apply to that platform or format.

| Platform / format | Discovery | Audit | Impact | Plan-change | Render/export | Bootstrap | MCP config | Runtime validation | Public status | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| Generic `AGENTS.md` | Supported | Supported | Supported | Supported | Not supported | Not supported | Not supported | Fixture-tested static discovery; no live client required. | Supported | Static instruction asset used by Codex and other agents. |
| Generic `SKILL.md` / Agent Skills | Supported | Supported | Supported | Supported | Experimental | Partial | Not supported | Fixture-tested static discovery; generated copies need client validation. | Supported | Portable skill inventory and relationships first; rendering remains experimental. |
| GitHub Copilot repository instructions | Supported | Partial | Partial | Supported | Experimental | Supported | Not supported | Fixture-tested; real Copilot behavior still needs validation. | Partial | Covers `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md`. |
| GitHub Copilot / VS Code custom agents | Partial | Partial | Partial | Supported | Experimental | Partial | Not supported | Fixture-tested only. | Partial | Covers `.github/agents/*.agent.md`; validate runtime behavior in VS Code/GitHub Copilot. |
| VS Code MCP | Not supported | Not supported | Not supported | Not supported | Not supported | Partial | Supported | Config shape is generated; real VS Code runtime validation pending. | Partial | `wd mcp config --client=vscode` prints `.vscode/mcp.json`. |
| Claude Code skills/subagents | Partial | Partial | Partial | Supported | Experimental | Supported | Supported | Generated files and MCP snippets are static-tested; real client validation pending. | Partial | `wd bootstrap claude` and `.mcp.json` flow exist. |
| Codex `AGENTS.md` | Supported | Supported | Supported | Supported | Not supported | Supported | Supported | Static discovery and bootstrap are tested; real Codex runtime validation pending. | Partial | `wd bootstrap codex` writes `.codex/config.toml` and a Weld skill. |
| Codex skills | Partial | Partial | Partial | Supported | Experimental | Supported | Supported | Static discovery and generated files are tested; real client validation pending. | Partial | Validate `.codex/skills/*/SKILL.md` behavior with Codex before claiming full support. |
| Cursor rules | Partial | Partial | Partial | Supported | Not supported | Not supported | Supported | Static discovery and config generation are tested; real Cursor runtime validation pending. | Partial | Covers `.cursor/rules`; `wd mcp config --client=cursor` prints `.cursor/mcp.json`. |
| Cursor skills/subagents/hooks | Planned | Planned | Planned | Supported | Not supported | Not supported | Supported | Not runtime-tested. | Experimental | Do not claim full Cursor support yet. |
| OpenCode `AGENTS.md` | Supported | Supported | Supported | Supported | Not supported | Not supported | Not supported | Static discovery only. | Partial | OpenCode documents `AGENTS.md`-style rules; Weld treats the file as generic instructions. |
| OpenCode agents/commands/config | Partial | Partial | Partial | Supported | Not supported | Not supported | Not supported | Static `opencode.json` discovery only; command/agent fixtures pending. | Experimental | Add parser fixtures before stronger claims. |
| Gemini `GEMINI.md` | Partial | Partial | Partial | Supported | Not supported | Not supported | Not supported | Static discovery only. | Experimental | `GEMINI.md` is discovered; broader Gemini command/subagent coverage is not complete. |
| Gemini custom commands/subagents | Planned | Planned | Planned | Planned | Not supported | Not supported | Not supported | Not runtime-tested. | Planned | Needs fixtures and client validation. |
| Generic MCP clients | Not supported | Not supported | Not supported | Not supported | Not supported | Partial | Supported | Stdio server and generated snippets are tested; each client still needs validation. | Partial | Claim stdio MCP server support and snippets, not universal client compatibility. |

## Runtime Validation Records

Runtime validation means a real client was installed, configured with Weld, and
observed invoking or consuming the Weld integration successfully. Per-platform
records, the record format, and the per-row checklist of which Supported rows
still need a live-client record are tracked in
[`docs/runtime-validation.md`](runtime-validation.md).

Rules of thumb tied to that page:

- Every row whose **Public status** is **Supported** needs at least one
  runtime validation record on
  [`docs/runtime-validation.md`](runtime-validation.md) before broad launch,
  unless the row is a static instruction asset (e.g. generic `AGENTS.md`)
  whose fixture coverage is itself sufficient evidence.
- Rows whose **Public status** is **Partial**, **Experimental**, or
  **Planned** explicitly rely on fixture tests only. They do not require a
  runtime validation record before launch, but they need one before being
  upgraded to **Supported**.

No live-client runtime validation records are published yet. Public claims
should therefore use the matrix above and avoid unqualified "supports all major
platforms" wording.

## References

- GitHub Copilot custom instructions: <https://docs.github.com/copilot/customizing-copilot/adding-custom-instructions-for-github-copilot>
- GitHub custom instruction support matrix: <https://docs.github.com/en/copilot/reference/custom-instructions-support>
- VS Code custom agents: <https://code.visualstudio.com/docs/copilot/customization/custom-agents>
- Claude Code skills: <https://code.claude.com/docs/en/skills>
- OpenAI Codex `AGENTS.md`: <https://developers.openai.com/codex/guides/agents-md>
- OpenAI Codex Agent Skills: <https://developers.openai.com/codex/skills>
- OpenAI Codex MCP: <https://developers.openai.com/codex/mcp>
- OpenCode rules: <https://opencode.ai/docs/rules/>
- OpenCode commands: <https://opencode.ai/docs/commands/>
- OpenCode agents: <https://opencode.ai/docs/agents/>
- Cursor rules: <https://cursor.com/docs/rules>
- Cursor skills: <https://cursor.com/docs/skills>
- Cursor subagents: <https://cursor.com/docs/subagents>
- Gemini CLI custom commands: <https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/custom-commands.md>
- Gemini CLI subagents: <https://github.com/google-gemini/gemini-cli/blob/main/docs/core/subagents.md>
- Model Context Protocol registry: <https://registry.modelcontextprotocol.io/>
