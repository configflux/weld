# 0001. Plugin Strategy Architecture

Date: 2026-03-28
Status: Proposed

## Context

Historical note: at the time this ADR was written, the discovery runtime lived
in `tools/kg_discover.py`, which was 1154 lines and contained 13 strategy functions, a YAML
parser, shared AST helpers, and the discovery orchestrator -- all in one file.
The file already exceeds the project's 800-line hard limit. Every new strategy
(tree-sitter, Django, Rails) would make this worse.

More importantly, the strategies are a closed set compiled into a single
module. A user adopting cortex for a new project cannot add extraction logic
without forking the tool. The plan to make cortex portable and shippable to
arbitrary codebases requires an open, plugin-based strategy architecture.

The current strategy interface is:

```python
def strategy_*(
    root: Path, source: dict, nodes: dict, edges: list, context: dict,
) -> list[str]
```

Strategies mutate `nodes` and `edges` in place and use `context` for
cross-strategy state (e.g., `table_to_entity` from sqlalchemy is consumed
during FK edge resolution in the orchestrator's post-processing step).

### Forces

1. **Portability** -- strategies must be loadable without editing cortex source.
2. **Zero mandatory dependencies** -- bundled strategies must work with
   stdlib only (no pip install required for core operation).
3. **Build-system agnostic** -- cortex must not assume Bazel, Make, or any
   particular build system. Plugin loading must work from a plain checkout.
4. **Cross-strategy coupling** -- the sqlalchemy strategy currently writes
   `pending_fk_edges` to `context`, which the orchestrator resolves after
   all strategies run. This is the only known cross-strategy dependency.
5. **Security** -- loading arbitrary Python from `.cortex/strategies/` is
   intentional user opt-in, equivalent to running any project script. No
   additional sandboxing is needed at this scope.
6. **File size** -- the 800-line gate requires splitting the file regardless
   of whether we adopt a plugin architecture.

### Alternatives Considered

**A. Simple file split without plugin loading.** Move strategies into
`cortex/strategies/*.py` but hardcode imports in the discovery runtime. This solves
the file size problem but not portability. Rejected because portability is a
primary goal.

**B. Entry-point based plugins (setuptools/pkg_resources).** Standard Python
plugin mechanism. Requires packaging and installation. Rejected because cortex
must work as a plain directory copy or git clone with no install step.

**C. importlib + sys.path manipulation.** Load strategy modules by appending
`cortex/strategies/` and `.cortex/strategies/` to `sys.path`, then
`importlib.import_module(name)`. Simple, no install step, works from any
checkout.

**D. Direct `importlib.util.spec_from_file_location`.** Load strategy
modules by absolute file path without touching `sys.path`. More explicit,
avoids polluting the module namespace, and eliminates the risk of name
collisions between bundled and project-local strategies.

## Decision

We will adopt **option D: direct file-path loading via
`importlib.util.spec_from_file_location`**.

### Directory structure

```
cortex/
  _yaml.py              # Minimal YAML parser (shared by orchestrator and strategies)
  strategies/           # Bundled strategies (ship with tool)
    __init__.py         # Empty; not used for imports
    _helpers.py         # Shared AST helpers and StrategyResult type
    sqlalchemy.py       # Framework: SQLAlchemy entities
    fastapi.py          # Framework: FastAPI routes
    pydantic.py         # Framework: Pydantic contracts
    worker_stage.py     # Framework: worker pipeline stages
    dockerfile.py       # Infrastructure: Dockerfile base images
    compose.py          # Infrastructure: Docker Compose services
    frontmatter_md.py   # Docs: markdown with YAML frontmatter (agents)
    firstline_md.py     # Docs: markdown first content line (commands)
    tool_script.py      # Docs: script shebang detection
    yaml_meta.py        # Docs: YAML name + triggers (CI workflows)
    markdown.py         # Docs: generic markdown doc nodes
    config_file.py      # Docs: static config file nodes
    python_module.py    # Code: top-level classes/functions

.cortex/
  strategies/           # Project-local custom strategies (optional, user-created)
    django_model.py     # Example: user-provided
```

Each strategy is a separate file (no ``_builtin.py`` bundle). Every
strategy file exposes a single ``extract()`` function.

### Strategy interface contract

Every strategy module must expose a single callable:

```python
def extract(
    root: Path,
    source: dict,
    context: dict,
) -> StrategyResult:
    """Extract nodes and edges from files matching the source config.

    Args:
        root: Project root directory.
        source: The source entry from discover.yaml (glob, type, strategy, etc.).
        context: Mutable shared state dict. Used for cross-strategy communication.

    Returns:
        StrategyResult(nodes, edges, discovered_from) where nodes is a dict
        of node_id -> {type, label, props}, edges is a list of
        {from, to, type, props}, and discovered_from is a list of source
        paths (relative to root) for provenance tracking.
    """
```

This is identical to the current signature with a standardized name
(`extract` instead of `strategy_*`).

### Plugin resolution order

When `discover.yaml` specifies `strategy: foo`, the orchestrator resolves it
by checking, in order:

1. `.cortex/strategies/foo.py` (project-local override -- checked first so
   projects can shadow bundled strategies)
2. `cortex/strategies/foo.py` (bundled with tool)

The first match wins. If neither exists, the source entry is skipped with a
warning to stderr.

### Loading mechanism

```python
spec = importlib.util.spec_from_file_location(f"kg_strategy_{name}", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
fn = getattr(mod, "extract")
```

No `sys.path` mutation. Each strategy is loaded as an isolated module. Name
collisions between bundled and project-local strategies are resolved by
search order, not by Python's import system.

### Cross-strategy dependencies

The only known cross-strategy dependency is `sqlalchemy` writing
`table_to_entity` and `pending_fk_edges` to `context`, which the
orchestrator resolves after all strategies complete. This pattern is
preserved: the orchestrator owns post-processing, and strategies communicate
only through `context`. No strategy may import another strategy.

If future strategies need cross-strategy post-processing, the pattern is:
write to `context` during extraction, resolve in the orchestrator after all
strategies run. The orchestrator is the only code that reads `context` for
graph fixups.

### Shared helpers

AST helpers (`_base_names`, `_inherits`, `_tablename`, `_extract_fks`, etc.)
that are used by multiple strategies move to `cortex/strategies/_helpers.py`.
Strategies import from this module. At the time of writing, the YAML parser
stayed in the discovery runtime.
(the orchestrator) since strategies do not need it.

## Consequences

### What becomes easier

- Adding a new extraction strategy requires only dropping a `.py` file in
  the right directory -- no changes to cortex source.
- The discovery runtime shrinks from 1154 lines to ~300 (orchestrator + YAML
  parser + post-processing), well within the 800-line gate.
- Each strategy file is independently testable.
- Projects can override bundled strategies by shadowing the name in
  `.cortex/strategies/`.

### What becomes harder

- Strategy authors must follow the interface contract. There is no compile-
  time enforcement; a missing or mistyped `extract` function fails at
  runtime. Mitigation: clear error messages on load failure.
- Debugging a strategy requires knowing which file it was loaded from.
  Mitigation: log the resolved path when running with `--verbose`.
- Shared helpers create a coupling point. If `_helpers.py` changes signature,
  bundled strategies must update. Mitigation: keep the helper surface area
  minimal and stable.

### What the team commits to

- Every new strategy is a separate file with an `extract` function.
- The orchestrator never contains extraction logic -- only loading,
  dispatching, and post-processing.
- Cross-strategy communication goes through `context`, never through direct
  imports between strategies.

## Related Issues

- bd-G100: Refactor strategies into plugin files
- bd-G106: Tree-sitter optional dependency (first consumer of the plugin arch)
