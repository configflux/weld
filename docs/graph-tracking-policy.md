# Graph-tracking policy: Mode A vs Mode B

Every repository using Weld makes one choice, usually without noticing:
**is `.weld/graph.json` committed to git, or rebuilt from source?**

Weld has a blessed default and a supported opt-out. This page names both,
says which to pick, and gives the procedure for switching once you have
picked.

| | **Mode A — regenerable graph** | **Mode B — tracked graph** |
|---|---|---|
| How you get it | `wd init` (the default) | `wd init --track-graphs` |
| `.weld/graph.json` in git | no | yes |
| Diff noise | none | the entity lines that changed |
| Merge conflicts | none | resolved by a bundled rule |
| Fresh clone | one cold build, then warm | warm immediately |
| Needs CI | no | no |
| Status | **blessed default** | supported opt-in |

## Mode A — regenerable graph (default)

The graph is a build artifact. It is derived from your source, so git
does not carry it; contributors regenerate it locally and it stays fresh
on its own.

`wd init` writes a `.weld/.gitignore` that tracks the source-of-truth
config (`discover.yaml`, `workspaces.yaml`, `agents.yaml`, `strategies/`,
`adapters/`) and ignores the generated graphs along with per-machine
state. A contributor cloning the repo gets a clean `git status`.

**What it costs.** A fresh clone has no graph, so the first read pays one
full `wd discover`. That is the only cold moment; auto-refresh-on-read
keeps the graph current from then on, rebuilding incrementally as sources
drift. Note that auto-refresh does not build a *missing* graph — it
refreshes an existing one — so the very first read is what pays.

