# Performance notes

This page documents how Weld behaves on synthetic repositories of
increasing size. Numbers here are guidance for planning, not service
levels: discovery and query timings depend on language mix, file sizes,
strategy plugins, and disk speed. The intent is to give realistic
expectations and a reproducible recipe so anyone with the installed
package can rerun the same scenarios on their own hardware.

For a deeper architectural view of why discovery is the dominant cost,
see [`docs/graph-schema.md`](graph-schema.md). For the on-demand
retrieval benchmark (token cost, first-context quality), see
[`weld/bench/`](../weld/bench/) and the existing report at
`weld/docs/bench-results.md`.

---

## Reference environment

The numbers in this page were produced on the following machine:

- CPU: Intel Core i7-9700F @ 3.00 GHz, 8 cores
- RAM: 16 GiB
- OS: Linux 6.6 (WSL2 on Windows 11), x86_64
- Python: 3.12.3
- Weld: from source, in-tree (`python -m weld`), no optional
  acceleration libraries installed
- Storage: ext4 under WSL2 (the `/tmp/` tempdirs)

Re-running on a faster CPU or a native Linux disk usually halves
discovery wall-clock; running on Windows NTFS or macOS APFS slows
small-file workloads noticeably. Treat the numbers as order-of-magnitude
guides, not benchmarks to compare across environments.

---

## Synthetic repository generator

The generator lives at
[`weld/bench/synthetic_large_repo.py`](../weld/bench/synthetic_large_repo.py).
It writes a deterministic Python tree with a configurable number of
modules and intra-package imports. Two layouts are supported:

- `single` -- one repository with `--files` modules in `pkg_NNNN/`
  buckets of 256 modules each. Pass `--git-init` to make the layout a
  real git repo (one commit) so `wd init` takes the faster
  `git ls-files` path; without the flag `wd init` falls back to
  `os.walk` and runs measurably slower.
- `polyrepo` -- a workspace root with `--children` nested git repos,
  each containing `--files` modules. Each child is `git init`-ed so
  `wd workspace bootstrap` recognises it as a federation child.
  (`--git-init` is a no-op for this layout.)

Generated trees are not committed. They are written to a directory you
choose (typically a `mktemp -d` path) and removed after timing.

The generator ships with the package, so anyone who installs Weld can
rerun these scenarios:

```bash
# Single repo, 1000 files (os.walk fallback path -- worst case)
python -m weld.bench.synthetic_large_repo \
  --layout single --files 1000 --output /tmp/weld-bench-1k

# Single repo, 1000 files, git-initialised (typical real-world path)
python -m weld.bench.synthetic_large_repo \
  --layout single --files 1000 --git-init --output /tmp/weld-bench-1k-git

# Polyrepo, 10 children x 1000 files each
python -m weld.bench.synthetic_large_repo \
  --layout polyrepo --children 10 --files 1000 \
  --output /tmp/weld-bench-poly-10x1k
```

Pass `--clean` to remove the output directory before generating. Pass
`--imports-per-file N` to change the number of intra-package imports
each generated module emits (default 2).

---

## Single-repo scaling

Each row was produced by:

1. Generating the synthetic tree with the script above.
2. Running `wd init <root>` to scaffold `.weld/discover.yaml`.
3. Running `wd discover --full --output <root>/.weld/graph.json <root>`.
4. Running `wd query helper --limit 5` from inside `<root>`.

Wall-clock numbers are a single run on the reference environment; they
include process start-up. `graph.json` size is the on-disk JSON the
discovery step writes.

| Files   | Mode | Generate | `wd init` | `wd discover` | `wd query` | `graph.json` |
| ------- | ---- | -------- | --------- | ------------- | ---------- | ------------ |
| 1 000   | os.walk |   0.23 s |   0.67 s  |    2.21 s     |   0.33 s   |   3.6 MiB    |
| 10 000  | os.walk |   1.69 s |   6.22 s  |   17.53 s     |   2.42 s   |  36.1 MiB    |
| 100 000 | os.walk |  12.56 s |  50.15 s  |  173.76 s     |  26.33 s   | 357 MiB      |
| 100 000 | git ls-files | -- | (faster -- see note) | -- | -- | -- |

