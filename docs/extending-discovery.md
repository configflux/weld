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

### Resolving `glob:` and honouring `exclude:`

Resolve the source entry's glob with
`weld.strategies._glob_resolve.resolve_glob(root, pattern, excludes)` and
nothing else:

```python
from weld.strategies._glob_resolve import resolve_glob

for path in resolve_glob(root, source["glob"], source.get("exclude", [])):
    ...
```

If your strategy also records `discovered_from` — and it should — take
both from one call instead:

```python
from weld.strategies._glob_resolve import resolve_glob_with_provenance

matched, provenance = resolve_glob_with_provenance(root, pattern, excludes)
discovered_from.extend(provenance)
```

Report every file you actually **read**, not only the ones your glob
resolved. A strategy that follows a reference out of its own file set —
the bazel strategy reading a `.bzl` a `BUILD` file loads, for instance —
must name that file too. Discovery records a content hash for every file
in `discovered_from`, and that inventory is what freshness compares a
dirty working tree against: a real input you leave out is one no
`wd stale` can see and no incremental run will re-read.

That one module is the whole contract, and it exists because strategies
used to carry hand-copied variants of it that drifted apart. It handles,
once:

- **`**`** — recursive patterns resolve at any depth. Do **not** add a
  `(root / pattern).parent.is_dir()` early return: for `docs/**/*.md`
  that parent is the literal path `docs/**`, which is never a directory,
  so the guard makes your strategy emit *nothing* — silence, not a
  subset, which is a trap with no partial result to notice.
- **a wildcard in a *directory* segment** — `apps/*/package.json`,
  `services/*/src/*.py`, `packages/*/Dockerfile` all resolve, and `*`
  spans exactly one segment (so `apps/*/package.json` does not reach
  `apps/b/nested/package.json`; write `apps/**/package.json` for that).
  Same trap as the bullet above, and for the same reason: the parent of
  `apps/*/package.json` is the literal path `apps/*`, which is never a
  directory, so a `.parent.is_dir()` guard makes the pattern match
  nothing at all.
- **`{a,b}` brace alternatives** — `packages/ui/src/**/*.{ts,tsx}`
  expands, unions and de-duplicates. `Path.glob` does not understand
  braces, so without this the pattern matches nothing at all.
- **`exclude:`** — matching directories are pruned *during descent*,
  which is what gives the directory form its meaning. Filtering an
  already-resolved file list is **not** equivalent: the matcher tests the
  file path with no ancestor-directory check, so `exclude: ["pkg/tests"]`
  never matches `pkg/tests/foo.py` and the whole subtree is read and
  emitted anyway. Do not re-filter afterwards; the repo-boundary filter
  and vendored-tree skip are applied too, so no `filter_glob_results`
  call is needed either.
- **order** — the result is sorted, so your node emission and
  `discovered_from` are properties of the tree rather than of filesystem
  enumeration.

Both exclude forms below must work, and only the shared resolver
delivers the first:

```yaml
exclude:
  - pkg/tests      # directory form (also matches a bare `tests` at any depth)
  - pkg/tests/**   # subtree form
```

One historical trap, now closed: a single-directory pattern (no `**`)
resolves through `Path.glob`, which used to yield matching *directories*
alongside files while the `**` branch yielded only files. Neither branch
yields a directory any more, so `glob: "packs/*"` no longer hands your
strategy `packs/nested` beside `packs/a.yaml`. An
`if not path.is_file(): continue` before you read or emit is still worth
keeping as belt-and-braces, but it is no longer load-bearing. If you do
guard, guard when you *emit* rather than by filtering the resolved list:
the resolution call must stay identical to the one
`weld._source_resolve.resolve_source_files` makes, or the set your
strategy covers and the set discovery records drift, and the difference
reads as scope that is never covered — staleness that never clears.

Spell every repo-relative path you put in `props.file`, `props.dir` or
`discovered_from` with `weld._rel_path.rel_to_root(path, root)`, never
`str(path.relative_to(root))`. The latter is separator-native, so off
POSIX your props disagree with the POSIX node ids addressing the same
file. `weld/tests/strategy_path_prop_spelling_test.py` enforces this.

The simplest example in the bundled tree is
[`weld/strategies/config_file.py`](../weld/strategies/config_file.py),
which accepts either `glob:` or an explicit `files:` list (or both).
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

