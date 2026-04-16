# Example: Custom Strategy Plugin

Demonstrates writing and registering a project-local strategy plugin for
weld. The custom strategy extracts TODO and FIXME comments from Python
files and represents each one as a node in the connected structure.

## What This Example Contains

- `.weld/strategies/todo_comment.py` -- a custom strategy that scans
  Python files for TODO/FIXME comments and emits a graph node for each
- `sample.py` -- a sample Python file containing several TODO comments
  for the strategy to find
- `.weld/discover.yaml` -- weld discovery configuration that
  registers the custom strategy

## How Custom Strategies Work

Weld resolves strategies by name from `discover.yaml`. Project-local
strategies placed in `.weld/strategies/` take priority over the
built-in strategies shipped with weld.

A strategy is a Python module that exports an `extract()` function with
this signature:

```python
def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    ...
```

- `root` -- absolute path to the repository (or project) root
- `source` -- the source entry from `discover.yaml`, including any
  custom keys you define
- `context` -- a shared dict passed across all strategies in a single
  discovery run

The function returns a `StrategyResult(nodes, edges, discovered_from)`
named tuple.

## Running Discovery

```bash
cd examples/02-custom-strategy
wd discover
```

## What the Graph Contains

The output graph includes a `concept` node for each TODO/FIXME comment
found in the sample file:

```json
{
  "nodes": {
    "todo:sample_py:10": {
      "type": "concept",
      "label": "TODO: Add input validation for edge cases",
      "props": {
        "file": "sample.py",
        "line": 10,
        "kind": "TODO",
        "source_strategy": "todo_comment"
      }
    }
  }
}
```

## Registering the Strategy

In `.weld/discover.yaml`, the `strategy` field matches the filename
(without `.py`) in `.weld/strategies/`:

```yaml
sources:
  - strategy: todo_comment
    glob: "*.py"
```

Any extra keys in the source entry (like `glob` and `type`) are
available to the strategy via the `source` dict parameter.