The published 100k row reflects the `os.walk` fallback in
`weld/repo_boundary.py:iter_repo_files`, which is what the synthetic
generator produces by default. Real-world single repos are git-tracked
and `wd init` takes the `git ls-files` branch instead, which is
measurably faster on large trees because git already has the file list
indexed. To reproduce numbers that match a real repo, regenerate with
`--git-init` (see recipe below). Concrete `git ls-files` numbers are
left to the operator running the bench so the table reflects the
publisher's machine, not a stale run; the `os.walk` row remains a
worst-case ceiling.

A few things worth calling out:

- **`wd discover` is roughly linear in file count.** Going from 1k to
  10k files is ~8x discovery cost; 10k to 100k is ~10x. There is no
  sub-linear bulk path -- discovery walks each source according to the
  configured strategies and parses tree-sitter (or AST) per file.
- **`wd init` is *not* O(1) in practice.** At 100k files, scaffold
  scanning takes ~50 s on this machine because the bootstrap walks the
  tree to detect language mix and write a sensible `discover.yaml`.
  This is one-time-per-repo work but worth budgeting.
- **`wd query` from cold becomes noticeable at 100k.** The 26 s figure
  is dominated by loading the 357 MiB `graph.json` into memory; the
  in-memory search itself is sub-second once the graph is loaded.
- **Graph size scales with file count.** Roughly 3.6 KiB of graph per
  source file in this synthetic mix (small Python modules, two imports
  each). Real repos with longer files and more strategies typically
  produce 5--15 KiB of graph per file.

### Limits

The 100 000-file run completed but produced a 357 MiB `graph.json` and
a 26 s `query` cold path. For a single-process, file-on-disk graph,
that is the practical ceiling on this hardware for an interactive loop.
Above that scale, expect:

- Memory pressure when loading the graph (the JSON is parsed eagerly).
- Disk I/O dominating cold queries.
- Discovery exceeding three minutes on commodity laptops.

For repositories larger than ~100k source files, prefer a polyrepo
layout (one `.weld/` per child repo) over a single mega-repo with one
giant graph. The federation mode below is designed for exactly that
shape.

### Profiling breakdown (100k single repo)

The 50 s `wd init` and 26 s `wd query` cold numbers above were
profiled with `cProfile` to confirm where wall time actually goes.
The short version is that the *original* mental model -- "init walks
the tree" and "query parses the JSON" -- is not where the time is.

For `wd init` (50 s wall):

| Phase | cumtime | share |
| --- | --- | --- |
| `detect_frameworks` (line-by-line scan of every source file) | 60 s | 33% |
| `detect_docs` | 28 s | 16% |
| `detect_structure` | 18 s | 10% |
| `scan_files` (the actual filesystem walk) | 13 s | 7% |
| Other `detect_*` phases (each) | ~9 s | ~5% |

`pathlib.Path.relative_to` alone accounts for ~54% of cumulative CPU
because each detect_* phase iterates the file list and reparses
relative paths per file. The walk is fast; the per-file passes are
not. The synthetic single repo is not git-initialised, so these
numbers reflect the `os.walk` fallback in
`weld/repo_boundary.py:iter_repo_files` rather than the faster
`git ls-files` branch a real git repo would take.

For `wd query` cold (26 s wall):

| Phase | cumtime | share |
| --- | --- | --- |
| Build inverted index + BM25 corpus | 35.9 s | 80% |
| -- query index | 18.5 s | 41% |
| -- BM25 corpus | 16.4 s | 36% |
| `json.loads` of `graph.json` | 2.3 s | 6% |
| The actual search (`Graph.query`) | 5.8 s | 13% |

