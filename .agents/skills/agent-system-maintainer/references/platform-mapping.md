# Platform Mapping

Weld Agent Graph normalizes AI customization files into a canonical graph without making every platform identical.

| Platform | Common Inputs |
|---|---|
| GitHub Copilot / VS Code | `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md`, `.github/prompts/*`, `.github/agents/*`, `.github/skills/*/SKILL.md` |
| Claude Code | `CLAUDE.md`, `.claude/agents/*`, `.claude/skills/*/SKILL.md`, `.claude/settings.json` |
| Codex | `AGENTS.md`, `AGENTS.override.md`, generic `SKILL.md` files |
| OpenCode | `AGENTS.md`, `opencode.json`, configured agents, commands, instructions, MCP servers |
| Cursor | `.cursor/rules/*`, `AGENTS.md`, skills, hooks, subagents |
| Gemini CLI | `GEMINI.md`, `.gemini/agents/*`, custom commands, skills |
| Generic | `AGENTS.md`, `SKILL.md`, Markdown references |

## Canonical Status

- `canonical`: authoritative source for a customization.
- `derived`: generated or rendered copy from a canonical source.
- `platform-specific`: intentionally maintained for one platform.
- `manual`: discovered file with no explicit authority metadata.

When platform variants share a name or purpose, use `wd agents explain` and `wd agents impact` before editing one copy.
