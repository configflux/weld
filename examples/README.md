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
