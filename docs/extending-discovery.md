# Extending discovery: adding a strategy

This guide is for users who want weld to understand a framework or
language it does not support out of the box. It describes the
discovery layer, the contract a strategy plugin must satisfy, and
walks through two complete worked examples end-to-end. After reading
this you should be able to drop a Python file in
`.weld/strategies/` and have `wd impact`, `wd query`, and the
capability matrix all light up for your new framework.

This document covers the practical "how": writing a strategy that
extends discovery to a new language, framework, or build system, and
the contract its outputs must satisfy.

This guide has two worked examples, each following the same five-step
shape (strategy file → `discover.yaml` → capability registry → fixture
→ regenerate and test):

- [§4 — a `csproj_dependency` build strategy](#4-worked-example-a-hypothetical-csproj_dependency-strategy)
  recovers *structural* evidence from a project file (`contains` /
  `depends_on` edges).
- [§5 — an `events_nats` pub-sub strategy](#5-worked-example-a-hypothetical-events_nats-pub-sub-strategy)
  recovers an *interaction surface* from call sites (`channel` nodes plus
  directional `produces` / `consumes` edges).

Skim whichever is closer to your framework's evidence shape.

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
project-local code entirely under `--safe` mode.

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
   step is *required for bundled strategies*. **Project-local
   strategies** under `.weld/strategies/` cannot edit this in-tree
   table; they declare capabilities with a declarative manifest
   instead — see [Project-local capability
   registration](#project-local-capability-registration) at the end
   of this section.

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

### Project-local capability registration

A project-local strategy under `.weld/strategies/` cannot edit the
in-tree `STRATEGY_CAPABILITIES` table, so it declares its capability as
**data** — read in the same trust tier as `discover.yaml`, never by
importing the strategy module. That keeps it correct under
`wd discover --safe`, which refuses to execute project-local code: safe
mode still won't *run* the strategy, but it *reads* the declared
capability. Use either declaration site, with the same fields as a
registry entry (`language` / `languages`, `framework` / `frameworks`,
`evidence`, `file_extensions`, `file_basenames`):

- an inline `capabilities:` block on the strategy's `discover.yaml`
  source entry, or
- a sibling `.weld/strategies/<name>.yaml` manifest (optionally wrapped
  in a top-level `capabilities:` key).

```yaml
# .weld/discover.yaml
sources:
  - glob: "src/**/*.foo"
    type: file
    strategy: foo_lang          # runs .weld/strategies/foo_lang.py
    capabilities:
      language: foolang
      evidence: [file, symbols]
      file_extensions: [".foo"]
```

The inline block wins when both sites declare the same strategy. The
evidence rule is unchanged: a declared flag flips true only when the
strategy is wired **and** the graph carries a matching file, so a
declaration alone can never spoof support. Under `--safe` the strategy's
`extract` never runs, so the capability surfaces with all evidence flags
`False` until a normal discovery run collects real evidence — declared,
but honestly empty.

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

## 5. Worked example: a hypothetical `events_nats` pub-sub strategy

The `csproj_dependency` walkthrough above recovers *structural* evidence
from a project file. This second example recovers an *interaction
surface*: it reads Python call sites and emits the `channel` nodes and
directional `produces` / `consumes` edges that `wd impact` follows to
trace a message across a pub/sub boundary. It mirrors the bundled
[`events_mqtt`](../weld/strategies/events_mqtt.py) strategy — read that
file for the production version. This walkthrough is illustrative and
uses NATS purely because its `publish("subject", …)` /
`subscribe("subject", …)` API is the cleanest single-literal shape to
teach the pattern.

The interaction-surface vocabulary — the `channel` node type, the
`protocol` / `surface_kind` / `transport` / `boundary_kind` node props,
and the `produces` / `consumes` edge types — is defined and validated in
[`weld/contract.py`](../weld/contract.py). The rule there is
omission-over-guess: a strategy stamps a metadata prop **only** when it
is statically knowable, and leaves it unset otherwise.

Two evidence shapes:

- `<client>.publish("subject", …)` → a `channel:tcp:<subject>` node plus
  a `produces` edge `file:<caller> → channel:tcp:<subject>`.
- `<client>.subscribe("subject")` → the same channel node plus a
  `consumes` edge in the opposite direction.

NATS rides the TCP wire, so `transport="tcp"`: the `event` row of
`PROTOCOL_TRANSPORT_COMPATIBILITY` in `weld/contract.py` admits
`{amqp, kafka, mqtt, tcp, inproc}`. A broker with its own transport
value (Kafka, MQTT, AMQP) would use that instead; the node shape is
otherwise identical.

Prefer **reusing** an existing `TRANSPORT_VALUES` member over minting a
new one: a new value is a contract change (`SCHEMA_VERSION` bump plus an
ADR), whereas reuse keeps a strategy purely additive. The bundled
`dds_idl` strategy is the worked example — its CycloneDDS / FastDDS topic
channels reuse `ros2_dds` (the DDS/RTPS wire, not the ROS2 framework)
rather than adding a `dds` value, and leave `protocol` unset because no
`PROTOCOL_VALUES` member fits non-ROS2 DDS (omission-over-guess skips the
coherence check). Mint a new transport value only when a concrete
correctness need forces the distinction.

### 5a. The strategy file

Place this at `<repo>/.weld/strategies/events_nats.py` (project-local)
or `weld/strategies/events_nats.py` (bundled, requires PR). It reuses
the shared call-site primitives in
[`weld/strategies/_ast_calls.py`](../weld/strategies/_ast_calls.py) and
the channel-node minter in
[`weld/strategies/events_shared.py`](../weld/strategies/events_shared.py),
so the strategy is mostly a table of rules plus a short walk:

```python
"""Strategy: NATS subject producer/consumer extraction (illustrative)."""
from __future__ import annotations

from pathlib import Path

from weld.strategies._ast_calls import (
    classify_receiver_verb,
    file_imports_root,
    iter_call_nodes,
    iter_python_asts,
    literal_first_arg,
)
from weld.strategies._helpers import StrategyResult
from weld.strategies.events_shared import channel_id, channel_node, file_node_id

_TRANSPORT = "tcp"  # NATS rides TCP; see PROTOCOL_TRANSPORT_COMPATIBILITY.

# Idiomatic NATS connection-handle names (``nc = await nats.connect(...)``).
_CLIENT_ROOTS = frozenset(["nc", "nats_client"])
_PRODUCER_RULES = ((_CLIENT_ROOTS, frozenset(["publish"]), _TRANSPORT),)
_CONSUMER_RULES = ((_CLIENT_ROOTS, frozenset(["subscribe"]), _TRANSPORT),)
_IMPORT_ROOTS = frozenset(["nats"])  # pre-filter: only walk files importing nats.


def _edge(src: str, dst: str, etype: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": etype,
        "props": {"source_strategy": "events_nats", "confidence": "inferred"},
    }


def _emit(node, rules, etype, rel_path, file_id, nodes, edges) -> bool:
    transport = classify_receiver_verb(node, rules)
    if transport is None:
        return False
    subject = literal_first_arg(node)  # dynamic subjects are dropped, not guessed
    if not subject:
        return False
    cid = channel_id(transport, subject)
    nodes[cid] = channel_node(transport=transport, name=subject, rel_path=rel_path)
    edges.append(_edge(file_id, cid, etype))
    return True


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    for rel_path, tree in iter_python_asts(root, pattern):
        if not file_imports_root(tree, _IMPORT_ROOTS):
            continue
        file_id = file_node_id(rel_path)
        emitted = False
        for node in iter_call_nodes(tree):
            if _emit(node, _PRODUCER_RULES, "produces", rel_path, file_id, nodes, edges):
                emitted = True
                continue
            if _emit(node, _CONSUMER_RULES, "consumes", rel_path, file_id, nodes, edges):
                emitted = True
        if emitted:
            discovered_from.append(rel_path)

    return StrategyResult(nodes, edges, discovered_from)
```

Three details, all shared with the bundled events family:

- **Node vs edge provenance.** `channel_node()` stamps the channel node
  `authority="canonical"`, `confidence="definite"`, and
  `source_strategy="events"` (the family name). The directional edge
  carries `source_strategy="events_nats"` and `confidence="inferred"`:
  the channel is a definite declaration, but *which* file produces or
  consumes it is a static inference. That node-definite / edge-inferred
  split is the events-family convention.
- **The `file:` endpoint is minted elsewhere.** The edge's `from` side is
  `file_node_id(rel_path)` — the canonical extensionless `file:<path>`
  id that the `python_module` strategy mints. `events_nats` does **not**
  emit that node; it relies on `python_module` running in the same
  discovery pass (see 5b). If nothing mints the `file:` node,
  `weld/_discover_postprocess.py` prunes the edge as dangling — the
  contract from §2. (Contrast `csproj_dependency`, which mints its own
  target nodes with `nodes.setdefault(...)`.)
- **Static-truth only.** A non-literal subject — `nc.publish(topic, …)`
  where `topic` is a variable — makes `literal_first_arg` return `None`,
  and the call is skipped. Partial coverage is honest; a guessed subject
  is not.

### 5b. The `discover.yaml` entry

Append to `<repo>/.weld/discover.yaml`. Keep the `python_module` entry:
it mints the `file:` nodes the `produces` / `consumes` edges attach to.

```yaml
sources:
  - glob: "src/**/*.py"
    type: file
    strategy: python_module
  - glob: "src/**/*.py"
    type: channel
    strategy: events_nats
```

### 5c. The capability registry entry (bundled only)

Unlike `csproj_dependency`, which introduced a brand-new `dotnet`
framework, `events_nats` slots under the **existing** `events` framework
that the bundled `events*` strategies already register. In
[`weld/_capabilities_registry.py`](../weld/_capabilities_registry.py),
add one line alongside them:

```python
"events_nats": _fw("events", ("nodes_emitted",), (".py",)),
```

There is no `MISSING_FRAMEWORK_PATTERNS` entry to remove — `events` is
already covered. The `test_expected_strategies_match_disk` discipline
test still fails until this line is present, exactly as for any bundled
strategy. If you keep `events_nats` **project-local** rather than
upstreaming it, skip this registry line and declare the capability with
a manifest instead — see [Project-local capability
registration](#project-local-capability-registration).

### 5d. The minimal fixture

Create `weld/tests/fixtures/blast_radius/events_nats_minimal/`:

```
events_nats_minimal/
├── README.md                       # describes the scenario
├── .weld/
│   └── discover.yaml               # the two source entries above
├── src/
│   └── orders.py                   # one publish + one subscribe
└── expected/
    ├── graph.json                  # generated by the regen target
    └── impact_orders_py.json       # placeholder seed
```

`src/orders.py` — one subject, produced in one function and consumed in
another:

```python
import nats


async def emit(nc):
    await nc.publish("orders.created", b"{}")


async def listen(nc):
    await nc.subscribe("orders.created")
```

This yields a single `channel:tcp:orders.created` node with both a
`produces` and a `consumes` edge from `file:src/orders`. The `await`
wrappers do not matter — the AST walk sees the inner call either way.
The seed placeholder `expected/impact_orders_py.json`:

```json
{"depth": 3, "target": {"input": "src/orders.py"}}
```

### 5e. Run the regenerator and the test

```bash
bazel run //weld/tests:regenerate_blast_radius_goldens
bazel test //weld/tests:weld_blast_radius_fixtures_test --test_output=errors
```

The first command writes `expected/graph.json` and fills in the
`impact_orders_py.json` golden; the second verifies the output is
byte-deterministic across runs. Inspect the generated channel node and
its two edges for sanity, then commit.

> **Open follow-up — a runnable `examples/NN-*/` fixture?** The
> `examples/` tree ships full, runnable sample projects. Whether this
> pub-sub walkthrough should graduate into a runnable `examples/NN-*/`
> project — versus staying an in-doc illustration like `csproj_dependency`
> — is left open here; raise it with the maintainers if the runnable
> version would help.

---

## 6. Adding a fixture without a new strategy

If you only want to pin a regression for an *already-supported*
framework, skip steps 1–4 and follow the harness contract in
[weld/tests/fixtures/blast_radius/README.md](../weld/tests/fixtures/blast_radius/README.md).
The harness auto-discovers fixture directories and applies the
golden-parity, impact-parity, and in-process-determinism checks.

---

## 7. CI sync check

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

## 8. Pre-PR checklist and reference

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
  — full-featured strategy; also the file-node minter the §5 example's
  `produces` / `consumes` edges attach to.
- [weld/strategies/events_mqtt.py](../weld/strategies/events_mqtt.py)
  — self-contained pub/sub strategy (channel node plus directional
  `produces` / `consumes` edges): the model for the §5 walkthrough.
- [weld/strategies/_ast_calls.py](../weld/strategies/_ast_calls.py)
  — shared call-site primitives (`iter_python_asts`,
  `classify_receiver_verb`, `literal_first_arg`) the events family reuses.
- ADRs 0043–0047 in the upstream design docs — capability matrix,
  edge closures, and the fixture suite.