**Optional accelerator.** If you run CI, you can build the graph once
there, publish it as a build artifact, and have contributors fetch it
with [`wd warm`](../README.md#warm-graphs-from-ci-wd-warm) instead of
discovering locally. This is an optimization layered on Mode A, not a
requirement of it — Mode A works with no CI at all, which is the common
case for solo and small-team repos.

**Pick Mode A when** you can run `wd discover` in the environments that
matter. That is almost everyone, and it is why this is the default.

## Mode B — tracked graph (`--track-graphs`)

The graph is committed, so any checkout is warm the moment it exists —
no discovery, no network fetch.

```bash
wd init --track-graphs
wd workspace bootstrap --track-graphs   # polyrepo: root and every child
```

This widens the default policy so the artifacts that make a checkout warm
are tracked alongside config — **each one together with the record that
explains it**:

```
graph.json       + discovery-state.json    what the graph read
agent-graph.json
file-index.json  + file-index-state.json   what the index covers
```

That pairing is the point, not bookkeeping. An artifact shipped without
its record is either believed blindly or disbelieved entirely: a graph
with no inventory arrives in your clone with no account of what it read,
and a file index with no companion has no coverage check at all. Both
records are content-addressed — file hashes, a graph digest, an index
digest — so they stay true in a checkout that did not build them, and
fail closed in one whose sources have moved on. Per-machine state
(`graph-meta.json`, `workspace-state.json`, locks, snapshots) stays
ignored in both modes.

**What it costs.** Mode B is diff noise traded for a warm checkout:

- **Diffs, but only where the graph changed.** The on-disk format is one
  entity per line, so a source change shows up as the node and edge lines
  it touched. It is still a large file; a small change is now a small
  diff of it.
- **Merges resolve by regenerating.** `wd init --track-graphs` writes a
  `.weld/.gitattributes` marking these artifacts `merge=weld-regenerable`
  and registers the driver in your checkout. Two branches that both
  re-discovered are not really in disagreement — they are two renderings
  of two different trees — so the rule keeps the current branch's copy
  and the next weld read rebuilds it from the merged sources.

  Git will not clone merge-driver config (it would let a repository run
  commands on your machine), so **each clone registers it once**:

  ```bash
  git config merge.weld-regenerable.driver true
  ```

  Without that, you get ordinary conflict markers; resolve them with
  `git checkout --ours .weld/graph.json && wd discover`.
- **Nothing stops you committing a stale graph** unless you check. Add
  one line to CI:

  ```bash
  wd stale --check --no-refresh    # exits 1 when the graph is behind source
  ```

**Pick Mode B when** regeneration is not available where the graph is
read — an air-gapped or dependency-restricted environment, a consumer
that cannot run discovery, or a checkout that must answer instantly with
no build step. If regeneration is merely *inconvenient*, prefer Mode A
with a CI artifact.

### Freshness in a Mode B clone

A clone gets the graph and the inventory that explains it, so its first
read is warm: weld can tell that the committed graph accounts for the
committed sources without rebuilding anything, and if it cannot, it
refreshes — incrementally where the clone runs the same weld version, in
full where it does not.

If the inventory is missing — a repository initialised by an older weld,
or one that gitignores it by hand — weld reconstructs a coverage record
from the graph's own contents instead, so a file that is in scope but
missing from the graph is still noticed. That reconstruction is
deliberately conservative: it can only see files the graph drew nodes
from, so a file a strategy read and legitimately skipped (an empty
`__init__.py`, a build file declaring no rules) reads as uncovered. Such
a clone reports stale on its first read and pays one full discovery,
which writes the real record and converges permanently. Over-reporting
costs a single pass; under-reporting would be a wrong answer that never
heals, so the trade is made in that direction on purpose.

## Switching

The managed `.weld/.gitignore` is written once — both `wd init` and
`wd workspace bootstrap` skip it if it already exists — and neither ever
switches an existing file to a *different* mode. (Re-running either does
append lines a template gained after your checkout was initialised, without
touching anything else; that is not a mode switch, since the file's mode
never changes — see "Upgrading from an older weld" below.) So switching to a
different mode always starts by deleting it.

**Mode A → Mode B** (start committing the graph):

```bash
rm .weld/.gitignore
wd init --track-graphs      # also writes .weld/.gitattributes + the driver
wd discover                 # make sure the graph matches HEAD
git add .weld
git commit -m "track the weld graph"
```

`git add .weld` rather than a file list: the artifacts and their records
have to land in the same commit, or the records vouch for a graph nobody
else has.

**Mode B → Mode A** (stop committing it):

```bash
rm .weld/.gitignore .weld/.gitattributes
wd init                     # config-only default
git rm --cached --ignore-unmatch \
    .weld/graph.json .weld/agent-graph.json .weld/discovery-state.json \
    .weld/file-index.json .weld/file-index-state.json .weld/.gitattributes
git add .weld/.gitignore
git commit -m "untrack the weld graph"
```

`git rm --cached` is the load-bearing step: it removes the file from the
index while leaving your working copy in place. Without it the graph
stays tracked no matter what the ignore file says, because git does not
apply ignore rules to files it is already tracking.

`--ignore-unmatch` matters more than it looks. `agent-graph.json` only
exists if you have run `wd agents`, and `git rm` fails the *whole*
command on an unmatched path — so without that flag the step aborts and
leaves `graph.json` tracked, quietly undoing the switch you just made.
Afterwards, confirm with `git ls-files .weld/`: only `discover.yaml`,
`.gitignore` and your other config should remain.

## A third option: ignore everything

```bash
wd init --ignore-all
```

Writes a blanket `*` / `!.gitignore`, so no Weld file is committed at
all — not even `discover.yaml`. This is for trying Weld out in a repo
where you are not ready to commit anything yet. It is not a
graph-tracking policy so much as a way to defer the decision, and it
means every contributor configures discovery themselves.

`--ignore-all` and `--track-graphs` are mutually exclusive; passing both
is a usage error.

## What each mode actually tracks

After `wd init` and one `wd discover`, in a repo with no other ignore
rules:

| File | Mode A | Mode B | What it is |
|---|---|---|---|
| `.weld/discover.yaml` | tracked | tracked | discovery config — source of truth |
| `.weld/.gitignore` | tracked | tracked | the policy itself |
| `.weld/.gitattributes` | — | **tracked** | how a conflict in the above resolves |
| `.weld/graph.json` | ignored | **tracked** | the graph |
| `.weld/discovery-state.json` | ignored | **tracked** | what the graph read |
| `.weld/agent-graph.json` | ignored | **tracked** | AI-customization map |
| `.weld/file-index.json` | ignored | **tracked** | filename index for `wd find` |
| `.weld/file-index-state.json` | ignored | **tracked** | what the index covers |
| `.weld/graph-meta.json` | ignored | ignored | volatile graph metadata |
| `.weld/query_state.bin`, `graph.db` | ignored | ignored | derived query accelerators |
| `.weld/workspace-state.json`, `*.lock` | ignored | ignored | per-machine state |
| `.weld/.enrichment-prompted` | ignored | ignored | first-run enrichment prompt sentinel |

The line falls in one place: Mode A commits **no** generated artifact,
because it can rebuild every one of them; Mode B commits each artifact
**with its record**, because a checkout that cannot rebuild them also
cannot rebuild the account of what they contain.

`graph-meta.json` stays ignored in both modes even though the graph
beside it is tracked in one. It holds the volatile fields — the timestamp
and the commit the graph was built at — which describe *your* run, not
the graph's content. Keeping them out is what makes two discover runs at
the same commit produce a byte-identical `graph.json`.

**Upgrading from an older weld.** The managed `.gitignore` never switches an
existing checkout to a different *mode* on its own, so a repo keeps whatever
mode it was initialised with until you run the "Switching" procedure.
Within the *same* mode, though, re-running `wd init` (or
`wd workspace bootstrap`) now self-heals: if every line already in the file
is one that mode's current template still ships, whatever lines the
template gained since your checkout was initialised are appended — nothing
existing is ever removed, reordered, or rewritten, and a file carrying
anything resync does not recognize (a hand-added line, for instance) is left
alone entirely. That covers the recurring "a new weld sidecar was added to
the template, but my checkout predates the line" class — `file-index.json`,
`auto-refresh.jsonl`, `graph.write.lock`, `telemetry.jsonl` and
`.enrichment-prompted` have each hit it in turn — with no delete-and-recreate
step: just re-run `wd init` (or `wd workspace bootstrap`) and the missing
lines catch up.

That self-heal only runs when you actually re-run `wd init` or `wd workspace
bootstrap`, so a checkout that runs `wd discover` on every change and never
re-runs either gets no signal on its own. `wd doctor` closes that gap
read-only: a `.weld/.gitignore` it can still fully recognize (config-only,
`--track-graphs`, or `--ignore-all`) but that is missing lines its own
template currently ships gets a `[warn]` under `[Config]` naming the missing
lines and pointing at `wd init` as the fix. A file it cannot fully account
for — hand-edited, foreign, or simply absent — stays silent there too, the
same leave-alone posture as the self-heal itself.

Two cases still need fixing by hand:

- A Mode A repo initialised before `file-index.json` was ignored is still
  *committing* it. Re-running `wd init` adds the missing ignore line for
  you, but git keeps tracking a file it already tracks regardless of what
  the ignore file says — untrack it once with
  `git rm --cached .weld/file-index.json` (or use the "Switching" procedure
  above, which does both).
- A Mode B repo has no `.weld/.gitattributes` and no tracked records. Run
  `wd init --track-graphs` in it, then `git add .weld` — the missing
  policy files are written and the records appear on the next
  `wd discover`. Init prints `discover.yaml already exists` and leaves
  that file alone, then confirms the policy is in effect and exits **0**,
  so this is safe in a `set -e` setup script.

If the repository is *not* already Mode B, `wd init --track-graphs` says
so and exits non-zero:

```
/repo/.weld/.gitignore:13:graph.json still ignores the artifacts
--track-graphs asks to track, and weld does not switch an existing ignore
file's mode -- so this repository is not in --track-graphs mode.
Delete /repo/.weld/.gitignore and re-run `wd init --track-graphs` to
switch. The full procedure is under 'Switching' in
docs/graph-tracking-policy.md.
```

That is the case the "Switching" procedure above covers: the managed
ignore file is written once, so the `rm` is what lets the new mode be
written. Init fails rather than leaving you a half-switched checkout
whose `.gitattributes` describes a mode its `.gitignore` contradicts.

### The rule need not be weld's

Whether Mode B is in effect is a question about **git**, not about the one
file weld manages, so weld asks git (`git check-ignore`). That covers the
whole ignore stack — your repository root's `.gitignore`, any `.gitignore`
on the way down, `.git/info/exclude`, and your global `core.excludesFile`
— with git's own precedence rules.

The common case is a repository whose **root** `.gitignore` already
carries `.weld/`, which is what people write before they learn weld ships
its own policy file. That line hides `graph.json`, `discovery-state.json`
and `file-index.json` just as completely as the managed file does, so the
clone that was supposed to arrive warm arrives with nothing. Init names
the file, the line and the pattern, and the remedy is different — that
rule is yours, so weld does not offer to delete it:

```
/repo/.gitignore:2:.weld/ still ignores the artifacts --track-graphs asks
to track, and that rule lives outside weld's managed .weld/.gitignore, so
it is not weld's to rewrite -- so this repository is not in
--track-graphs mode.
Remove or narrow that rule, then re-run `wd init --track-graphs`.
```

Narrowing usually means replacing `.weld/` with the specific per-machine
files you did not want committed; the managed `.weld/.gitignore` weld
writes already lists them.

Two cases deliberately still succeed:

- **`graph.json` is already tracked** under such a rule. Git keeps
  committing a file it already tracks whatever the ignore rules say, so
  that repository *is* in Mode B and is not refused.
- **The directory is not a git checkout.** `wd init` is supported outside
  a repository, so when git cannot be asked, weld falls back to reading
  the managed file as before.

A bare `wd init` under the same root rule is unaffected: no mode flag is
no request, and hiding `.weld/` is what the default policy is for.

## Choosing, in one line

Use Mode A unless something in your environment genuinely cannot run
`wd discover` — then use Mode B and accept the diffs.
