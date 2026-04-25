# Weld Toolkit Examples

Self-contained examples demonstrating configflux-weld usage patterns.

## Examples

### [01-python-fastapi](./01-python-fastapi/)

Discover a minimal FastAPI project. Shows how weld extracts routes,
Pydantic models, and module structure from Python source files using the
built-in `python_module` strategy.

### [02-custom-strategy](./02-custom-strategy/)

Write and register a custom strategy plugin. Demonstrates the
project-local strategy pattern by extracting TODO/FIXME comments from
Python files as graph nodes.

### [03-build-system-extension](./03-build-system-extension/)

Integrate weld into a build system. Shows a Makefile that runs
`wd discover` as a build target with file-level dependencies so
the graph only refreshes when sources change. Includes adaptation
notes for Bazel, Just, and CI pipelines.

### [04-monorepo-typescript](./04-monorepo-typescript/)

Discover a TypeScript monorepo with workspace packages, shared libs,
a backend service, container runtime, CI workflows, and an ADR-backed
architecture doc. Shows how weld extracts exported symbols with
per-package scoping, cross-package dependency edges (client and
service both importing `@acme/shared-types`), and folds Docker/CI/docs
into the same connected structure. Target of the 5-minute demo.

### [05-polyrepo](./05-polyrepo/)

A federated polyrepo workspace with three in-tree children
(`services/api`, `services/auth`, `libs/shared-models`) stitched
together by `.weld/workspaces.yaml`. The `services-api` child calls
`services-auth` over HTTP, so the `service_graph` resolver emits a
visible `cross_repo:calls` edge in the root graph. Target of the
polyrepo half of the 5-minute demo.

### [06-infrastructure-as-code](./06-infrastructure-as-code/)

Discover infrastructure-as-code artifacts across a project. Shows how
weld maps Dockerfiles, Docker Compose services, GitHub Actions
workflows, and Terraform configurations into a unified connected structure
using the `dockerfile`, `compose`, `gh_workflow`, `yaml_meta`, and
`boundary_entrypoint` strategies together.

### [agent-graph-demo](./agent-graph-demo/)

Inspect a mixed AI customization workspace. Shows `wd agents discover`,
`list`, `audit`, `explain`, `impact`, and `plan-change` across agents,
skills, prompts, hooks, instructions, MCP config, and platform variants.

Demo flow:

```bash
cd examples/agent-graph-demo
wd agents discover
wd agents list
wd agents audit
wd agents explain planner
wd agents impact .github/agents/planner.agent.md
wd agents plan-change "planner should always include test strategy"
```

## Prerequisites

Install configflux-weld (from the repository root or via pip):

```bash
pip install -e .
```

## Running an Example

Each example directory contains its own `.weld/discover.yaml`
configuration. To run discovery against an example:

```bash
cd examples/01-python-fastapi
wd discover
```

The output is a JSON connected structure printed to stdout. Use `--output` to
write it atomically to a file (recommended) or pipe stdout for quick
inspection:

```bash
wd discover --output graph.json
python -m json.tool graph.json
```
