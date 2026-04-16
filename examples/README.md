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

Discover a TypeScript monorepo with workspace packages. Shows how
weld extracts exported symbols from multiple packages using the
`typescript_exports` strategy with per-package scoping, and how
cross-package dependency edges appear in the connected structure.

### [05-infrastructure-as-code](./05-infrastructure-as-code/)

Discover infrastructure-as-code artifacts across a project. Shows how
weld maps Dockerfiles, Docker Compose services, GitHub Actions
workflows, and Terraform configurations into a unified connected structure
using the `dockerfile`, `compose`, `gh_workflow`, `yaml_meta`, and
`boundary_entrypoint` strategies together.

### [05-polyrepo](./05-polyrepo/)

Set up a federated polyrepo workspace. Walks through initializing child
repos, scaffolding `workspaces.yaml` at the workspace root, running
federated discovery with cross-repo resolvers, and checking workspace
status with `wd workspace status`.

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

The output is a JSON connected structure printed to stdout. Pipe it to a file
to inspect the structure:

```bash
wd discover > graph.json
python -m json.tool graph.json
```
