# Determinism Audit — T1a Inventory

- **Status:** Audit complete; fixes deferred to T2 (`the internal audit task`).
- **Date:** 2026-04-14
- **Related issues:**
  - `the internal audit task` — this audit (T1a).
  - `the internal audit task` — regression harness (T1b).
  - `the internal audit task` — fixes (T2).
- **Governing contract:** ADR 0012 (`docs/adrs/0012-determinism-contract.md`).

This inventory catalogs every source of nondeterminism observed in the
`wd discover` pipeline and in each bundled strategy. It is the
authoritative list handed off to T1b (regression harness) and T2 (fixes).

Each finding is tagged with a single category per ADR 0012 §2:

- **ordering** — dict/set iteration, filesystem walk, glob expansion,
  plugin scan, or any in-memory collection ordering that leaks to output.
- **hashing** — use of the Python built-in `hash()` or other hash
  functions whose output varies across runs.
- **subprocess** — locale-sensitive subprocess output (`git`, shell
  utilities) invoked without `LC_ALL=C` / `LANG=C`.
- **timestamps** — time-of-run values (ISO-8601, mtime) that are
  exempt from determinism but must be explicitly enumerated.
- **other** — anything that does not fit the four categories above.

Every finding carries a **scope** tag:

- **base** — exercised by `wd discover` (subject to the contract).
- **enrichment** — exercised only by `wd enrich` (excluded from
  the contract, per ADR 0012 §7; recorded for completeness).

## 1. Summary of findings

| # | Category    | Scope       | Site                                                         | Severity |
|---|-------------|-------------|--------------------------------------------------------------|----------|
| 1 | ordering    | base        | `weld/discover.py` — final `json.dump` omits `sort_keys=True` | high     |
| 2 | ordering    | base        | `weld/discover.py` `_post_process` — node dict iteration order leaks via `{"nodes": nodes}` | high     |
| 3 | ordering    | base        | `weld/discover.py` `_post_process` — edges list order driven by emission order (no sort) | high     |
| 4 | ordering    | base        | `weld/graph.py` `Graph.save` — `json.dump` omits `sort_keys=True` | high     |
| 5 | ordering    | base        | `weld/repo_boundary.py` `iter_repo_files` — `os.walk` dir traversal is filesystem-enumeration order | high     |
| 6 | ordering    | base        | `weld/strategies/cpp_resolver.py` `augment_state_with_headers` — iterates `frozenset` `CPP_HEADER_EXTS` | medium   |
| 7 | ordering    | base        | `weld/file_index.py` `save_file_index` — `json.dump` omits `sort_keys=True` | medium   |
| 8 | ordering    | base        | `weld/discovery_state.py` `save_state` — `json.dumps` omits `sort_keys=True` | medium   |
| 9 | hashing     | base        | No production `hash()` call found — **PASS**, but lint rule is absent (ADR 0012 §5 Risks). | tracking |
| 10| subprocess  | base        | `weld/_git.py` — three `subprocess.run(["git", ...])` calls omit `env={..., "LC_ALL": "C"}` | high     |
| 11| subprocess  | base        | `weld/repo_boundary.py` — two `subprocess.run(["git", ...])` calls omit `env={..., "LC_ALL": "C"}` | high     |
| 12| subprocess  | base        | `weld/discover.py` `_run_external_json` — `subprocess.run(argv, ...)` omits `env={..., "LC_ALL": "C"}` | medium   |
| 13| timestamps  | base (exempt)| `weld/discover.py` `_post_process` — `meta.updated_at` = `datetime.now(UTC)` | exempt (ADR 0012 §1) |
| 14| timestamps  | base (exempt)| `weld/graph.py` `_now` — `meta.updated_at` refreshed on every save | exempt (ADR 0012 §1) |
| 15| timestamps  | base        | `weld/discovery_state.py` `save_state` — `created_at` uses `datetime.now(UTC)` | contained — not in graph.json |
| 16| other       | base (exempt)| Plugin directory scan — `_load_strategy` uses explicit filename lookup, and sources are iterated in declared `discover.yaml` order; no plugin directory iteration. | OK — contract §2 row 6 satisfied |
| 17| other       | enrichment  | `weld/enrich.py` — LLM provider output varies by design. Scope: enrichment. | out of scope (ADR 0012 §7) |

