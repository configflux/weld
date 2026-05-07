# Extending discovery: adding a strategy

This guide is for users who want weld to understand a framework or
language it does not support out of the box. It describes the
discovery layer, the contract a strategy plugin must satisfy, and
walks through a complete worked example end-to-end. After reading
this you should be able to drop a Python file in
`.weld/strategies/` and have `wd impact`, `wd query`, and the
capability matrix all light up for your new framework.

The higher-level design rationale lives in ADR 0043 (blast-radius
extensions, parent), ADR 0044 (Bazel `srcs`/`deps` edges), ADR 0045
(Dockerfile / compose edges), ADR 0046 (multi-language test peers),
and ADR 0047 (deterministic fixture suite). This document is the
practical "how" companion to those decisions.

---

## 1. Architecture in two paragraphs

Discovery is config-driven. Each entry in `.weld/discover.yaml`
maps a glob (or explicit file list) to a *node type* and a
*strategy name*. The strategy is a Python module under either
`weld/strategies/<name>.py` (bundled) or `<repo>/.weld/strategies/<name>.py`
(project-local). Project-local files of the same name shadow the
bundled implementation; the loader at
`weld/_discover_strategies.py` resolves names in that order, prints
a stable warning when shadowing is in effect, and refuses
project-local code entirely under `--safe` mode (ADR 0024).

Each strategy returns a `StrategyResult(nodes, edges, discovered_from)`
which the discovery driver merges, dedupes, and runs through
`weld/serializer.py` for canonical, byte-deterministic output. The
runtime capability matrix in `weld/capabilities.py` reads the
registry at `weld/_capabilities_registry.py` and the actual graph
contents to compute `wd capabilities` output: which languages and
frameworks are supported, with what evidence flags. Adding a
strategy without updating the registry is a build-break by design —
the discipline test
`weld_capabilities_test.test_expected_strategies_match_disk` fails
on registry/disk drift.

For more on the connected structure itself (entities, edges, MCP
surface), see [graph-schema.md](graph-schema.md) and the project
[README.md](../README.md). For test-suite contract, see
[weld/tests/fixtures/blast_radius/README.md](../weld/tests/fixtures/blast_radius/README.md).

---

## 2. The strategy contract

A strategy module exposes one public function:

```python
from pathlib import Path
from weld.strategies._helpers import StrategyResult

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Return (nodes, edges, discovered_from) for this source entry."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []
    # ...
    return StrategyResult(nodes, edges, discovered_from)
```

- `root` is the repository root as a `Path`.
- `source` is the matched entry from `discover.yaml`, including
  `glob`, `type`, optional `exclude`, and any strategy-specific
  keys you want to read.
- `context` is a mutable dict the discovery driver passes through
  every strategy invocation. Used for cross-strategy hand-offs;
  most strategies ignore it.
- The return value is a `StrategyResult` named tuple with three
  fields: `nodes` (mapping of node id to node dict), `edges` (list
  of edge dicts), and `discovered_from` (list of repo-relative
  paths the strategy actually inspected — used by the
  determinism harness to render diffs).

The simplest example in the bundled tree is
[`weld/strategies/config_file.py`](../weld/strategies/config_file.py).
The most thorough example is
[`weld/strategies/python_module.py`](../weld/strategies/python_module.py).
Read both before writing a new one. Strategies must not import
each other; reusable helpers live in `weld/strategies/_helpers.py`.

### Node and edge shape

A node id is a string of the form `<type>:<stable-key>`. A node
dict carries a `type`, a human-readable `label`, and a `props`
mapping. Required props by convention: `source_strategy`,
`authority` (`canonical` or `derived`), `confidence` (`definite`,
`inferred`, or `speculative`), and `roles` (a list of role tags).
Edges have `from`, `to`, `type`, and `props`. Stamp every edge
with `source_strategy` so consumers can attribute provenance.

If a strategy emits an edge whose `to` does not exist as a node,
the post-processing step in `weld/_discover_postprocess.py` will
prune the edge as dangling. Either emit the target node alongside
the edge, or rely on another strategy to emit it (and document the
dependency in your discover.yaml entry). The Dockerfile strategy
[`weld/strategies/dockerfile.py`](../weld/strategies/dockerfile.py)
shows the "emit the target file node yourself" pattern.

---

## 3. Step-by-step: adding a new strategy

1. **Identify the framework or language.** Decide what evidence
   shape the strategy will produce: file presence, module/package
   boundaries, imports, symbol declarations, calls, tests,
   build-graph edges, or runtime topology. The capability matrix
   uses the same vocabulary; see `LANGUAGE_EVIDENCE` and
   `FRAMEWORK_EVIDENCE` in `weld/_capabilities_registry.py`.

