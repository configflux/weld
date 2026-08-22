# Public Benchmark (`wd bench --public`)

Per the public benchmark methodology, every minor release of weld ships
a refreshed public-benchmark report. The methodology is published first,
the numbers second, and weld loses honestly when it loses.

## Running

```bash
# Default: writes to docs/bench/PUBLIC-BENCHMARK-<version>.md.
wd bench --public

# Custom output path:
wd bench --public --out path/to/report.md

# Custom corpus manifest (for development; production uses the pinned
# weld/bench/public_corpus.yaml):
wd bench --public --corpus path/to/corpus.yaml

# Re-run the corpus and assert byte-identity with the committed report:
wd bench --public --verify

# Print to stdout instead of writing:
wd bench --public --print
```

## Corpus

The corpus manifest is at `weld/bench/public_corpus.yaml`. It is
SHA-pinned: each repo entry declares the exact commit to clone before
benchmarking. Updating a SHA is an ADR amendment, not a quiet change.

Repos with placeholder SHAs (annotated with `placeholder: true` or
matching the heuristic in `weld.bench._public_setup.is_placeholder_sha`)
emit `SKIPPED: <reason>` rows in the report rather than crashing the
run. The bench is honest about what it did NOT exercise.

## Adapters

Each task is dispatched against the following adapters:

| Adapter         | Scope         | What it runs                                       |
|-----------------|---------------|----------------------------------------------------|
| `weld`          | every repo    | `wd brief` / `wd references` / `wd query`          |
| `weld_libclang` | cpp repos     | same surfaces with `WELD_CPP_LIBCLANG=1` active    |
| `grep`          | every repo    | shared baseline (ripgrep-style file search)        |
| `tree_sitter`   | every repo    | `tree-sitter query` (symbol-only baseline)         |
| `graphify`      | every repo    | competitor CLI (install via `pipx install graphifyy`) |

An adapter whose external binary is missing emits `unavailable` and is
excluded from per-family aggregates so its absence does not depress its
own median scores.

`weld_libclang` is the libclang variant of the weld stack: it activates
the optional C++ best-in-class methodology by setting
`WELD_CPP_LIBCLANG=1` for the discovery call. It runs only when

1. the `cpp-libclang` extra is installed (`clang.cindex` imports), and
2. the repo's `setup:` clause produced a `compile_commands.json`.

When either precondition is missing the adapter emits a stable
`unavailable` / `SKIPPED: <reason>` cell so the report tells the truth
rather than crashing or fabricating numbers.

## Setup hooks

A corpus entry may declare a `setup:` clause that runs once after a
successful clone. The canonical case is the `nlohmann_json` entry,
where `cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON .` produces
the database the libclang variant consumes:

```yaml
setup:
  requires_binary: cmake
  cmd:
    - cmake
    - -B
    - build
    - -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
    - .
  produces: build/compile_commands.json
  timeout_s: 300
```

The step is gated on the `requires_binary` being present on PATH. When
the binary is missing -- e.g. cmake not installed in the runtime -- the
per-repo setup state is recorded as `setup_unavailable`, the primary
`materialized` status is preserved (the clone succeeded), and the
libclang adapter renders SKIPPED for that repo's rows.

## Output

The report is written to `docs/bench/PUBLIC-BENCHMARK-<version>.md` by
default, under the `--root` being benchmarked. `<version>` is the version
of the weld that produced the report -- the same one in the report header
and in `wd --version`, not the version of the repository under benchmark.
When weld cannot determine its own version (a partial checkout with
neither installed distribution metadata nor a `VERSION` file), the report
falls back to the unversioned `docs/bench/PUBLIC-BENCHMARK.md`. Sections:

- `## Methodology` -- benchmark methodology abbreviated
- `## Corpus manifest` -- repo ids, families covered, materialization status
- `## Per-task results` -- one row per (task, adapter) combo
- `## Per-family aggregates` -- median precision / recall / F1 per family
- `## Caveats` -- every place weld lost or was degraded (honest losing)

## Reproducibility

`wd bench --public --verify` reruns the corpus and asserts byte-identical
output. Latency / wall-clock fields are normalized in the renderer so
they never drift run-to-run; only stable facts (which files an adapter
returned, token counts, status codes) appear in the rendered markdown.

## Where the work lives

- `weld/bench/_public_corpus.py` -- YAML loader + schema validation
- `weld/bench/_public_setup.py` -- placeholder detection, clone-on-demand,
  optional setup-step execution (cmake gate for the libclang variant)
- `weld/bench/_public_runner.py` -- adapter dispatch + per-task results;
  also enforces language-scoped adapter dispatch (`weld_libclang` runs
  only against `language: cpp` repos)
- `weld/bench/_public_report.py` -- markdown renderer
- `weld/bench/_public_report_cpp.py` -- C++ variant comparison
  (tree-sitter vs. libclang median F1) narrative
- `weld/bench/adapters/` -- one module per benchmarked tool, plus
  `weld_libclang.py` for the libclang variant of the weld stack
- `weld/bench/public_corpus.yaml` -- the SHA-pinned manifest
- `weld/bench/fixtures/public_corpus_smoke/` -- hermetic CI smoke corpus

## Follow-ups

- Live-corpus libclang validation: until cmake + libclang are available
  in CI, the libclang column for the production `nlohmann_json` entry
  renders SKIPPED. A manual run against a clone with the `cpp-libclang`
  extra installed is the next datapoint.
- Filling in the C# (eShop), polyrepo fixture, and ROS2 SHAs.
- Expanding per-repo task counts to the target of 5-10 per family per
  repo (~150 total).