The 357 MiB JSON parse is **not** the bottleneck. Stdlib `json.loads`
takes ~2.3 s; `orjson.loads` takes ~1.6 s on the same file -- a
~0.7 s win out of 26 s. The dominant cost is rebuilding the inverted
index and BM25 corpus on every cold load, because neither structure
is persisted. A sidecar index written by `wd discover` would convert
that ~34 s rebuild into a load.

---

## Incremental auto-refresh cost

Every read command (`query`, `find`, `context`, ...) self-heals a stale
graph before serving (refresh-on-read). For that contract to feel free,
the steady-state incremental refresh has to be sub-second. The dominant
cost is **not** parsing the changed files -- it is rebuilding the
postprocess output, the query-state / file-index sidecars, and
re-serializing the whole canonical `graph.json` on every refresh.

Measured on **this repository** (≈6.5k nodes, ≈25k edges, a 14 MB
`graph.json`, ≈1.6k indexed files), single cold `python -m weld`
invocation per run, sqlite sidecar deferred on the read path (it is
federation-scoped and self-heals):

| Scenario | Before | After |
| --- | --- | --- |
| No content change (the common refresh-on-read case) | 5.9–8.4 s | **~1.5 s** (first refresh) / ~0.6 s (subsequent reads short-circuit on `stale=False`) |
| 1–5 source files changed (incremental rebuild) | ~6–8 s | ~6 s |

Both rows are the refresh-on-read optimization's before/after and are kept as the historical
baseline. The content-change row has since improved well past the `~6 s`
recorded here — the strategy-parse and glob-traversal levers described below
landed after it was written, and each was measured against its own contemporary
baseline rather than against this table.

The no-change collapse from ~6–8 s to ~1.5 s is the win that makes
refresh-on-read viable: once a refresh writes fresh state, later reads see
`stale=False` and skip discovery entirely (~0.6 s CLI floor). A genuine
content change still re-runs the global post-process and the 14 MB
`graph.json` re-serialization; the `python_callgraph` strategy no longer
re-extracts every call edge in a dirty glob (it now parses only
the dirty files and reconstructs cross-file state from the prior graph,
saving ~0.6–0.9 s per dirty glob). So the sub-second target is met
for the no-content-change steady state and the strategy-parse lever is
landed, but post-process + serialization keep a real edit above sub-second
on a graph this size; see the caveat below.

What changed:

- **No-change is the dominant real case.** The live
  `.weld/auto-refresh.jsonl` showed ~6.5 s even for *zero* changed files,
  because a source that legitimately produces no file-anchored node (an
  issue-store source whose strategy emits abstract concepts) re-triggered
  the full with-changes path on every refresh. The audit now exempts files
  the prior run proved node-less, so an unchanged repo takes the fast
  path (~1.5 s on the first refresh, dominated by the two change-detection
  walks: the discover-source glob walk plus the file-index surface re-hash
  of ≈1.6k files). After that first refresh the graph is fresh, so the very
  next read short-circuits at ~0.6 s.
- **File index incrementalized.** Instead of re-parsing every Python AST
  (~3.7 s here), the refresh re-tokenizes only files whose content hash
  changed since the last write (~0.2 s), driven by the index's own
  surface so doc changes outside `discover.yaml` are still caught. The
  output is byte-identical to a full rebuild.
- **One serialization, not four.** The canonical graph is serialized once
  and the bytes are threaded into the query-state and sqlite sidecar
  writers and the `graph.json` write. The sidecars hash their freshness
  digest over the *volatile-stripped* bytes, so a cold load
  now actually hits the sidecar instead of always missing on the
  `updated_at` delta -- and a byte-identical refresh skips the rebuilds
  entirely.

The genuine-content-change rows are now bounded by re-serializing a 14 MB
canonical `graph.json` (~0.9 s) plus the global query-state rebuild and the
global post-process -- inherent to writing a changed graph of this size. The
per-source language-strategy re-extraction (parsing) lever has landed for both
Python strategies: on a dirty glob, `python_callgraph` parses only its changed
files and reconstructs the `project_modules` origin set from the prior graph
instead of re-parsing every sibling, and `python_module` likewise parses only
the dirty files (it has no cross-file state, so it needs no reconstruction --
a warm ~370 ms/glob saving on this repo). The remaining levers (post-process
scoping, partial/delta
serialization) are tracked separately.

