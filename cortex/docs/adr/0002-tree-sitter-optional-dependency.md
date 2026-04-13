# 0002. Tree-sitter as Optional Native Dependency

Date: 2026-03-28
Status: Proposed

## Context

The cortex tool's portability goal requires extracting symbols (functions,
classes, types, imports) from source files in any programming language.
Today, only Python files get AST-level extraction (via the stdlib `ast`
module). TypeScript, Go, Rust, Java, and every other language are invisible
to the graph.

Writing a regex-based or AST-based extractor per language is unsustainable.
Tree-sitter provides a single C library with Python bindings that can parse
any language for which a grammar exists. As of 2026, tree-sitter grammars
cover 100+ languages including every mainstream one.

### Forces

1. **Build-system agnostic** -- cortex must work as a plain directory copy or
   `pip install`. It cannot require Bazel, Make, or native compilation at
   the user's site.
2. **Zero mandatory dependencies** -- the core cortex tool (graph engine,
   keyword index, bundled regex strategies) must work with Python stdlib
   only. Adding a hard dependency on tree-sitter would break adoption for
   users who cannot or will not install native extensions.
3. **Coverage vs complexity tradeoff** -- tree-sitter gives file-level
   symbol extraction for any language with one strategy. The alternative is
   N regex strategies that each cover a fraction of a language's syntax.
4. **Install friction** -- `pip install tree-sitter tree-sitter-python
   tree-sitter-typescript tree-sitter-go` is straightforward on any platform
   with a C compiler. Pre-built wheels exist for common platforms. But it is
   still a native dependency with potential build failures on constrained
   environments.
5. **Graceful degradation** -- agents and users must get useful output even
   without tree-sitter. A graph with path-based file nodes and keyword index
   is still valuable; symbol-level exports are an enrichment, not a
   prerequisite.

### Alternatives Considered

**A. Make tree-sitter a hard dependency.** Simplest code path, but violates
the zero-mandatory-dependency constraint and breaks the "plain copy" install
story.

**B. Ship regex-only strategies for each language.** No native dependency,
but regex extraction is fragile, incomplete, and requires maintenance per
language. A Python regex strategy can extract `def` and `class` lines
reasonably well; a Go or Rust regex strategy is significantly harder (e.g.,
distinguishing exported vs unexported symbols in Go requires understanding
capitalization of identifiers after `func`, `type`, `const`, `var`).

**C. Tree-sitter optional, regex fallback, graceful degradation.** Tree-
sitter is the preferred extraction path. When not installed, regex-based
strategies handle common patterns for high-value languages (Python,
TypeScript). For languages without a regex fallback, files are still indexed
as path-based nodes with keyword tokens from path segments -- no symbol
extraction, but still discoverable via `cortex find`.

**D. WASM-based tree-sitter.** Avoids native compilation by running tree-
sitter grammars via a WASM runtime. Adds a different native dependency
(wasmtime or similar). Not mature enough in the Python ecosystem as of 2026.

## Decision

We will adopt **option C: tree-sitter as an optional pip dependency with
graceful degradation**.

### Dependency structure

```
Historical runtime layout (stdlib only): tools/kg_graph.py, tools/kg_discover.py, bundled strategies
Optional (pip install):    tree-sitter, tree-sitter-{language} grammars
```

The `tree_sitter.py` strategy plugin guards its imports:

```python
try:
    import tree_sitter
    from tree_sitter_python import language as python_language
    # ... other language imports
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False


def extract(root, source, nodes, edges, context):
    if not TREE_SITTER_AVAILABLE:
        # Log a warning once, skip extraction
        context.setdefault("_warnings", []).append(
            "tree-sitter not installed; skipping tree_sitter strategy. "
            "Install with: pip install tree-sitter tree-sitter-python ..."
        )
        return []
    # ... normal extraction
```

### Per-language query patterns

Tree-sitter queries are bundled as YAML files in `cortex/languages/`:

```
cortex/languages/
  python.yaml       # exports, classes, imports queries
  typescript.yaml
  go.yaml
```

Each file contains tree-sitter S-expression queries for extracting exports,
types, and imports from that language. Adding a new language requires only a
new YAML file -- no Python code changes.

`discover.yaml` references languages by name:

```yaml
sources:
  - glob: "**/*.go"
    type: file
    strategy: tree_sitter
    language: go
```

### Regex fallback strategies

For Python and TypeScript, bundled regex-based strategies provide degraded
but useful extraction when tree-sitter is unavailable:

- **Python:** The existing `python_module` strategy uses `ast` (stdlib) --
  this is already better than regex and serves as the fallback.
- **TypeScript:** A new `typescript_regex` strategy using line-level regex
  to extract `export function`, `export class`, `export const`, `export
  interface` declarations. Imperfect but covers the common cases.

For other languages, no regex fallback is provided. Files are still indexed
by path segments and any string tokens extractable from filenames and
directory structure.

### Strategy selection guidance in discover.yaml

```yaml
# With tree-sitter installed (recommended):
- glob: "**/*.py"
  strategy: tree_sitter
  language: python

# Without tree-sitter (stdlib fallback):
- glob: "**/*.py"
  strategy: python_module
```

`cortex init` will detect whether tree-sitter is installed and generate the
appropriate strategy references in the starter `discover.yaml`.

### Relationship to the plugin architecture (ADR 0001)

`tree_sitter.py` is a standard strategy plugin. It follows the same
`extract()` interface as every other strategy. The optional dependency is
handled entirely within the plugin -- the orchestrator has no knowledge of
tree-sitter. This is the intended plugin isolation: a strategy's
dependencies are its own concern.

## Consequences

### What becomes easier

- Adding language support requires only a YAML query file in
  `cortex/languages/`, not a new Python strategy.
- A single code path handles symbol extraction for all tree-sitter-supported
  languages.
- Users on constrained environments (no C compiler, corporate lockdown) can
  still use cortex with reduced extraction quality.

### What becomes harder

- Two code paths for symbol extraction (tree-sitter vs regex/ast fallback)
  must be tested and maintained. Mitigation: the regex strategies are
  intentionally simple and stable; they do not try to match tree-sitter's
  completeness.
- Users must understand that `pip install tree-sitter` unlocks better
  extraction. Mitigation: `cortex discover` prints a clear message when
  tree-sitter strategies are configured but the package is missing.
- Tree-sitter grammar package versions must track upstream. Mitigation:
  pin versions in a `requirements-cortex.txt` or document compatible ranges.
- Testing requires both with-tree-sitter and without-tree-sitter
  configurations. Mitigation: CI runs both; the without-tree-sitter path
  is the simpler one and is the default in Bazel tests (no native deps).

### What the team commits to

- Tree-sitter is never a hard import outside of `tree_sitter.py`.
- Core cortex functionality (graph, find, stale, init) works without tree-sitter.
- The `python_module` strategy (stdlib `ast`) remains the Python fallback
  indefinitely -- it is not deprecated by tree-sitter.
- Language query files in `cortex/languages/` are maintained alongside the
  tree-sitter strategy.

## Related Issues

- bd-G106: Tree-sitter as optional native dependency
- bd-G100: Plugin strategy architecture (prerequisite -- tree-sitter is a plugin)
- bd-G101: Tree-sitter universal extraction strategy (implementation)
- bd-G102: Extend discover.yaml for full source coverage (consumer)