2. **Write the strategy file.** Project-local at
   `<repo>/.weld/strategies/<name>.py`, or bundled at
   `weld/strategies/<name>.py` (the bundled path requires a PR
   into the upstream repository). Public API: a single
   `extract(root, source, context) -> StrategyResult` function.
   Keep one strategy per file. Aim for under ~200 lines; split
   pure helpers into `_<name>_helpers.py` if you need more.

3. **Wire the strategy in `discover.yaml`.** Add a `sources`
   entry mapping the matched glob (or `files: [...]`) to a node
   `type` and your `strategy` name. Project-local
   `discover.yaml` lives at `<repo>/.weld/discover.yaml`; the
   bundled defaults under `weld/templates/` ship as a starting
   point on `wd init`.

4. **Declare capabilities** by adding an entry to
   `STRATEGY_CAPABILITIES` in
   [`weld/_capabilities_registry.py`](../weld/_capabilities_registry.py).
   Set `language=`, `framework=`, or both, plus the `evidence`
   tuple drawn from `LANGUAGE_EVIDENCE` /`FRAMEWORK_EVIDENCE`,
   and the relevant `file_extensions` / `file_basenames`. This
   step is *required for bundled strategies*. For project-local
   strategies, see the "v1.1+ gap" note at the bottom of this
   section.

5. **Add a fixture** under
   `weld/tests/fixtures/blast_radius/<framework>/`. The
   determinism harness picks fixtures up automatically — the
   contract is documented in
   [weld/tests/fixtures/blast_radius/README.md](../weld/tests/fixtures/blast_radius/README.md).
   Generate goldens with:

   ```bash
   bazel run //weld/tests:regenerate_blast_radius_goldens
   ```

6. **Run the test gate.** `bazel test //...` plus
   `bazel run //tools:lint_repo` cover lint, build, and tests; the
   `test_expected_strategies_match_disk` check inside
   `weld_capabilities_test` will fail if you forgot step 4 — that
   is the design.

> **Project-local capability registration is a v1.1+ gap.** The
> registry currently only knows about bundled strategies under
> `weld/strategies/`. A project-local strategy under
> `.weld/strategies/` will run and emit nodes/edges, but it will
> not appear in `wd capabilities` output until a project-local
> registry hook lands. If you need this, please file an issue
> with the title `feat(capabilities): project-local capability
> registration` so it can be tracked. Until then, the workaround
> is to land the strategy bundled (PR upstream).

---

## 4. Worked example: a hypothetical `csproj_dependency` strategy

This walkthrough adds preliminary support for C#/.NET projects by
parsing `.csproj` files for two evidence shapes:

- `<Compile Include="…" />` entries → `csproj → contains → file:<src>` edges.
- `<PackageReference Include="…" />` entries →
  `csproj → depends_on → package:nuget:<name>` edges.

This is illustrative only; production-grade .NET support is
deliberately out of scope and tracked separately (see
`MISSING_FRAMEWORK_PATTERNS["dotnet"]` in the registry).

### 4a. The strategy file

Place this at `<repo>/.weld/strategies/csproj_dependency.py`
(project-local) or `weld/strategies/csproj_dependency.py`
(bundled, requires PR). The skeleton below is ~50 LOC; a real
implementation would harden the regex into proper XML parsing
and handle wildcards in `<Compile Include="…" />`:

```python
"""Strategy: parse .csproj into csproj nodes + contains/depends_on edges."""
from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

_COMPILE = re.compile(r'<Compile\s+Include="([^"]+)"')
_PKGREF = re.compile(r'<PackageReference\s+Include="([^"]+)"')

_PROPS_NODE = {
    "source_strategy": "csproj_dependency",
    "authority": "canonical",
    "confidence": "definite",
    "roles": ["build"],
}
_PROPS_EDGE = {"source_strategy": "csproj_dependency", "confidence": "definite"}


def _resolve(src: str, base: Path, root: Path) -> str | None:
    cand = (base / src.replace("\\", "/")).resolve()
    try:
        return cand.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_files: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, seen_files)

    for proj in filter_glob_results(root, sorted(parent.glob(Path(pattern).name))):
        if should_skip(proj, excludes):
            continue
        rel = proj.relative_to(root).as_posix()
        seen_files.append(rel)
        text = proj.read_text(encoding="utf-8", errors="replace")
        nid = f"csproj:{proj.stem}"
        nodes[nid] = {"type": "csproj", "label": proj.name, "props": {**_PROPS_NODE, "file": rel}}

        for src in _COMPILE.findall(text):
            resolved = _resolve(src, proj.parent, root)
            if resolved is None:
                continue
            target = f"file:{resolved}"
            nodes.setdefault(target, {
                "type": "file", "label": Path(resolved).name,
                "props": {**_PROPS_NODE, "file": resolved, "authority": "derived", "roles": ["code"]},
            })
            edges.append({"from": nid, "to": target, "type": "contains", "props": _PROPS_EDGE})

        for pkg in _PKGREF.findall(text):
            target = f"package:nuget:{pkg}"
            nodes.setdefault(target, {
                "type": "package", "label": pkg,
                "props": {**_PROPS_NODE, "ecosystem": "nuget", "authority": "derived", "roles": ["dependency"]},
            })
            edges.append({"from": nid, "to": target, "type": "depends_on", "props": _PROPS_EDGE})

    return StrategyResult(nodes, edges, seen_files)
```