**Also stamp `props.provenance.file` on any edge that crosses files** —
set it to the repo-relative POSIX path of the file *your strategy read
to produce the edge*, which is normally the `from` side, never the
resolved target. This is not decoration; it is what keeps the edge
alive across an incremental refresh. When a file changes, the
orchestrator purges that file's nodes and then decides which edges to
keep: an edge that names its producing file survives unless *that* file
is stale, while an edge that names nothing is dropped whenever either
endpoint was purged. So an unstamped edge pointing *into* a changed
file is dropped with it — and if your strategy's own glob holds no
changed file, the strategy never re-runs and the edge is gone until the
next full discover. The graph then silently disagrees with itself
depending on how it was built, which is the hardest class of discovery
bug to notice. [`weld/strategies/test_peer.py`](../weld/strategies/test_peer.py)
shows the stamp in context.

This is checked automatically: `wd lint`'s `cross-source-edge-provenance`
rule flags a strategy edge whose endpoints are not provably minted by the
same `discover.yaml` source entry and carries no `props.provenance.file`,
naming the strategy and the missing stamp.

**If a node's own existence depends on member files it does not itself
anchor via `props.file`** (a directory- or namespace-rooted parent like
`python_package`'s `package:python:*`, `csharp_package`'s
`package:csharp:*`, or `go_package`'s `package:go:*`, whose real anchor
is `props.dir` or nothing at all),
stamp `"package"` into `props.roles` and always emit at least one
outgoing `contains` edge to a member whenever the node is emitted. The
orchestrator purges such a node automatically once an incremental refresh
leaves it with zero `contains` out-edges — the same self-repair a full
discover gives you for free, since a full run's own emission logic
never mints the node in the first place once its last member is gone
(`weld._discover_membership_purge`). Do **not** stamp the `package` role
on a node representing something else entirely (an external dependency
sentinel, for example) — for those, having no outgoing edges (only
inbound `depends_on` from the files that reference them) is normal, and
the `package` role would make the orchestrator purge them the next time
any unrelated file goes stale.

If a strategy emits an edge whose `to` does not exist as a node,
the post-processing step in `weld/_discover_postprocess.py` will
prune the edge as dangling. Either emit the target node alongside
the edge, or rely on another strategy to emit it (and document the
dependency in your discover.yaml entry). The Dockerfile strategy
[`weld/strategies/dockerfile.py`](../weld/strategies/dockerfile.py)
shows the "emit the target file node yourself" pattern.

When two source entries emit the same node id, the driver keeps the
better-evidenced claim: a node whose `confidence` is strictly weaker
than the one already recorded for that id is discarded, and otherwise
the later entry wins as before. So a placeholder emitted only to keep
an edge from dangling — no `file`, no `line`, `confidence:
speculative` — cannot overwrite the real declaration another entry
parsed, whatever order the entries are declared in. Emit placeholders
freely; just give the definite claim every prop you actually proved.

**Stamping the placeholder's `confidence` is not optional.** The veto
compares two ranks, so it can only fire when *both* sides state one. A
placeholder that states no `confidence` at all is not "weakest" — it is
unrankable, the comparison is skipped, and the driver falls back to
last-writer-wins, so your placeholder silently *replaces* the real node
whenever your entry happens to be declared after the one that parsed the
file. Which rank to state is a judgement about what you observed:
`speculative` for a node you never saw (a call target defined outside
your glob), `inferred` for one you did read but did not fully extract
(the boundary `file:` node a route strategy stands up for the file it
scanned). Either one is ranked; stating none is the bug.

**This guarantee is per source entry, not per file.** `extract()` is
called once per source entry, and if your `glob` matches several
files, your own `nodes` dict accumulates all of them in one call — the
driver's claim veto only runs once your `StrategyResult` comes back,
so it never sees two of *your own* files competing for the same id. If
your strategy can mint the same id from two different matched files
(for example, an id derived from a declared name rather than from the
file path), a plain `nodes.setdefault(...)` lets whichever file
`resolve_glob`'s sorted walk happens to reach first win outright,
silently discarding the other claim — order-dependent, and observable
across incremental vs. full discovery. `weld/strategies/cpp_cmake.py`
hit exactly this (a project's own id collided with one of its
`find_package` dependencies) and fixed it by calling
`weld._discover_node_merge.claim_supersedes` itself before writing to
its own `nodes` dict, the same veto the driver applies — see
`weld/strategies/cpp_cmake.py`'s `_ensure_project_node` and
`weld/strategies/_cmake_packages.py`'s `ensure_package_sentinel` for
the pattern.

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

from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult

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

    for proj in resolve_glob(root, pattern, excludes):
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
