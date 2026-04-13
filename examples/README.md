# Cortex Toolkit Examples

Self-contained examples demonstrating configflux-cortex usage patterns.

## Examples

### [01-python-fastapi](./01-python-fastapi/)

Discover a minimal FastAPI project. Shows how cortex extracts routes,
Pydantic models, and module structure from Python source files using the
built-in `python_module` strategy.

### [02-custom-strategy](./02-custom-strategy/)

Write and register a custom strategy plugin. Demonstrates the
project-local strategy pattern by extracting TODO/FIXME comments from
Python files as graph nodes.

### [03-build-system-extension](./03-build-system-extension/)

Integrate cortex into a build system. Shows a Makefile that runs
`cortex discover` as a build target with file-level dependencies so
the graph only refreshes when sources change. Includes adaptation
notes for Bazel, Just, and CI pipelines.

### [04-monorepo-typescript](./04-monorepo-typescript/)

Discover a TypeScript monorepo with workspace packages. Shows how
cortex extracts exported symbols from multiple packages using the
`typescript_exports` strategy with per-package scoping, and how
cross-package dependency edges appear in the knowledge graph.

### [05-infrastructure-as-code](./05-infrastructure-as-code/)

Discover infrastructure-as-code artifacts across a project. Shows how
cortex maps Dockerfiles, Docker Compose services, GitHub Actions
workflows, and Terraform configurations into a unified knowledge graph
using the `dockerfile`, `compose`, `gh_workflow`, `yaml_meta`, and
`boundary_entrypoint` strategies together.

## Prerequisites

Install configflux-cortex (from the repository root or via pip):

```bash
pip install -e .
```

## Running an Example

Each example directory contains its own `.cortex/discover.yaml`
configuration. To run discovery against an example:

```bash
cd examples/01-python-fastapi
cortex discover
```

The output is a JSON knowledge graph printed to stdout. Pipe it to a file
to inspect the structure:

```bash
cortex discover > graph.json
python -m json.tool graph.json
```
