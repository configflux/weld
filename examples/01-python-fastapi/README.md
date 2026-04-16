# Example: Python FastAPI Discovery

Demonstrates weld discovering a minimal FastAPI project. The built-in
`python_module` strategy extracts modules and their structure from Python
source files.

## What This Example Contains

- `app.py` -- a simple FastAPI application with three routes
  (GET /health, GET /items, POST /items)
- `models.py` -- Pydantic models used by the routes
- `.weld/discover.yaml` -- weld discovery configuration pointing to
  the sample files

## Running Discovery

```bash
cd examples/01-python-fastapi
wd discover
```

## What the Graph Contains

After running discovery, the output JSON graph includes:

- **Module nodes** for `app` and `models`, with their top-level symbols
  (functions, classes, imports)
- **File metadata** showing the source path and strategy that produced
  each node
- **Edges** connecting modules to their dependencies (e.g., `app`
  imports from `models`)

Example output (abbreviated):

```json
{
  "nodes": {
    "mod:app": {
      "type": "module",
      "label": "app",
      "props": {
        "file": "app.py",
        "source_strategy": "python_module"
      }
    },
    "mod:models": {
      "type": "module",
      "label": "models",
      "props": {
        "file": "models.py",
        "source_strategy": "python_module"
      }
    }
  },
  "edges": [...],
  "discovered_from": ["app.py", "models.py"]
}
```

## Customizing

Edit `.weld/discover.yaml` to add more source entries or try different
strategies. See the [strategy cookbook](../../weld/docs/strategy-cookbook.md)
for the full list of built-in strategies.
