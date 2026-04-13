# Example: Build System Extension

Demonstrates integrating cortex into a build system so that knowledge
graph discovery runs automatically as part of your build pipeline. This
example uses a Makefile, but the same approach applies to any build
tool (Bazel, Just, Taskfile, shell scripts, CI pipelines, etc.).

## What This Example Contains

- `Makefile` -- build targets that run cortex discovery and validate
  the graph as part of the build
- `service.py` -- a minimal Python module representing application code
- `.cortex/discover.yaml` -- cortex discovery configuration
- `README.md` -- this walkthrough

## The Idea

Instead of running `cortex discover` manually, you embed it as a build
target. This ensures the knowledge graph stays up to date whenever you
build your project. The Makefile defines three targets:

| Target | Purpose |
|---|---|
| `make discover` | Run cortex discovery and write `.cortex/graph.json` |
| `make graph-check` | Verify the graph is fresh (fails if stale) |
| `make all` | Build the project and refresh the graph |

The `discover` target uses file-level dependencies: it only re-runs
cortex when source files change, avoiding redundant work.

## Running the Example

```bash
cd examples/03-build-system-extension

# Run the full build (includes discovery)
make all

# Run discovery only
make discover

# Check graph freshness
make graph-check
```

## How It Works

### Makefile Integration

The `Makefile` declares `.cortex/graph.json` as a target that depends
on your source files and the discovery configuration:

```makefile
SOURCES := $(wildcard *.py)
DISCOVER_CONFIG := .cortex/discover.yaml

.cortex/graph.json: $(SOURCES) $(DISCOVER_CONFIG)
	cortex discover > .cortex/graph.json
```

When any `.py` file or the discovery config changes, Make re-runs
`cortex discover`. When nothing has changed, Make skips it.

### Freshness Check

The `graph-check` target uses `cortex stale` to verify the graph
reflects the current source files:

```makefile
graph-check: .cortex/graph.json
	@cortex stale && echo "Graph is fresh" || \
		(echo "ERROR: Graph is stale -- run 'make discover'" && exit 1)
```

This target is useful in CI pipelines to enforce that developers keep
the graph up to date.

### Full Build

The `all` target chains your normal build steps with discovery:

```makefile
all: build discover
```

This way the knowledge graph is always refreshed alongside your
application build.

## Adapting for Other Build Systems

### Bazel

Wrap cortex in a `genrule` that produces the graph as a build output:

```python
genrule(
    name = "cortex_discover",
    srcs = glob(["**/*.py"]) + [".cortex/discover.yaml"],
    outs = ["graph.json"],
    cmd = "cd $$(dirname $(location .cortex/discover.yaml))/.. && cortex discover > $@",
)
```

### Just (justfile)

```just
discover:
    cortex discover > .cortex/graph.json

build: discover
    python -m build
```

### CI Pipeline (GitHub Actions)

```yaml
- name: Refresh knowledge graph
  run: cortex discover > .cortex/graph.json

- name: Check graph freshness
  run: cortex stale
```

## What the Graph Contains

After running `make discover`, the output graph includes module nodes
extracted from the Python source files:

```json
{
  "nodes": {
    "mod:service": {
      "type": "module",
      "label": "service",
      "props": {
        "file": "service.py",
        "source_strategy": "python_module"
      }
    }
  },
  "edges": [],
  "discovered_from": ["service.py"]
}
```

## Customizing

- Add more source entries to `.cortex/discover.yaml` to discover
  additional file types
- Extend the `Makefile` with targets for `cortex enrich` or
  `cortex query` to integrate more cortex features into your workflow
- Use `cortex discover --format dot` (when available) to generate
  dependency diagrams as build artifacts
