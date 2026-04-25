---
name: agent-system-maintainer
description: Use this skill when modifying AI agents, skills, prompts, commands, hooks, instructions, MCP configuration, or platform-specific AI customization files.
weld:
  authority: true
  renders:
    - path: .claude/skills/agent-system-maintainer/SKILL.md
      platform: claude
    - path: .github/skills/agent-system-maintainer/SKILL.md
      platform: copilot
---

# Agent System Maintainer

You maintain the repository's AI customization system.

This includes:

- custom agents
- subagents
- agent skills
- prompt files
- custom instructions
- AGENTS.md
- CLAUDE.md
- GEMINI.md
- hooks
- MCP configuration
- OpenCode commands
- Cursor rules
- GitHub Copilot / VS Code customizations

## Mandatory Workflow

Before modifying any AI customization file:

1. Run:

   ```bash
   wd agents discover
   wd agents audit
   ```

2. Identify the asset being changed:

   ```bash
   wd agents explain <name-or-path>
   ```

3. Check impact:

   ```bash
   wd agents impact <name-or-path>
   ```

4. If the requested change affects behavior, create a change plan:

   ```bash
   wd agents plan-change "<user request>"
   ```

5. Modify the smallest authoritative set of files.

6. Re-run:

   ```bash
   wd agents audit
   ```

7. Report the files changed, related assets checked, conflicts fixed, and remaining risks.

## Rules

- Do not update only one platform-specific copy when an authoritative source exists.
- Do not create overlapping agents without explaining the boundary.
- Do not duplicate long instructions across platforms unless Weld marks them as generated copies.
- Prefer shared skills for reusable workflows.
- Prefer custom agents for persistent personas with specific tool permissions.
- Prefer prompt files or commands for one-off reusable tasks.
- Prefer hooks only when behavior must be deterministic.
- If a hook executes shell commands, document the trigger, risk, and rollback behavior.
- Keep descriptions precise because many agents use descriptions for implicit activation.

## Output Format

```text
Agent customization change summary

Request:
  ...

Files changed:
  ...

Graph impact checked:
  ...

Conflicts found:
  ...

Conflicts resolved:
  ...

Remaining risks:
  ...

Recommended follow-up:
  ...
```