### 4b. The `discover.yaml` entry

Append to `<repo>/.weld/discover.yaml`:

```yaml
sources:
  - glob: "src/**/*.csproj"
    type: csproj
    strategy: csproj_dependency
```

### 4c. The capability registry entry (bundled only)

In `weld/_capabilities_registry.py`, add:

```python
"csproj_dependency": _fw(
    "dotnet",
    ("nodes_emitted", "srcs_edges", "deps_edges"),
    (".csproj",),
),
```

…and remove the `"dotnet"` entry from `MISSING_FRAMEWORK_PATTERNS`
since the framework now has real strategy coverage.

### 4d. The minimal fixture

Create `weld/tests/fixtures/blast_radius/csproj_minimal/`:

```
csproj_minimal/
├── README.md                       # describes the scenario
├── .weld/
│   └── discover.yaml               # the source entry above
├── src/
│   └── App/
│       ├── App.csproj
│       └── Program.cs
└── expected/
    ├── graph.json                  # generated by the regen target
    └── impact_program_cs.json      # placeholder seed
```

`App.csproj`:

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <Compile Include="Program.cs" />
    <PackageReference Include="Newtonsoft.Json" />
  </ItemGroup>
</Project>
```

`Program.cs`: any single-line valid (or even invalid) C# source —
the strategy reads only `.csproj`. The seed placeholder
`expected/impact_program_cs.json`:

```json
{"depth": 3, "target": {"input": "src/App/Program.cs"}}
```

### 4e. Run the regenerator and the test

```bash
bazel run //weld/tests:regenerate_blast_radius_goldens
bazel test //weld/tests:weld_blast_radius_fixtures_test --test_output=errors
```

The first command writes `expected/graph.json` and fills in the
`impact_program_cs.json` golden. The second command verifies the
output is byte-deterministic across runs. Inspect the generated
graph for sanity, then commit.

---

## 5. Adding a fixture without a new strategy

If you only want to pin a regression for an *already-supported*
framework, skip steps 1–4 and follow the harness contract in
[weld/tests/fixtures/blast_radius/README.md](../weld/tests/fixtures/blast_radius/README.md).
The harness auto-discovers fixture directories and applies the
golden-parity, impact-parity, and in-process-determinism checks.

---

## 6. CI sync check

Strategy/registry drift is already caught by an existing test —
no extra doc-sync test is needed:

- `weld_capabilities_test.test_expected_strategies_match_disk`
  fails on any module under `weld/strategies/` that is not in
  `STRATEGY_CAPABILITIES` (and vice versa).
- `weld_capabilities_test.test_missing_patterns_disjoint_from_known_frameworks`
  fails if a framework appears in both `STRATEGY_CAPABILITIES` and
  `MISSING_FRAMEWORK_PATTERNS`.
- The blast-radius fixture harness
  (`weld_blast_radius_fixtures_test`) fails on golden drift the
  moment a strategy starts emitting different output.

`bazel test //...` runs all three on every change. So the rule
is: add a strategy → update the registry → run the tests. The
gate tells you if you missed a step.

---

## 7. Pre-PR checklist and reference

Checklist:

- [ ] `extract(root, source, context) -> StrategyResult` is the
  only public function.
- [ ] `discover.yaml` entry added.
- [ ] `STRATEGY_CAPABILITIES` entry added (bundled strategies).
- [ ] Fixture + placeholder seed under
  `weld/tests/fixtures/blast_radius/<framework>/`; goldens
  generated via `bazel run //weld/tests:regenerate_blast_radius_goldens`.
- [ ] `bazel test //...` and `bazel run //tools:lint_repo` pass.
- [ ] If anything in this guide was wrong or missing, please open
  an issue so the next reader finds the answer on first read.

Reference:

- [graph-schema.md](graph-schema.md) — node/edge schema.
- [weld/tests/fixtures/blast_radius/README.md](../weld/tests/fixtures/blast_radius/README.md)
  — fixture harness contract.
- [weld/strategies/config_file.py](../weld/strategies/config_file.py)
  — minimal strategy.
- [weld/strategies/dockerfile.py](../weld/strategies/dockerfile.py)
  — emits nodes plus `contains` edges with defensive target-node
  creation.
- [weld/strategies/python_module.py](../weld/strategies/python_module.py)
  — full-featured strategy.
- ADRs 0043–0047 in the upstream design docs — capability matrix,
  edge closures, and the fixture suite.
