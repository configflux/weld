---
name: orchestrator
description: Demo worker-shaped orchestrator. Dispatches to implementer / reviewer / qa specialists per task class. Modeled on the real worker.md so that slice-1 q8rl regex (literal subagent_type) and slice-2 hw6j frontmatter (placeholder declaration) both have non-Claude-only fixture coverage.
model: opus
weld:
  invokes_agents:
    - tdd
    - migration
    - build-fixer
    - reviewer
    - qa
    - architect
    - security
handoffs: [reviewer]
tools: [editFiles, search]
---

# Orchestrator

This agent is the demo's worker-shaped orchestrator. Every literal
`subagent_type:` reference below is a `slice-1` empirical pattern (the
q8rl regex). The single `<implementer_type>` placeholder is the
`slice-2` empirical pattern (frontmatter declaration via `weld:
invokes_agents:` resolves it without polluting the inferred-edge regex).

The boundary vs. existing demo agents:

- `agent:planner` produces the plan; the orchestrator consumes it.
- `agent:reviewer` is a leaf reviewer the orchestrator dispatches to.
- The orchestrator itself never edits source -- it routes to specialists.

## Phase 1 -- Architecture review (literal references)

```
Agent(
  name: "architect",
  subagent_type: "architect",
  prompt: "Write an ADR if the task class is ARCHITECTURE."
)
```

```
Agent(
  name: "qa",
  subagent_type: "qa",
  prompt: "Verify acceptance criteria once the implementer reports done."
)
```

## Phase 2 -- Implementer dispatch (placeholder indirection)

The implementer is selected by `task_class`. The literal name is *not*
known at graph-discovery time -- declaring the candidate set in
frontmatter (`weld: invokes_agents`) is the only authoritative way to
surface those edges.

```
Agent(
  name: "dev",
  subagent_type: "<implementer_type>",
  prompt: "Implement bd-<id>. Run the gate before returning."
)
```

## Phase 3 -- Specialist review chain (literal references)

```
Agent(
  name: "reviewer",
  subagent_type: "reviewer",
  prompt: "4-eye review of the dev diff. Findings only -- no edits."
)
```

```
Agent(
  name: "build-fixer",
  subagent_type: "build-fixer",
  prompt: "Fall through to the build-fixer if Bazel breaks."
)
```

```
Agent(
  name: "security",
  subagent_type: "security",
  prompt: "Run security screen if required."
)
```

## Phase 4 -- Hand-off

The orchestrator delegates final acceptance to the reviewer chain. See
@docs/architecture/principles.md for the underlying authority rule and
skill:architecture-decision when an ADR is required. Use mcp:filesystem
for any file-context lookups.