Severity legend:

- **high** — reproducibly causes byte-differences across two discover runs
  on the same source tree or on realistic locales.
- **medium** — can cause byte-differences in worst-case Python versions,
  filesystem types, or `PYTHONHASHSEED=random` environments.
- **tracking** — no active bug today but governance gap (e.g., missing
  lint rule).
- **exempt** — explicitly exempt from the determinism contract.

## 2. Findings in detail

### Finding 1 — `weld/discover.py` `main` omits `sort_keys=True` (ordering, base)

```py
# weld/discover.py, line 386
json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
```

The orchestrator's default emission path writes the graph to stdout
without `sort_keys=True`. Keys at every level of the returned dict
(meta, nodes, edges, per-node props, per-edge props) serialize in
insertion order. CPython 3.7+ makes `dict` ordering deterministic
within a single run, but any sequence of keys produced in different
emission order between refactors (or between CPython 3.11 / 3.12
structural changes) will diff. ADR 0012 §3 rule 4 requires
`sort_keys=True` at the top level.

**Reproduction test:** `weld_determinism_dict_order_test.py` (see §3).

### Finding 2 — `_post_process` node emission order follows strategy order (ordering, base)

```py
# weld/discover.py, _post_process
return {"meta": meta, "nodes": nodes, "edges": deduped}
```

`nodes` is built by successive `nodes.update(r.nodes)` across strategies
(line 322). Every strategy emits its own `dict[str, dict]` whose
iteration order depends on (a) the order the strategy inserted keys and
(b) in several strategies the implicit iteration of an input `set` or
glob result. ADR 0012 §3 rule 1 requires the output nodes to be sorted
by `id`. Today they are not.

**Reproduction test:** `weld_determinism_dict_order_test.py` covers
this alongside Finding 1 — when `sort_keys=True` is added at the top
level, the nested `nodes` dict will serialize sorted, so the fix for
Finding 1 carries this one. Documented as a distinct site so T2 does
not overlook strategy-level ordering if top-level sort is deferred.

### Finding 3 — edges list order driven by emission order (ordering, base)

```py
# weld/discover.py, _post_process
edges = [e for e in edges if e["from"] in nodes and e["to"] in nodes]
seen: set[str] = set()
deduped: list[dict] = []
for e in edges:
    key = f"{e['from']}|{e['to']}|{e['type']}"
    if key not in seen:
        seen.add(key)
        deduped.append(e)
```

Edges are appended to `deduped` in first-seen order. Emission order is
driven by the order in which strategies add to `edges`, which in turn
depends on each strategy's internal ordering (some sorted, some not).
ADR 0012 §3 rule 2 requires edges to be sorted by
`(from, to, type, json.dumps(props, sort_keys=True))`. Today they are
not.

**Reproduction test:** `weld_determinism_dict_order_test.py` asserts
edges emerge in sorted order, which fails today.

### Finding 4 — `Graph.save` omits `sort_keys=True` (ordering, base)

```py
# weld/graph.py, line 45
json.dump(self._data, f, indent=2, ensure_ascii=False)
```

Every write site for `.weld/graph.json` goes through `Graph.save`
when mutating via the CLI (`wd add-node`, `wd import`, etc.).
The same `sort_keys` omission applies. ADR 0012 §3 rule 5 requires a
single canonical form via `json.dumps(..., sort_keys=True, indent=2,
ensure_ascii=False)` — or equivalent.

**Reproduction test:** covered by the same dict-order test.

### Finding 5 — `iter_repo_files` `os.walk` dir order is filesystem order (ordering, base)

