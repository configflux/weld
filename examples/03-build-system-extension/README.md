# Example: Build System Extension

Demonstrates integrating weld into a build system so that knowledge
graph discovery runs automatically as part of your build pipeline. This
example uses a Makefile, but the same approach applies to any build
tool (Bazel, Just, Taskfile, shell scripts, CI pipelines, etc.).

## What This Example Contains

- `Makefile` -- build targets that run weld discovery and validate
  the graph as part of the build
- `service.py` -- a minimal Python module representing application code
- `.weld/discover.yaml` -- weld discovery configuration
- `README.md` -- this walkthrough

## The Idea

Instead of running `wd discover` manually, you embed it as a build
target. This ensures the connected structure stays up to date whenever you
build your project. The Makefile defines three targets:

| Target | Purpose |
|---|---|
| `make discover` | Run weld discovery and write `.weld/graph.json` |
| `make graph-check` | Verify the graph is fresh (fails if stale) |
| `make all` | Build the project and refresh the graph |

The `discover` target uses file-level dependencies: it only re-runs
weld when source files change, avoiding redundant work.

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

The `Makefile` declares `.weld/graph.json` as a target that depends
on your source files and the discovery configuration:

```makefile
SOURCES := $(wildcard *.py)
DISCOVER_CONFIG := .weld/discover.yaml

.weld/graph.json: $(SOURCES) $(DISCOVER_CONFIG)
	wd discover --output .weld/graph.json
```

When any `.py` file or the discovery config changes, Make re-runs
`wd discover`. When nothing has changed, Make skips it.

### Freshness Check

The `graph-check` target uses `wd stale` to verify the graph
reflects the current source files:

```makefile
graph-check: .weld/graph.json
	@wd stale && echo "Graph is fresh" || \
		(echo "ERROR: Graph is stale -- run 'make discover'" && exit 1)
```

This target is useful in CI pipelines to enforce that developers keep
the graph up to date.

### Full Build

The `all` target chains your normal build steps with discovery:

```makefile
all: build discover
```

This way the connected structure is always refreshed alongside your
application build.

## Adapting for Other Build Systems

### Bazel

Wrap weld in a `genrule` that produces the graph as a build output:

```python
genrule(
    name = "weld_discover",
    srcs = glob(["**/*.py"]) + [".weld/discover.yaml"],
    outs = ["graph.json"],
    cmd = "cd $$(dirname $(location .weld/discover.yaml))/.. && wd discover > $@",
)
```

### Just (justfile)

```just
discover:
    wd discover --output .weld/graph.json

build: discover
    python -m build
```

### CI Pipeline (GitHub Actions)

```yaml
- name: Refresh connected structure
  run: wd discover --output .weld/graph.json

- name: Check graph freshness
  run: wd stale
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

- Add more source entries to `.weld/discover.yaml` to discover
  additional file types
- Extend the `Makefile` with targets for `wd enrich` or
  `wd query` to integrate more weld features into your workflow
- Use `wd discover --format dot` (when available) to generate
  dependency diagrams as build artifacts
