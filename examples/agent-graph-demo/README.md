# Agent Graph Demo

This workspace demonstrates the static Agent Graph workflow across Copilot,
Claude Code, Cursor, Gemini, OpenCode, and generic instruction files.

Run from this directory:

```bash
wd agents discover
wd agents list
wd agents audit
wd agents explain planner
wd agents impact .github/agents/planner.agent.md
wd agents plan-change "planner should always include test strategy"
```

The workspace intentionally contains broken references, duplicate planner
variants, permission conflicts, overlapping review responsibilities, a hook
without safety notes, a vague skill description, and platform drift.