```py
# weld/repo_boundary.py, lines 248–272
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [
        d for d in dirnames
        if not _is_dir_name_excluded(d, ...)
    ]
    ...
    for filename in sorted(filenames):
        ...
```

`iter_repo_files` sorts `filenames` but does not sort `dirnames`.
`os.walk` visits subdirectories in the order they appear in
`dirnames`, which it populates from filesystem enumeration
(`scandir`). On ext4 and many overlay filesystems this is directory
hash order — stable within a filesystem generation but not across
filesystems (ext4 vs tmpfs vs FUSE), not across creation-order
changes, and not guaranteed by POSIX. This non-git branch fires when
`wd discover` runs outside a git repo or when `git ls-files`
fails (the `uses_git=True` branch at line 244 sorts `visible_files`
and is fine).

ADR 0012 §2 row 2 requires every walk to be materialized and sorted
before iteration. `dirnames` must be sorted (or reassigned sorted) to
make traversal order a property of the tree, not the filesystem.

**Reproduction test:** `weld_determinism_walk_order_test.py` (see §3).

### Finding 6 — `augment_state_with_headers` iterates `frozenset` (ordering, base)

```py
# weld/strategies/cpp_resolver.py, lines 38–40, 179–180
CPP_HEADER_EXTS: frozenset[str] = frozenset(
    {".h", ".hh", ".hpp", ".hxx", ".ipp", ".tpp", ".inc"}
)
...
for ext in CPP_HEADER_EXTS:
    for hdr in sorted(root_resolved.rglob(f"*{ext}")):
        ...
```

`frozenset` iteration order is determined by hash-mod-bucket assignment
at construction time. String `hash()` is randomized per process by
default (`PYTHONHASHSEED=random`), so `CPP_HEADER_EXTS` iteration order
varies across processes. The inner `sorted(rglob)` is stable, but the
outer `for ext` order affects the order `per_file` grows — and that
list flows into `resolve_includes_pass` where edges are mutated in
place. Any call-site resolution that depends on header-entry order
(e.g., first header to claim a callee wins) becomes nondeterministic.

ADR 0012 §2 row 1 requires all `dict` / `set` emission to ordered
output to go through explicit `sorted()` over canonical keys.

**Reproduction test:** `weld_determinism_frozenset_order_test.py` (see §3).

### Finding 7 — `save_file_index` omits `sort_keys=True` (ordering, base)

```py
# weld/file_index.py, line 190
json.dump(envelope, f, indent=2, ensure_ascii=False)
```

`.weld/file-index.json` writes omit `sort_keys=True`. Although
`iter_repo_files` returns a deterministic sequence, the envelope's
nested dicts still serialize in insertion order and the per-file
`tokens` lists are not sorted. Keys within `meta` (e.g., `version`,
`git_sha`) also emit in insertion order. ADR 0012 is scoped to
`graph.json` today, but file-index.json is a sibling artifact consumed
by the same audience and is worth fixing under the same contract
spirit. T2 may scope this out; the finding is recorded here so it is
not forgotten.

**Reproduction test:** covered indirectly by the dict-order test for
`graph.json`; separate xfail not added (per task scope: 1 test per
category).

### Finding 8 — `save_state` omits `sort_keys=True` (ordering, base)

```py
# weld/discovery_state.py, line 144
json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
```

`.weld/discovery-state.json` is out of scope per ADR 0012 §"What
this ADR does NOT cover" (discovery state files are exempt as long
as they do not leak into `graph.json`). Recorded for completeness
only.

### Finding 9 — `hash()` built-in usage (hashing, base)

Grep `\bhash\(` in `weld/` returns zero matches in production paths.
Current state is compliant with ADR 0012 §5. However, the ADR
explicitly lists "lint automation for the `hashlib`-only rule" as a
follow-up (ADR 0012 §"What this ADR does NOT cover"). The
`discover_twice_identical` harness with `PYTHONHASHSEED=random` would
catch a regression indirectly. A dedicated lint rule is tracked
separately (follow-up issue created; see §4).