**Glob traversals are shared across one run.** Parsing only the dirty files
still left each strategy *resolving* its glob from scratch. Discovery asks for
one `(root, pattern, excludes)` triple in two places: once when the source-file
resolver builds the file map, and once more inside every strategy that
re-resolves the same glob in its own `extract()`.
`_source_resolve.resolve_source_file_map` already collapses the source entries
sharing a glob, but that memo cannot reach inside a strategy — so this repo's
three sources on `weld/**/*.py` (`python_module`, `python_callgraph`,
`python_package`) cost **four** whole-tree `os.walk` traversals per refresh,
each followed by a per-path repo-boundary filter, for a result that cannot
change mid-run.

`weld.glob_match.glob_scope` memoizes `walk_glob` for the duration of one
operation, and `_discover_single_repo` opens it alongside the repo-boundary
scope. The lifetime rule is the boundary snapshot's, for the same reason: a
glob result is a point-in-time observation of the tree, so it is bound to an
operation and never to the process — a long-lived host (the MCP stdio server,
weld embedded as a library) must not inherit an earlier run's file listing.
Measured here, `weld/**/*.py` drops from 4 traversals to 1 (16 requests → 13
traversals per refresh), worth **~0.5–0.8 s** off a warm content-change
refresh across two interleaved A/B runs (paired-delta medians +0.51 s and
+0.65 s; min-to-min +0.65 s and +0.80 s; best observed refresh 3.23 s → 2.58 s).
Wall-clock on a shared host is noisy, so the load-independent number is the
traversal count. Entries whose excludes differ keep their own traversal — the
memo is keyed on the whole triple.

Determinism is the hard bar: the incrementally-refreshed graph and every
index are byte-identical to a full `wd discover` at the same state. The
standing `//tools:tier_check_determinism_gate_test`, the file-index
equivalence tests (`weld/tests/file_index_incremental_test.py`), the
integration equivalence tests
(`weld/tests/incremental_refresh_equivalence_test.py`), and the
`python_callgraph` provenance-purge regression
(`weld/tests/incremental_callgraph_provenance_purge_test.py` — a
clean caller's `calls` edge into an edited callee's symbol must survive the
purge) gate it at merge.

Reproduce the no-change row in a checkout with a built graph:

```bash
wd discover --output .weld/graph.json   # warm the graph + sidecars once
# time a no-op refresh-on-read (a read auto-refreshes when stale):
time wd query something --json >/dev/null
```

---

## Polyrepo scaling

Each row was produced by:

1. Generating a workspace root containing N nested git repositories of
   1000 files each.
2. Running `wd workspace bootstrap --root <root>`. This initialises the
   root, scans for nested git repos, runs `wd init` and `wd discover`
   in each child, and assembles the federated meta-graph at the root.
3. Running `wd workspace status --root <root>`.
4. Running `wd query helper --limit 5` from inside `<root>` so the
   federation meta-graph is searched.

| Children x files   | Generate | `bootstrap` | `status` | `query` | Root meta `graph.json` |
| ------------------ | -------- | ----------- | -------- | ------- | ---------------------- |
|  5 x 1 000         |   1.99 s |   11.13 s   |  0.05 s  |  0.33 s |       1.9 KiB          |
| 10 x 1 000         |   3.94 s |   22.10 s   |  0.05 s  |  0.33 s |       3.5 KiB          |

Observations:

- **`workspace bootstrap` cost is roughly linear in the number of
  children.** Each child runs its own `wd init` and `wd discover`, so
  the workspace cost is the sum of per-child costs plus the root
  meta-graph step.
- **The root meta-graph stays small.** It carries one `repo:<name>`
  node per child plus cross-repo edges; child entities live in each
  child's own `graph.json` (~3.7 MiB per 1 000-file child). This is
  why federation scales well for many-children workspaces -- the root
  does not duplicate child content.