**Reproduction test:** `weld_determinism_hash_randomization_test.py`
(see §3) — reproduces the generic effect of `hash()` randomization on
serialization order using a synthetic fixture (the test does **not**
require a stray `hash()` call to exist in weld; it demonstrates the
class of bug the rule exists to prevent).

### Finding 10 — `_git.py` subprocess calls omit `LC_ALL=C` (subprocess, base)

```py
# weld/_git.py, lines 19, 35, 56
subprocess.run(
    ["git", "rev-parse", "HEAD"],
    capture_output=True,
    text=True,
    cwd=str(root),
    timeout=5,
)
```

All three git helpers (`get_git_sha`, `is_git_repo`, `commits_behind`)
inherit the caller's environment. Under `LANG=fr_FR.UTF-8` or similar
locales, git's output reformats date and message strings and may
reorder `ls-files` when the active collation differs from POSIX byte
order. For `rev-parse HEAD` and `rev-list --count A..B`, the reorder
risk is low (output is a SHA or an integer), but translated error
messages leak into stderr and — more importantly — setting `LC_ALL=C`
is the ADR-mandated contract (§2 row 3) regardless of the specific
git subcommand.

**Reproduction test:** `weld_determinism_subprocess_locale_test.py`
(see §3) — demonstrates that a `git log --format=...` output changes
under a different `LANG` without `LC_ALL=C`. The test stages a tiny
synthetic repo, not the weld repo itself, to avoid flakiness.

### Finding 11 — `repo_boundary.py` subprocess calls omit `LC_ALL=C` (subprocess, base)

```py
# weld/repo_boundary.py, lines 93–99, 130–145
subprocess.run(
    ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
    check=False,
    capture_output=True,
    text=True,
)
...
subprocess.run(
    ["git", "-C", str(repo_root), "ls-files", "--cached", "--others",
     "--exclude-standard", "--full-name", "-z"],
    check=False, capture_output=True, text=True,
)
```

`ls-files -z` output uses `\0` separators so locale affects filename
byte order rather than record-separator ambiguity. Paths containing
Unicode characters may collate differently under different `LC_COLLATE`
settings; git emits UTF-8 regardless, but the point is contract
compliance: ADR 0012 §2 row 3 mandates `LC_ALL=C` on every subprocess.
Today these two calls inherit the caller's locale.

**Reproduction test:** covered by finding 10.

### Finding 12 — `_run_external_json` inherits caller locale (subprocess, base)

```py
# weld/discover.py, line 113
proc = subprocess.run(argv, capture_output=True, text=True,
                      cwd=str(root), timeout=timeout)
```

External JSON adapters run arbitrary commands. Their output is parsed
as JSON, so locale effects on the `json.loads` call are nil. However
any tool invoked (e.g., `bazel query`, `cargo metadata`) may reorder
output under a non-POSIX locale. ADR 0012 §2 row 3 applies. Low
severity because external_json is opt-in and rare in practice.

**Reproduction test:** covered by finding 10.

### Finding 13 — `meta.updated_at` timestamp (timestamps, base, exempt)

```py
# weld/discover.py, line 239
"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
```

`meta.updated_at` changes on every run. This field is the only
timestamp explicitly exempt from the determinism contract per ADR 0012
§1. Documented here for completeness.

### Finding 14 — `Graph.save` stamps `updated_at` on every save (timestamps, base, exempt)

```py
# weld/graph.py, line 40
self._data["meta"]["updated_at"] = _now()
```

Same exemption as Finding 13. When a test adopts the
`discover_twice_identical_test` harness, it must strip
`meta.updated_at` before comparison.

### Finding 15 — `discovery-state.json` records `created_at` (timestamps, base)

```py
# weld/discovery_state.py, line 139
state.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
```

`.weld/discovery-state.json` is not `.weld/graph.json` and is
exempt per ADR 0012 "What this ADR does NOT cover". Documented.

### Finding 16 — plugin directory scan is declared-order (ordering, base, compliant)

`_load_strategy` in `weld/discover.py` (lines 43–87) looks up a
single strategy by name — it does not enumerate the strategy directory.
Strategies run in the order they appear in `discover.yaml` (line 320),
so plugin execution order is declared, not enumeration-driven. ADR
0012 §2 row 6 is satisfied today. Documented as a **compliant** item
so T2 reviewers can confirm and close the inventory row.

### Finding 17 — enrichment output (other, enrichment, exempt)

`weld/enrich.py` delegates to LLM providers whose output varies by
model, prompt, temperature, and provider state. Explicitly excluded
from the determinism contract per ADR 0012 §7. Recorded so reviewers
can verify there are no cross-leaks from enrichment back into base
discovery.

## 3. Reproduction tests

For each category at least one reproduction test exists in
`weld/tests/` and is wired into `weld/tests/BUILD.bazel`. Every
test is marked `xfail`-style (expected to fail today; will pass
after T2 lands the fix).

| Category    | Test file                                                 | Coverage notes                                 |
|-------------|-----------------------------------------------------------|------------------------------------------------|
| ordering (dict/set iteration in graph serialization) | `weld_determinism_dict_order_test.py`  | `wd discover` output must have sorted top-level keys, sorted node ids, sorted edges, and `sort_keys=True` semantics at every level. |
| ordering (filesystem walk order)                     | `weld_determinism_walk_order_test.py`  | `iter_repo_files` outside a git repo must return the same list regardless of directory creation order (demonstrated by injecting directories in two different orders). |
| ordering (frozenset / hash-order leak)               | `weld_determinism_frozenset_order_test.py` | `CPP_HEADER_EXTS` iteration order must be deterministic across `PYTHONHASHSEED` values; the test materializes the iteration twice under different seeds and asserts equality. |
| hashing (`hash()` built-in)                          | `weld_determinism_hash_randomization_test.py` | A synthetic fixture demonstrates that Python set iteration order depends on `PYTHONHASHSEED` when strings are hashed. Test asserts `sorted()` is applied so the leak is contained. |
| subprocess (locale-sensitive output)                 | `weld_determinism_subprocess_locale_test.py` | `weld._git.get_git_sha` (and the other helpers) must pass `env={..., 'LC_ALL': 'C'}` so output does not depend on the caller locale. |
| timestamps                                           | n/a — exempt per ADR 0012 §1.             | No reproduction test; exemption documented above. |

Each test uses `unittest.expectedFailure` so it fails cleanly today
and, once T2 lands, converting them to passing tests is a trivial
decorator removal.

## 4. Follow-ups

The following are created as separate bd issues (T2 and beyond do not
absorb them):

- **`hashlib`-only lint rule.** ADR 0012 §"What this ADR does NOT
  cover" already lists this as follow-up. A new bd issue tracks adding
  a `tools/lint_repo` check that greps `weld/` production paths for
  `\bhash\(`. Created: see the worker's `follow_ups` return value.
- **`file-index.json` canonicalization.** ADR 0012 scope is
  `graph.json`; file-index determinism is related but distinct. Created
  as a separate bd issue so T2 can choose whether to bundle it.

## 5. Verification protocol (for T2 reviewer)

1. Run `bazel test //weld/...` on the audit branch; confirm no
   previously-passing test now fails.
2. Run each new `weld_determinism_*_test` target individually with
   `bazel test`; confirm each fails predictably (they are marked
   `expectedFailure`, so a "FAIL" is a pass for xfail semantics).
3. Re-run the same tests after T2 lands with
   `expectedFailure` removed; all should pass.
4. Verify the inventory tables above are checked off row-by-row in the
   T2 diff description.

## 6. Changelog

- **2026-04-14** — Audit complete. Inventory signed off for handoff
  to T1b and T2.