- **`wd workspace status` and `wd query` at the root are fast** because
  they only touch the small root meta-graph and load child graphs
  on-demand.
- **Limits.** A 10-child polyrepo with 1 000 files per child is a
  healthy scale. Pushing both knobs together -- e.g. 10 children at
  10 000 files each -- multiplies bootstrap cost. Bootstrap parallelism
  is currently per-process; if it becomes a pain point, file an issue
  for federation parallelism rather than trying to work around it.

---

## Reproducing these numbers

To rerun every scenario in this page:

```bash
# 1k single repo
WORK=$(mktemp -d -t weld-bench-1k.XXXXXX)
python -m weld.bench.synthetic_large_repo \
  --layout single --files 1000 --output "$WORK"
time wd init "$WORK"
time wd discover --full --output "$WORK/.weld/graph.json" "$WORK"
( cd "$WORK" && time wd query helper --limit 5 )
rm -rf "$WORK"

# 10k single repo
WORK=$(mktemp -d -t weld-bench-10k.XXXXXX)
python -m weld.bench.synthetic_large_repo \
  --layout single --files 10000 --output "$WORK"
time wd init "$WORK"
time wd discover --full --output "$WORK/.weld/graph.json" "$WORK"
( cd "$WORK" && time wd query helper --limit 5 )
rm -rf "$WORK"

# 100k single repo (slow: tens of seconds for init, minutes for discover)
WORK=$(mktemp -d -t weld-bench-100k.XXXXXX)
python -m weld.bench.synthetic_large_repo \
  --layout single --files 100000 --output "$WORK"
time wd init "$WORK"
time wd discover --full --output "$WORK/.weld/graph.json" "$WORK"
( cd "$WORK" && time wd query helper --limit 5 )
rm -rf "$WORK"

# 100k single repo, git-initialised (matches a real-world git repo,
# faster wd init via the `git ls-files` path)
WORK=$(mktemp -d -t weld-bench-100k-git.XXXXXX)
python -m weld.bench.synthetic_large_repo \
  --layout single --files 100000 --git-init --output "$WORK"
time wd init "$WORK"
time wd discover --full --output "$WORK/.weld/graph.json" "$WORK"
( cd "$WORK" && time wd query helper --limit 5 )
rm -rf "$WORK"

# Polyrepo, 10 children x 1k files
WORK=$(mktemp -d -t weld-bench-poly.XXXXXX)
python -m weld.bench.synthetic_large_repo \
  --layout polyrepo --children 10 --files 1000 --output "$WORK"
time wd workspace bootstrap --root "$WORK"
time wd workspace status --root "$WORK"
( cd "$WORK" && time wd query helper --limit 5 )
rm -rf "$WORK"
```

If you publish numbers from a different environment, please record the
CPU, RAM, OS, Python version, and Weld version alongside them so
readers can compare meaningfully. The reference table above is the
baseline -- not a contract.

---

## Performance defects

Discovered defects should be tracked as separate issues with the
`performance` label rather than inlined here. From the most recent
benchmark run, the linear discovery cost (1k -> 10k -> 100k roughly
8x then 10x) is expected for a per-file parse, but two areas were
filed for follow-up investigation rather than fixed in-line:

- `wd init` scaffold scan cost at 100k files (~50 s). The scanner walks
  the tree to write a sensible `discover.yaml`; sampling or parallel
  walking may help if init becomes interactive on huge monorepos.
- Cold `wd query` on a 357 MiB `graph.json` (~26 s on this machine).
  The graph is parsed eagerly. Memory-mapped or lazy-load JSON
  ingestion, or splitting the graph per namespace, is worth a look.

Both are tracked under the `performance` label. Treat them as scaling
notes, not launch blockers: every scenario in the tables above
completed successfully end-to-end. If a future scenario regresses
meaningfully against this baseline, file an issue and link to the
section above so the comparison is clear.
