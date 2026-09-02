# Weld

<!-- markdownlint-disable-next-line MD013 -->
[![CI](https://github.com/configflux/weld/actions/workflows/ci.yml/badge.svg)](https://github.com/configflux/weld/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/configflux-weld.svg)](https://pypi.org/project/configflux-weld/) [![Python versions](https://img.shields.io/pypi/pyversions/configflux-weld.svg)](https://pypi.org/project/configflux-weld/) [![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

A local codebase graph for AI coding agents. Weld scans code, docs, CI, build
files, runtime configs, and repo boundaries into a deterministic graph. Agents
can query this graph through CLI or MCP instead of rediscovering the repository
from scratch every session.

The graph lives on disk (`.weld/graph.json`), stays under your control, and
answers the questions agents and humans repeatedly ask about a codebase: where
a capability lives, which docs are authoritative, what build and test surfaces
a change touches, and what boundaries constrain the implementation.

<!-- evaluator-note: latest=v0.26.0 -->
> **Evaluators: start with v0.26.0.** v0.26.0 is the current
> recommended starting point. New in this release over the previous one:
> Next.js app-router routes; a TypeScript/JavaScript surface on which
> `wd callers` and `wd impact` answer at function granularity (first-party
> imports and `tsconfig` aliases bound to their files, JSX components,
> default exports, barrels, CommonJS); npm and MSBuild packages in the
> cross-repo package graph; `wd init --refresh` merging newly-wired
> entries into a hand-edited config; and directory-segment globs and
> brace groups resolving the same way in every strategy.
> [`CHANGELOG.md`](CHANGELOG.md) has the full per-release entries.

**Try it in 5 minutes →** [docs/tutorial-5-minutes.md](docs/tutorial-5-minutes.md)
walks through `wd init`, `discover`, `brief`, `query`, `context`, and `path`
against demo workspaces. Spin up a clean demo with one command:

```bash
scripts/create-polyrepo-demo.sh /tmp/weld-polyrepo-demo
# or
scripts/create-monorepo-demo.sh /tmp/weld-monorepo-demo
```

Each script materializes a self-contained demo directory with seeded source
files, `.weld` configs, and committed git history -- ready for `wd discover`.
If you have Weld installed but no source checkout, the same demos are
available through the CLI: `wd demo list`, `wd demo monorepo --init <dir>`,
`wd demo polyrepo --init <dir>`.

## Use Weld when…

- your repo is too large for an agent to understand in one pass
- your system spans multiple repositories
- architecture is spread across code, docs, CI, configs, and service contracts
- you want reproducible repo context instead of ad-hoc chat memory

## When not to use Weld

- **Your repo is small (under ~50 files).** An agent can read it end-to-end;
  a graph adds overhead without payoff.
- **`grep` plus your IDE already answers your questions.** If nothing is
  missing from that workflow, Weld has nothing to add.
- **You only need symbol navigation.** Go-to-definition and find-references
  are an LSP job. Weld covers architecture, contracts, docs, and CI -- not
  IDE jump-to.
- **You expect compiler-grade static analysis.** Weld is a pragmatic graph,
  not a type checker or dataflow engine. It will not catch every reference
  or prove correctness.
- **You do not want repo-local configuration.** Weld writes config to
  `.weld/` (`discover.yaml`, `workspaces.yaml`, `strategies/`) and
  expects that **config** to be committed alongside your code. Generated
  graphs (`graph.json`, `agent-graph.json`) are gitignored by default;
  the opt-in `wd init --track-graphs` team workflow commits them for
  warm-CI / warm-MCP setups instead. If even committing config is
  unacceptable, Weld is the wrong tool.

## How Weld compares

Weld is not a replacement for the tools below -- it sits alongside them and
gives agents a persistent, queryable map of the repository. Each of these
tools is excellent at what it does; Weld adds the connected structure they
were not designed to provide.

| Tool | Gives you | Weld adds |
|---|---|---|
| grep / ripgrep | Fast literal and regex search over file contents. | Typed nodes and edges -- a symbol, route, doc, or config is an addressable entity with neighbours, not a line of text. |
| ctags / LSP | Symbol navigation and go-to-definition inside one language. | A cross-language graph that also covers docs, CI, configs, service contracts, and repo boundaries -- surfaces an IDE was never meant to index. |
| Sourcegraph | Hosted code search and references across large fleets of repos. | A local, repo-local graph that lives next to your code. By default Weld tracks only config and lets you opt in (`wd init --track-graphs`) to commit the generated graph for warm-CI / warm-MCP setups. No server, no indexing fleet; agents query it offline through CLI or MCP. |
| vector DB / RAG | Embedding-based semantic recall over chunks of text. | Deterministic structure. Query results are exact nodes and edges with provenance, not top-k fuzzy matches, so agents can follow relationships instead of guessing. |
| Copilot / Claude Code / OpenCode | In-editor and agentic code generation and chat. | Shared repo context those agents can read through MCP -- the same graph across sessions and tools, instead of each agent rediscovering the repo on every run. |

## Key features

- **Whole-codebase discovery** — not just source code. Covers docs, config,
  CI workflows, infrastructure, and build files.
- **Startup and runtime flow** — models common Python, C#/.NET, and C++ entrypoints
  and connects them to services, boundaries, and deploy/runtime surfaces.
- **Config-driven** — point `.weld/discover.yaml` at your repo and tune
  what gets extracted.
- **Multi-language** — tree-sitter strategies ship for Python, TypeScript/JS,
  Go, Rust, C#, C++, Java, and ROS2. **Tree-sitter Python packages are an
  optional extra** (`pip install configflux-weld[tree-sitter]`); without
  them only Python is extracted natively. See
  [Supported languages](#supported-languages) for the per-language status
  and the optional libclang path for C++.
- **Plugin architecture** — drop a `.py` file in `.weld/strategies/` to
  extract anything repo-specific.
- **Agent Graph** — discover agents, skills, prompts, commands, hooks,
  instructions, MCP servers, and platform-specific copies into
  `.weld/agent-graph.json`; see the
  [Agent Graph guide](docs/agent-graph.md) for node and edge types,
  authority/drift, and limitations, and the
  [platform support matrix](docs/platform-support.md) for tested surfaces.
- **Agent-native** — generates MCP config snippets by default and ships an
  optional stdio MCP server so Claude Code, Codex, and other agents can query
  the graph directly.
- **Worktree-aware** — every read answers from the checkout you are standing
  in, and a fresh `git worktree add` seeds its graph from a sibling checkout
  on the first query instead of paying a cold discovery. See
  [Worktrees and multiple checkouts](#worktrees-and-multiple-checkouts).
- **Zero external dependencies** — runs from a plain checkout with Python >= 3.10.
  Tree-sitter is optional.

## Quickstart

```bash
# Install (recommended — see the Install section for alternatives)
uv tool install configflux-weld

# Bootstrap config for your repo
wd init

# Run discovery and save the graph (safe mode by default — see Trust model below)
wd discover --safe --output .weld/graph.json

# Query the graph
wd query "authentication"
wd trace "how does this service start"
wd find "login"
wd context file:src/auth/handler
wd viz --no-open
wd stale
```

Drop `--safe` once you trust the repository's project-local strategies and
external-JSON adapters; the [Trust model](#trust-model) section below explains
what `--safe` disables and when it is appropriate to remove.

Try it on a real example:
[examples/04-monorepo-typescript](examples/04-monorepo-typescript/) (monorepo) ·
[examples/05-polyrepo](examples/05-polyrepo/) (polyrepo federation).

Sample output (`wd query "auth"` — default human form, trimmed):

```text
# query: auth
  matches (1):
    1. symbol:src/auth/handler.py:authenticate  [type: function]
       label: authenticate
       confidence: definite
       description: Validate a bearer token and return the caller identity.
  neighbors (1):
    - route:/login  [type: route]
```

Each match shows its `confidence` (`definite` / `inferred` / `speculative`)
so an agent can weight strong hits over guesses. By default `wd query` also
hides unresolved-symbol sentinels (call-graph callees that could not be
linked to a definition, `origin=unresolved`) — they are noise in the result
set. Pass `--include-speculative` to bring them back. The `--json` envelope
applies the same default filter, and so does the MCP `weld_query` tool
(`include_speculative: true` restores them there) — the two surfaces return the
same matches by construction. Every match still carries its `confidence` so a
client can weight or discount hits itself.

The `description:` line above prefers `props.description` (an LLM enrichment
pass) whenever a match has one. A match with no enrichment yet — the common
case, since enrichment is opt-in — falls back to `props.summary` instead: a
file's or symbol's own opening doc-comment line (a file node reads its module
docstring — a Python test module's own docstring included, not just the
production code it covers; a symbol node reads its own function/class
docstring or, for Go and Rust, its own `//`/`///` doc comment), populated by
`wd discover` with no enrichment pass required, and rendered as
`summary: ...` so the two are never confused. `wd context`'s node header, and
the match blocks behind `wd callers` / `wd references`, apply the same
precedence. `--json` and the MCP surfaces are unaffected — both fields are
always present in `props` already.

`wd query` and `wd context` (and their MCP peers `weld_query` / `weld_context`)
also **bound the read envelope** by default so a large graph stays usable on
both surfaces. First the neighborhood is dieted: neighbors that are stdlib
symbols, unresolved sentinels, or speculative external symbols are dropped (real
external *package* dependencies are kept), dangling edges are removed, and the
fan-out is capped so a hub node cannot blow up the envelope. Then a deterministic
**byte budget** prunes the lowest-priority survivors until the serialized
envelope fits the agent tool cap. Nothing is hidden silently — the `--json`
envelope reports `neighbors_filtered: true` and an `omitted_neighbors` count by
reason (`stdlib`, `unresolved`, `external_symbol`, `fanout_capped`, and
`size_capped` for the byte budget). Pass `--full-neighborhood` (CLI) or
`full_neighborhood: true` (MCP) to restore the full, unfiltered neighborhood, or
`--full-size` / `full_size: true` to keep the diet but skip the byte budget.

`wd brief` / `weld_brief` are bounded the same way: edges are de-dangled to the
nodes the brief actually emits and the byte budget applies (a `warnings` entry
records any node dropped for size); `--full-size` / `full_size: true` returns the
unbounded brief.

The four **traversal reads** — `wd impact`, `wd callers`, `wd references`, and
`wd trace` (and their MCP peers) — share the same budget, because a blast radius
or a caller list is exactly the answer that grows without limit on the nodes
worth asking about. Each reports what it dropped rather than truncating quietly:

| Read | Dropped first | Reported in |
|---|---|---|
| `impact` | farthest hops, then lowest-priority nodes | `warnings.size_capped` + a `warnings.messages` entry |
| `callers` | lowest-priority callers | `size_capped` |
| `references` | file hits, then callers (resolved `matches` last) | `size_capped` |
| `trace` | lowest-priority slice nodes | a `warnings` entry |

`--full-size` / `full_size: true` returns the unbounded payload on every one of
them.

What the budget may **never** shrink is the verdict. `wd impact` always reports
`risk_level` and `affected_surface_counts` — per-bucket counts of everything the
radius reaches — over the **full** blast radius, so a bounded payload can never
come back claiming a smaller radius or a lower risk than the change really has.
The surface *member lists* under `affected_surfaces` are prunable (they are seven
unbounded node lists, not a summary), and what was dropped from each is reported
in `warnings.size_capped.affected_surfaces`. The human (non-`--json`) output is
never bounded — a terminal has no tool cap, and the counts are what a reader ran
the command for.

The budget is best-effort, and says so when it falls short: if everything
droppable has been dropped and the payload is *still* over, `warnings`
(top-level `budget_exceeded` on `callers` / `references`) carries
`budget_exceeded: true` plus a message suggesting a narrower question. It is
always present, so a consumer never has to probe for it.

All `wd` retrieval commands default to human-readable text and accept
`--json` for the stable JSON envelope. Pass `--json` when
piping to `jq` or other scripted consumers. `query` and `context`
additionally carry the neighbor-diet annotation (`neighbors_filtered` +
`omitted_neighbors`); the rest of the envelope is unchanged.
Sample `wd query "auth" --json`:

```json
{
  "query": "auth",
  "matches": [
    {
      "id": "symbol:src/auth/handler.py:authenticate",
      "label": "authenticate",
      "type": "function",
      "props": {
        "file": "src/auth/handler.py",
        "exports": ["authenticate"],
        "description": "Validate a bearer token and return the caller identity."
      }
    }
  ],
  "neighbors": [{"id": "route:/login", "type": "route"}],
  "edges": [
    {"from": "route:/login", "to": "symbol:src/auth/handler.py:authenticate", "type": "calls"}
  ],
  "neighbors_filtered": true,
  "omitted_neighbors": {"stdlib": 0, "unresolved": 0, "external_symbol": 0, "fanout_capped": 0, "size_capped": 0}
}
```

See [Install](#install) for alternatives (local checkout, pip, raw source).

### Agent Graph for AI customizations

Weld also maps the AI customization layer around a repository: agents, skills,
instructions, prompts, commands, hooks, MCP servers, tool permissions, and
platform variants. The Agent Graph is static and repo-bound; discovery reads
known customization files and does not execute project code.

```bash
wd agents discover
wd agents list
wd agents audit
wd agents explain planner
wd agents impact .github/agents/planner.agent.md
wd agents plan-change "planner should always include test strategy"
wd agents viz --no-open
```

Use `--json` on `list`, `explain`, `impact`, `audit`, and `plan-change` for
agent-friendly output. Use `wd agents rediscover` when you want an explicit
refresh of `.weld/agent-graph.json` before inspecting the persisted graph.
Use `wd agents viz` after discovery to open a local read-only browser explorer
for the persisted Agent Graph.
Static discovery and configuration generation are available for several
agent platforms; runtime validation is tracked per client in the
[platform support matrix](docs/platform-support.md). The
[Agent Graph guide](docs/agent-graph.md) documents node and edge types,
authority and drift, and the read-only-first policy.

### Agent-first onboarding

If an agent or coding assistant is driving setup, use the short bootstrap
path:

```bash
uv tool install configflux-weld   # recommended — see Install for alternatives
wd prime                  # show setup status + per-framework surface matrix
wd bootstrap claude       # writes .claude/commands/weld.md
wd bootstrap codex        # writes .codex/skills/weld/SKILL.md + .codex/config.toml
wd bootstrap copilot      # writes .github/skills/weld/SKILL.md + .github/instructions/weld.instructions.md
wd bootstrap cursor       # writes .cursor/rules/weld.mdc + .cursor/mcp.json
wd bootstrap aider        # writes CONVENTIONS.md + .aider.conf.yml (wiki fallback; no MCP)
wd bootstrap gemini-cli   # writes .gemini/skills/weld.md + .gemini/mcp.json
wd bootstrap copilot-cli  # writes .copilot/skills/weld.md + .copilot/config.json
```

Cursor, Gemini CLI, and Copilot CLI register the local weld stdio MCP server
in the host-native config file. Aider has no native MCP protocol, so its
`CONVENTIONS.md` stanza points at the agent-readable wiki export: run
`wd export --format=wiki --output=.weld/wiki` and read `.weld/wiki/index.md`
to navigate the graph.

All seven `wd bootstrap` frameworks accept opt-out flags:

- `--no-mcp` — skip the MCP pair (`.codex/config.toml` for codex; the `.mcp.json` guidance block for copilot/claude).
- `--no-enrich` — write the `.cli.md` variant that omits `wd enrich`.
- `--cli-only` — shorthand for `--no-mcp --no-enrich`.

To upgrade existing bootstrap files after pulling a new weld release, use
the diff-aware upgrade path:

- `wd bootstrap <framework> --diff` — print unified diffs between bundled
  templates and your on-disk copies without writing. Exits 1 when any
  file differs, 0 otherwise, so it composes with CI checks.
- `wd bootstrap <framework> --force` — overwrite targeted files while
  still honouring the opt-out (`--no-mcp`, `--no-enrich`, `--cli-only`)
  and federation template behaviour.

`wd prime` is idempotent and safe to re-run — it reports what is
already configured and what is still missing. Pass
`--agent {auto,claude,codex,copilot,all}` to force the active agent's row
into the matrix even when that framework has no files yet (e.g. a Codex user
in a Claude-only checkout sees `codex: skill no, mcp no -> wd bootstrap codex`
instead of silence). `auto` is the default and infers the agent from
environment variables such as `CODEX_*`.

## Trust model

Weld's trust posture is explicit and narrow:

- **Default**: bundled discovery reads source files and writes the local
  graph (`.weld/graph.json`). It does not execute discovered application
  code and does not open network connections.
- **Safe mode**: when enabled with `--safe`, safe mode disables
  project-local strategies (`.weld/strategies/`) and the `external_json`
  adapter for `wd discover`, and refuses network/LLM enrichment providers
  for `wd enrich`. Pass `wd discover --safe` to scan an untrusted
  repository without executing any code from it; pass `wd enrich --safe`
  to refuse network egress (every currently registered provider —
  Anthropic, OpenAI, Ollama, Copilot CLI — is refused). Safe mode produces a stable
  `[weld] safe mode: ...` stderr line for each refused path. Enrichment
  is still available under `--safe` via `wd enrich --agent-direct`, which
  makes no network call at all.
- **Launch form**: the claim above is measured against `wd`, the
  recommended surface. A console script's own directory heads Python's
  module search path, so the repository being scanned is never on that
  path and nothing in it can answer an import. The raw-source path
  `python -m weld` cannot reach zero: `-m` puts the working directory
  ahead of the standard library. Weld removes that entry before importing
  anything of its own, but CPython's `-m` bootstrap has already imported a
  handful of standard-library modules — eight on CPython 3.12, among them
  `collections`, `threading` and `warnings`, with the exact set varying by
  interpreter version — before any weld code runs, so a repository holding
  files by those names still gets them executed. That floor is what every
  `-m` target pays, weld's or the standard library's. Use `wd`, or
  `PYTHONSAFEPATH=1 python -m weld` (Python 3.11+), against a wholly
  untrusted repository.
- **Advanced strategies**: project-local strategies are Python modules
  loaded at discovery time, and `strategy: external_json` executes
  configured commands from `discover.yaml`. Only enable these on
  repositories you trust.
- **MCP server**: the stdio server is launched with `python -m`, so it is
  bound by exactly the **Launch form** limit above and by nothing worse.
  Clients start it with the project directory as its working directory,
  which `-m` would put ahead of the standard library on Python's module
  search path; the server removes that entry before it loads anything of
  its own, leaving the same interpreter-imposed floor and nothing above it.
  Launch it with `PYTHONSAFEPATH=1` (Python 3.11+) in its environment to
  remove the floor too. See [docs/mcp.md](docs/mcp.md#trust-model) for the
  exact boundary and the configuration snippet.

See [SECURITY.md](SECURITY.md) for the full policy and reporting process.

## Local telemetry

Weld records the success or failure of every `wd` CLI invocation and every
MCP tool call to a local-only file. There is no remote endpoint and no
upload — the file never leaves your machine unless you explicitly export
and share it.

**What is recorded.** Each event is one JSON line with a strict allowlist
of fields: subcommand or tool name, exit code, duration in milliseconds,
and the exception class name on failure. Paths, query strings, error
messages, flag values, and usernames are never recorded. The redaction is
enforced at write time, so the file on disk is already safe to attach to a
bug report.

**Where it lives.** In a single repo, the file is
`<repo>/.weld/telemetry.jsonl`. In a polyrepo workspace, every event from
the root and from any child repo aggregates into
`<workspace_root>/.weld/telemetry.jsonl` — one shareable artifact per
workspace. Invocations outside any project (for example `wd --version` in
`/tmp`) fall back to `${XDG_STATE_HOME:-~/.local/state}/weld/telemetry.jsonl`.
The file is gitignored and rotates at 1 MiB to keep the trailing 500 events.

**How to opt out.** Any one of these disables recording:
`WELD_TELEMETRY=off` in the environment, the `--no-telemetry` flag on a
single invocation, or `wd telemetry disable` to write a persistent sentinel
at the resolved root. Run `wd telemetry --help` for the full subcommand
surface (`status`, `show`, `path`, `export`, `clear`, `disable`, `enable`),
and see [`docs/telemetry.md`](docs/telemetry.md) for the full event
schema and design rationale.

## Supported languages

Weld's only built-in extractor is for Python. **Every other language
listed below depends on the `[tree-sitter]` optional extra.** Without
it, the tree-sitter strategies silently no-op on ImportError and the
graph will contain zero nodes for those languages — by design, so weld
still runs in a minimal environment. Install the extra to actually use
multi-language support:

```bash
uv tool install "configflux-weld[tree-sitter]"
# or
pip install "configflux-weld[tree-sitter]"
```

**Status ladder.** Every language is classified on a single ladder:
**Tier 1** (passes the binding tier-check harness criteria on the
pinned corpora; description-coverage is measured and reported as an
advisory signal rather than a gate, because enrichment quality reflects
LLM provider output rather than weld discovery) → **Tier 2** (ships and
is usable; fails one or more binding criteria with disclosed gaps) →
**Preview** (ships with documented correctness issues; not for
production use) → **Experimental** (opt-in extra, off by default) →
**Not supported**. Languages move tiers only via tier-check harness
output, not by editorial claim. The per-language Status column below is
generated from the harness baselines, so it always reflects the current
verdict; a language without a recorded baseline keeps its listed status
pending its own harness run.

<!-- LANG-TABLE:BEGIN -->
| Language | Extraction surface | Grammar package | Status |
|---|---|---|---|
| Python | modules, classes, functions, imports, call graph | built-in (no extra) | **Tier 1** |
| TypeScript | exports, re-exports, classes, imports, best-effort call graph (`.ts` and `.tsx`) | `tree-sitter-typescript` | **Tier 1** |
| JavaScript | functions, classes, exports (ESM and CommonJS `module.exports`), imports (`import` and `require`) | `tree-sitter-javascript` | Tier 2 |
| Go | exports, types, imports | `tree-sitter-go` | **Tier 1** |
| Rust | exports, types, imports | `tree-sitter-rust` | **Tier 1** |
| C# | types, methods, properties, attributes, namespaces, using dependencies, best-effort call graph | `tree-sitter-c-sharp` | **Tier 1** |
| C++ | classes, structs, namespaces, functions, methods, inherits edges, includes, CMake build targets, best-effort call graph | `tree-sitter-cpp` | **Tier 1** |
| Java | classes, interfaces, methods, fields, constructors, annotations, imports, inherits / implements edges | `tree-sitter-java` | **Tier 1** |
<!-- LANG-TABLE:END -->

**Frameworks** (reuse a language's extractor; status inherits from the
host language):

| Framework | Host language | Extraction surface | Status |
|---|---|---|---|
| ROS2 | C++ / Python | packages, nodes, topics, services, actions, parameters | Preview |
| DDS (CycloneDDS / FastDDS) | IDL (`.idl`) | data contracts (structs) with typed fields, enums, pub/sub topic channels | Preview |

Discovery also adds deterministic closure edges from files to source-backed
symbols and from import/include/use declarations to local files or external
package nodes across every listed language.

For non-preview tree-sitter languages, exact identifier queries such as
`wd query GetAsync` prefer first-class definition `symbol:` nodes before
owning files or package-level fallbacks. File results remain available when
the graph has no exact symbol candidate.

### TypeScript / JavaScript — first-party imports

In a monorepo, the name a file imports is often not a package. Weld
resolves two such spellings to the file that defines them, so a
workspace-internal dependency is an edge between your own files rather
than a `package:` node claiming the code is external:

- **npm workspace member names.** The root `package.json`'s `workspaces`
  globs (npm's array form and yarn's `{"packages": [...]}` form) name the
  member directories; each member's own `name` is then a first-party
  spelling. `import { formatPrice } from "@acme/shared"` binds to the
  file that member's `exports["."]`, `types`, `main` or conventional
  `index` entry point points at — including when `main` names a build
  output that a source checkout has not produced. Sub-path imports
  (`@acme/shared/money`) resolve against the member directory.
- **`tsconfig` path aliases.** `compilerOptions.baseUrl` and
  `compilerOptions.paths` from the nearest `tsconfig.json` (or
  `jsconfig.json`) at or above the importing file. `@/lib/greeting`
  resolves the way that app's own compiler resolves it, so two apps in
  one repo may give the same alias two different meanings. Comments and
  trailing commas in the config are tolerated. `extends` chains are not
  followed: aliases declared only in a shared base config are not
  resolved.

Both maps are read once per `wd discover`. The resolved target is
recorded on the importing file node as `props.import_targets`, and the
`depends_on` edge carries `resolution: first_party`. Nothing else about
an import changes: a genuine dependency still mints its `package:` node
with the origin classification it always had.

### TypeScript / JavaScript — who calls this function

A TypeScript source entry written with `emit_calls: true` (which `wd init`
writes) records a `calls` edge per call site. Two readings make those edges
answerable rather than merely present:

- **The caller is the export the call sits inside.** A call in
  `export async function GET()` is attributed to `GET`, and one in
  `export const handler = () => …` to `handler`, so `wd callers` and
  `wd impact` answer at function granularity. A call in a non-exported
  helper, or in an anonymous callback at module level, stays attributed to
  its file: weld names a symbol only where it extracted one.
- **The callee is the definition its import names.** A callee whose name
  arrived through a named import binds to the exported symbol behind that
  import — directly when the imported module defines it, and, when the
  module is a package entry point that defines nothing of its own (the
  `index.ts` barrel shape), to the single definition of that name inside
  that package. Two candidates is an ambiguity rather than a coin flip, and
  the call stays unresolved.

What weld cannot bind stays visible as an unresolved callee instead of
quietly disappearing: a third-party function, a method called on a value,
and a name the package does not define all keep that state — so "we cannot
say" reads differently from "nothing calls this".

### TypeScript — barrel files

A barrel — an `index.ts` whose whole content is `export { x } from
"./money"` — is what a package's `main` points at, so it is where reads
that arrive through the package name land. Weld records what it forwards:
the names on the file node's `props.reexports`, and the module it forwards
them from in `props.imports_from`, which resolves to a `depends_on` edge on
the defining file like any other import. `export * from "./money"` records
the dependency without the names, which is all that form states.

Re-exported names are kept out of `props.exports` on purpose. That list is
what becomes `symbol:` definition nodes, and a barrel defines nothing — so
`wd callers` and `wd impact` on `formatPrice` still answer about the one
module that declares it, not about every barrel that republishes it.

### C++ — Tier 1 details

**Status: Tier 1.** The C++ extraction surface passes the binding
tier-check harness criteria against the pinned C++ corpora
(`nlohmann/json`, `googletest`, `abseil-cpp`, `Kitware/CMake`,
`grpc/grpc`); see [docs/bench/tier1-cpp-baseline.md](docs/bench/tier1-cpp-baseline.md)
for the per-criterion measurement snapshot. Promotion is anchored by
the bundled fixture contract gate, which exercises a Shape / Circle
/ Rectangle / Drawable inheritance tree under a real CMake project
layout.

C++ has two extraction paths:

1. **Tree-sitter** (default once `[tree-sitter]` is installed).
   Indexes `.hpp`, `.cpp`, `.cc`, `.h`, `.hh`, `.hxx`, `.cxx`,
   `.ipp`, `.tpp` files into `file:` and `symbol:` nodes. Emits
   `inherits` edges originating at the derived-class symbol (so a
   `wd context` on a concrete class surfaces its base classes
   directly, not via the owning file). Query patterns live in
   [weld/languages/cpp.yaml](weld/languages/cpp.yaml). This is the
   fast path; no compilation database required and the path the
   tier-check harness measures.

2. **libclang** (optional, off by default). Adds macro-expansion,
   template-instantiation, and cross-translation-unit call edges
   that tree-sitter cannot resolve from a syntactic walk alone.
   Requires:
   - `pip install "configflux-weld[cpp-libclang]"` (Python bindings)
   - A `compile_commands.json` at the repo root, e.g.
     `cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON .`
   - `WELD_CPP_LIBCLANG=1` in the environment that runs `wd discover`

   When any prerequisite is missing the libclang strategy silently
   returns no nodes — tree-sitter still runs.

A CMake build-graph strategy (`cpp_cmake`) parses each
`CMakeLists.txt` and emits `project:`, `build-target:`, and
`package:` nodes plus `depends_on` edges so internal target
dependencies (`target_link_libraries`) and `find_package` declarations
are queryable as first-class graph entries.

**Framework markers.** The C++ framework strategies declare
`ros2`, `cmake`, `conan`, `gtest`, and `catch2` markers; the
tier-check harness reports them stub-by-design when a corpus is a
plain library that does not *consume* a C++ test or robotics
framework in its public surface. Corpora that do consume them
(downstream applications, services, ROS2 packages) light up
criterion 3 directly. If you adopt C++ support and measure it
against your own corpus, please share the numbers — public
measurements are the fastest way to keep the harness honest.

To use the built-in semantic enrichment providers:

```bash
uv tool install "configflux-weld[openai]"     # or [anthropic], [ollama], or [llm]
```

The `copilot-cli` provider needs no Python extra — install the standalone
GitHub Copilot CLI binary (`copilot`) and run
`wd enrich --provider copilot-cli`. Set `WELD_COPILOT_BINARY` to override
the binary path.

### First-run enrichment prompt

When you run `wd discover` for the first time and weld detects an
enrichment provider through the usual environment variables (the
Anthropic API key, the OpenAI API key, an `OLLAMA_HOST` value, or
the `copilot` binary on `PATH`), discover prints a cost-honest
prompt with the estimated dollar range and asks whether to run
enrichment now. The answer is persisted to
`.weld/.enrichment-prompted` and the prompt is not re-shown.

- `wd discover --no-enrich` skips the prompt for one invocation.
- `WELD_NO_ENRICH=1` skips it globally (CI-friendly).
- `wd discover --safe` implies skip (network/LLM calls forbidden).
- `wd enrich --reset-prompt` clears `.weld/.enrichment-prompted` so
  the next `wd discover` re-asks (useful after configuring a
  provider for the first time).

Graphs over 2,000 nodes are out of the auto-flow: the message points
you at the explicit `wd enrich --batch=N` path instead. Inside an
agent harness (Claude Code, Cursor, Codex, etc.) with no provider
configured, the prompt is replaced by a tip to run
`wd enrich --agent-direct`.

For a source-checkout install (contributors editing Weld itself), see
[CONTRIBUTING.md](CONTRIBUTING.md).

### Enrichment without an API key (`--agent-direct`)

If you are an AI agent, you *are* an enrichment provider — you can read the
source and judge it yourself. `wd enrich --agent-direct` prints the work plan
for exactly that: the nodes still missing enrichment, the record contract a
write must satisfy, the command that lands one, and how to verify the result.
It calls no provider, needs no extra, no API key, and no network, and it never
writes to the graph:

```bash
wd enrich --agent-direct                      # the full plan
wd enrich --agent-direct --type entity --limit 25   # one batch
wd enrich --agent-direct --json               # the same plan as data
```

`--limit` caps the listed nodes but still reports how many were left out, so a
batched run never mistakes a truncated list for a finished graph. `--json`
emits the pending list, the record contract, and the command template as a
payload a harness can drive batches from. `--force` lists nodes that already
carry enrichment, for a deliberate re-run. Because the mode makes no network
call, it is also the one enrichment path `wd enrich --safe` permits.

Following the plan means reading the source and writing the record back:

```bash
wd stale
wd context "<node-id>"
wd add-node "<node-id>" --type "<node-type>" --label "<label>" --merge --props '{"description":"...","purpose":"...","enrichment":{"provider":"manual","model":"agent-reviewed","timestamp":"<ISO-8601 UTC timestamp>","description":"...","purpose":"...","suggested_tags":["lowercase","tags"]}}'
wd graph validate
wd graph stats
wd graph communities --format markdown
```

Manual enrichment writes `.weld/graph.json` directly and is preserved across
later `wd discover` runs: discovery re-attaches `props.enrichment` to the
rebuilt node, keyed by node id. A record written by `wd enrich` is re-validated
against a node source fingerprint and dropped only when that node's own source
changes; manual enrichment carries no fingerprint, so it persists until you
re-enrich it.

That fingerprint covers a node's *identity*, not its position. Inserting a
line above a function shifts everything below it, and none of those nodes lose
their enrichment; a file node likewise keeps its enrichment when the file
merely grows, and loses it when its exports, constants, or imports change.
Because the graph stores no copy of a body, rewriting a function's internals
without changing its signature will *not* invalidate its enrichment — use
`wd enrich --force` when you want a deliberate rewrite. Enrichment is keyed by
node id, so renaming a symbol starts it fresh.

`props.enrichment` must carry a non-empty `provider`, `model`, `timestamp`, and
`description`. `wd add-node` refuses a record missing any of them and names the
gaps, rather than accepting a record that the next `wd discover` would discard:

```console
$ wd add-node "symbol:py:pkg.mod:fn" --type symbol --merge --props '{"enrichment":{"provider":"manual","description":"..."}}'
error[invalid_enrichment]: symbol:py:pkg.mod:fn: props.enrichment is missing required field(s): model, timestamp. An enrichment record must be written whole; discovery would drop this one, so the write was refused. | hint: ...
```

Write the record whole every time, including under `--merge`. A record is a
single attestation — provider P, model M, at time T, says D — so amending just
the `description` would leave the previous model and timestamp standing behind
text they never produced, and is refused for that reason. Restating all four
fields re-attests the node and is always accepted.

Manual inferred edges should use explicit provenance such as
`{"source": "manual"}` after the relationship is verified from source content.
`wd graph communities --write` derives `.weld/graph-communities.json`,
`.weld/graph-community-report.md`, and `.weld/graph-community-index.md`
from the existing graph without modifying `.weld/graph.json`.

Graph mutations are safe to run in parallel: every mutating command
(`add-node`, `add-edge`, `rm-node`, `rm-edge`, `import`, `migrate`,
`touch`, and `wd enrich`) serializes on an exclusive lock at
`.weld/graph.write.lock`, so concurrent writers queue instead of
overwriting each other's changes. A writer that cannot get the lock
within 60 seconds fails with an explicit error; set
`WELD_GRAPH_LOCK_TIMEOUT` (seconds) to wait longer, e.g. while a long
provider-backed `wd enrich` run holds the lock.

Without tree-sitter, the built-in Python module strategy and non-language
strategies (markdown, YAML, config, frontmatter) still work.

## MCP

Weld generates MCP config snippets for Claude Code, VS Code, Cursor, and
Codex in the default install:

```bash
wd mcp config --client=claude
wd mcp config --client=vscode
wd mcp config --client=cursor
```

Running the stdio MCP server requires the optional MCP SDK extra, which
pins `mcp>=2,<3`:

```bash
uv tool install "configflux-weld[mcp]"
wd mcp serve --help
```

Already have an older `mcp` installed separately? Upgrade it with
`pip install -U "mcp>=2"` -- the server targets the MCP SDK 2.x handler API
and exits with an upgrade hint on anything older.

Point your client at `wd mcp serve`:

```json
{"mcpServers": {"weld": {"command": "wd", "args": ["mcp", "serve"]}}}
```

`wd` is a console script, so the directory your client launches it in --
the repository being served -- is never placed on the server's module search
path, and the interpreter is the one weld was installed into rather than
whatever `python` resolves to in the client's environment. Running from a
source checkout rather than an install? `python -m weld.mcp_server` serves
the checkout and is supported for exactly that case; see
[docs/mcp.md](docs/mcp.md#the-repository-is-not-on-the-servers-import-path)
for what that form costs.

See **[docs/mcp.md](docs/mcp.md)** for the full tool reference, per-client
configs, example prompts, troubleshooting, and the exact dependency model. See
the [platform support matrix](docs/platform-support.md) for per-client support
status and runtime validation.

## Discovery configuration

Weld is driven by `.weld/discover.yaml`. Each entry maps a file pattern
to an extraction strategy:

```yaml
sources:
  - glob: "src/**/*.py"
    type: file
    strategy: python_module

  - glob: "docs/**/*.md"
    type: doc
    strategy: markdown

  - glob: ".github/workflows/*.yml"
    type: workflow
    strategy: yaml_meta
```

Run `wd init` to generate a starter config, or write one by hand. See
the [Strategy Cookbook](weld/docs/strategy-cookbook.md) for the full list
of bundled strategies.

Patterns take `*` (one path segment), `**` (any depth), `?`, `[abc]`, and a
single `{a,b}` alternative group, anywhere in the pattern — so a monorepo can
write `apps/*/package.json` or `services/*/src/**/*.{ts,tsx}` per package
rather than one repository-wide glob. Each entry also accepts an `exclude:`
list of patterns to skip. The pattern vocabulary belongs to the config, not to
the strategy you point it at: `api/*/routers/*.py` wired to `fastapi` names the
same files `services/*/src/*.py` wired to `python_module` does, and the same
goes for `pydantic`, `compose` and `events`. Freshness reads that same
vocabulary: a file your patterns name — through a `{a,b}` group or a `*`
segment as much as through a plain suffix — is in scope for `wd stale` exactly
as it is for `wd discover`, so adding one to a configured project is noticed as
missing from the graph on the next read.

`wd init` wires a source entry for every language it reports finding, and one
entry covers a language's whole *dialect family* — `**/*.{ts,tsx}` for
TypeScript, `**/*.{js,jsx,mjs,cjs}` for JavaScript — so a Next.js repository's
`.tsx` pages and components are claimed by the same entry as its `.ts`
modules. One entry is all they need: weld reads JSX with the TSX grammar and
plain TypeScript with the TypeScript one, choosing per file from its
extension, so a default-exported component in a `.tsx` page reaches the graph
like any other export. `language: tsx` is accepted as a spelling of the same
thing if you split the globs by hand, and records the files as TypeScript
either way — one language, one symbol namespace, whichever dialect a file is
written in. A framework it names is wired too: detecting Express writes an
`express` route entry, detecting Next.js writes a `next` one, the same way
detecting Gin or Axum writes a `gin` or `axum` one. The TypeScript and
JavaScript entries are written with `emit_calls: true`, which is what records
the function-level call evidence `wd callers` and `wd impact` answer from.

Next.js is detected from the markers a project carries rather than from an
import, because an app-router handler imports nothing from `next`: a
`next.config.*` file, or a `next` dependency in a `package.json`. The `next`
strategy then reads the app-router file conventions — every HTTP-verb function
an `app/**/route.ts` exports, and every `app/**/page.tsx`, becomes a `route:`
node at the URL its directory chain spells. Route groups (`(marketing)`) and
parallel slots (`@modal`) drop out of that URL as Next.js drops them, dynamic
segments (`[id]`, `[...slug]`) keep their source spelling, and a private
`_folder` is left out of routing entirely. A page reads as the `GET` it
answers, so "what URLs does this app expose" is one question with one kind of
answer whichever framework declared them; `props.route_source` separates
hand-written handlers from pages.

A route entry and a language entry routinely claim the same file, and both
answers survive. The handler file keeps everything the language pass recorded
about it — its exports, its imports, its line count — while the route entry
adds the `route:` nodes and the `exposes` edge saying which file serves them.
That holds whichever order the two entries appear in, so appending a framework
to a `discover.yaml` that already claims the language is safe. A config that
wires only the route entry still gets a file node for the boundary file, so
the `exposes` edge always has both ends.

A Dockerfile is identified by its path, so a repository that builds several
images gets a node per image. `apps/shop/Dockerfile` and `apps/blog/Dockerfile`
are `dockerfile:apps/shop/Dockerfile` and `dockerfile:apps/blog/Dockerfile`,
each carrying its own base image and its own `contains` edges to the files it
`COPY`s, and each reachable on its own from `wd context` and `wd impact`. A
Dockerfile at the repository root keeps the short `dockerfile:Dockerfile`
id it has always had, and where a project's only Dockerfile lives in a
subdirectory the old short id still resolves to it, so existing links and
bookmarks keep working.

The `markdown` strategy skips `README.md` by default — next to
`docs/architecture.md` a README is the project's front door, not one of its
documents. Set `include_readme: true` on a source entry to index it anyway.
`wd init` sets that flag itself on one entry: when a repository has no
conventional docs directory (`docs/`, `doc/`, `documentation/`) but does have
markdown, `wd init` wires a `**/*.md` fallback source, and in a repository
where markdown *is* the content the README is its index — the page that names
and links everything else. An indexed README is labelled by the `#` title it
declares rather than by its filename, so `wd query` reaches it under the name
it gives itself instead of under "Readme".

When a new weld release adds strategies for a language your repo uses, an
existing `discover.yaml` keeps discovering with the old config — `wd doctor`
and `wd prime` flag the drift (files on disk that no wired strategy claims,
including a dialect such as `.tsx` that an older `**/*.ts` entry misses).
Two ways to close the gap:

- `wd init --refresh` — **non-destructive**: merges newly-detected sources into
  your existing `discover.yaml`, appended under a marked `refresh` section.
  Your hand edits — custom globs, extra strategies, exclusions, and comments —
  are preserved exactly, and the `# generated-by: weld <version>` stamp is
  bumped so the config reads as current. Refresh delivers two kinds of thing:

  - **Languages** nothing on disk claims. For each one it wires **the same
    strategies `--force` would**: the whole detected stack, not just the
    tree-sitter backbone — so a C# repo gets the project, solution, MSBuild,
    test-framework, ASP.NET, EF Core and namespace-anchor entries, a Go repo
    gets `go_package` and its framework entries, and detected gRPC / event /
    ROS2 sources are offered too.
  - **Entries a language cannot stand for**: a root configuration file
    (`tsconfig.json`, `package.json`, `go.mod`, `Cargo.toml`, `Makefile` …)
    that joined weld's table after your config was written, and a framework
    entry (`express`, `next`, `gin`, `axum`, and the Python set) for a
    language your config already claims — the case where the language check
    has nothing to say and the entry is missing all the same.

  Anything your config already wires is left as you wrote it, never
  duplicated, and a config that is current on both counts is a no-op (it just
  refreshes the stamp). Refresh edits an existing config; run plain `wd init`
  first if none exists.

  **An entry you delete stays deleted.** `wd init` and `wd init --refresh`
  record what they wired as `# wired-entry:` comment lines under the version
  stamp, so refresh can tell an entry it never offered from one you removed on
  purpose — and only offers the first. The lines are inert comments; delete
  one and the next refresh offers that entry again. A `discover.yaml` written
  before weld kept that record seeds it from its own entries the first time
  you refresh, so an entry you had removed long ago may come back once;
  remove it again and it stays out.
- `wd init --force` — **destructive**: regenerates the whole config from a
  fresh scan, discarding any hand edits. Use it when you want a clean starter
  config back, not a merge.

### `.weld/.gitignore`

`wd init` and `wd workspace bootstrap` write a managed `.weld/.gitignore`
the first time they touch a `.weld/` directory, and never switch an
existing file to a different policy. Re-running either is still safe on a
later checkout: any pattern lines the file's own policy has gained since
are appended, nothing existing is changed, and a hand-edited file is left
alone entirely. Three policies are available.

The first two encode the one real choice — whether `graph.json` is
committed or rebuilt. The default (config-only) is the blessed answer for
almost every repo; see the [graph-tracking
policy](docs/graph-tracking-policy.md) for which to pick, what tracking
the graph actually costs today, and how to switch between them later.

- **Default — config-only.** Tracks the source-of-truth config
  (`discover.yaml`, `workspaces.yaml`, `agents.yaml`, `strategies/`,
  `adapters/`, `README.md`) and ignores everything else weld writes,
  including the generated graphs (`graph.json`, `agent-graph.json`),
  the filename index (`file-index.json`), graph-community reports
  (`graph-communities.json`, `graph-community-report.md`,
  `graph-community-index.md`), and per-machine state
  (`discovery-state.json`, `graph-previous.json`,
  `workspace-state.json`, `workspace.lock`, `query_state.bin`). A
  fresh contributor gets a clean `git status` after the first run.
- **Track-graphs (opt-in team workflow for warm CI / warm MCP).** Pass
  `--track-graphs` to widen the default so the artifacts that make a
  checkout warm are committed alongside config — each one together with
  the record that explains it: `graph.json` with
  `discovery-state.json` (what the graph read), `file-index.json` with
  `file-index-state.json` (what the index covers), plus
  `agent-graph.json`. Use this when every contributor should share a
  pre-built graph:

  ```bash
  wd init --track-graphs
  wd workspace bootstrap --track-graphs
  ```

  A fresh clone or `git worktree add` answers straight from the
  committed graph and its records, so an unchanged checkout reports
  fresh and runs no discovery at all. Only real source drift triggers a
  refresh. The same command writes a `.weld/.gitattributes` so a merge
  conflict on those files resolves by regenerating instead of by hand,
  and registers the driver in the checkout it runs in — git will not
  clone driver config, so **each clone runs `git config
  merge.weld-regenerable.driver true` once**. Gate freshness in CI with
  `wd stale --check --no-refresh`, which exits non-zero when the
  committed graph is behind its source. See [Worktrees and multiple
  checkouts](#worktrees-and-multiple-checkouts) for how the default
  (gitignored) mode bootstraps a new worktree.

- **Ignore-all (opt-in).** Pass `--ignore-all` for early experimentation
  or test installs where no weld state should be committed yet:

  ```bash
  wd init --ignore-all
  wd workspace bootstrap --ignore-all
  ```

  This writes a heavy-handed `*` / `!.gitignore` so every weld file is
  ignored.

`--track-graphs` and `--ignore-all` are mutually exclusive; passing both
is a usage error.

**Migration from earlier versions.** Pre-existing `.weld/.gitignore`
files written by older `wd init` / `wd workspace bootstrap` runs are
**not** rewritten — the helper is idempotent. Because of that,
`wd init --track-graphs` (or `--ignore-all`) in a repository whose
existing ignore file expresses a *different* mode fails and names the
file, rather than leaving you a half-switched checkout. To pick up the
new default, delete the file and re-run init:

```bash
rm .weld/.gitignore
wd init                  # config-only default, generated graphs ignored
# or wd init --track-graphs   to keep tracking the graphs as before
```

**Rules outside `.weld/` count too.** Whether the artifacts actually get
committed is a question about git, not about the one file Weld manages,
so `wd init --track-graphs` asks `git check-ignore`. That covers your
repository root's `.gitignore`, any `.gitignore` on the way down,
`.git/info/exclude`, and your global `core.excludesFile`. The usual
case is a root `.gitignore` that already carries `.weld/` — it hides the
artifacts just as completely, so `--track-graphs` fails and names the
file, line and pattern rather than reporting a warm setup that is not
one. Narrow that rule (or drop it: the managed `.weld/.gitignore` Weld
writes already ignores the per-machine files you did not want committed)
and re-run. Two cases still succeed: a `graph.json` that is *already*
tracked under such a rule — git keeps committing a tracked file
regardless, so the mode is in effect — and a directory that is not a git
checkout at all, where `wd init` works as before.

To opt out entirely, just delete `.weld/.gitignore` after init — the
skip-if-exists guard means it won't be recreated until the next init
or bootstrap.

### Warm graphs from CI (`wd warm`)

The config-only default keeps generated graphs out of git, which means a
fresh clone has no graph until the first `wd discover`. On a larger team you
can hand everyone a warm graph **without committing it** by building it once
in CI and distributing it as a build artifact. This rides your existing CI
artifact storage — there is no shared graph server and no hosted index.

Two pieces:

1. **Publish in CI.** The bundled
   [`graph-artifact.yml`](.github/workflows/graph-artifact.yml) workflow runs
   `wd discover --safe` on every push to `main` and uploads `graph.json` plus a
   `graph.json.sha256` integrity tag as an artifact keyed by the commit SHA.
   The graph is content-addressable, so the SHA identifies the graph content
   exactly. Adapt the workflow's storage/upload step to wherever your team
   already keeps build artifacts.

2. **Fetch locally.** `wd warm` finds the nearest-ancestor commit that has a
   published graph, verifies it against the published hash, lands it as your
   local `.weld/graph.json`, and refreshes it to your `HEAD`:

   ```bash
   # Point at the artifact source (a directory, or an https URL template
   # containing {sha}); or set WELD_WARM_SOURCE once for the whole team.
   wd warm --source /path/to/artifact-store
   wd warm --source "https://artifacts.example.com/weld/{sha}/graph.json"
   ```

   The artifact store is laid out as `<source>/<sha>/graph.json` (and the
   sibling `graph.json.sha256`). `wd warm` probes `HEAD` and its recent
   ancestors (`--max-ancestors`, default 50), so a developer a few commits
   ahead of the last CI build still gets a warm start and only re-discovers the
   handful of changed files.

**Always-safe fallback.** When no artifact is reachable — nothing published
yet, the source is unavailable, the integrity check fails, or you are outside a
git checkout — `wd warm` falls back to a full local `wd discover`. It never
leaves you worse off than running discover directly, and a tampered or corrupt
artifact is refused, never used. Pass `--no-fallback` if you want warm to skip
the local discover and simply report a miss.

### Custom strategies

Drop a Python file in `.weld/strategies/` to extract repo-specific
artifacts. The strategy signature:

```python
def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    ...
```

See [examples/02-custom-strategy](examples/02-custom-strategy/) for a
working example that extracts TODO comments as graph nodes, and
[docs/extending-discovery.md](docs/extending-discovery.md) for the
full step-by-step guide (contract, capability matrix, fixtures, and
a worked end-to-end walkthrough).

## Worktrees and multiple checkouts

A graph describes one tree at one commit, so an answer from a *different*
checkout is a wrong answer, not a cached one. Weld therefore answers from
the checkout you are standing in, and a fresh `git worktree add` becomes
answerable on its first read.

### Which root answers

Graph-backed reads (`wd query`, `wd find`, `wd context`, `wd path`,
`wd callers`, `wd references`, `wd trace`, `wd impact`, `wd brief`,
`wd stale`, `wd stats`, …) resolve their root in this order:

1. An explicit `--root` — always wins. It is made absolute and used as
   given, never re-walked.
2. The nearest enclosing directory containing a `.weld/`, **bounded by the
   git worktree you are in**. The bound is the point: worktrees are often
   created inside another checkout, and an unbounded upward walk would
   climb out of yours and answer from the outer checkout's branch.
3. The root of the current git worktree.
4. The current directory — outside a git checkout nothing else is searched,
   so weld never wanders into an unrelated parent project.

Resolution never crosses into another checkout. Commands that *create*
state — `wd discover`, `wd init`, `wd warm` — keep explicit-root semantics
and default to the current directory, so nothing is written to a root you
did not name.

### A new worktree answers on the first read

`git worktree add` gives you a tree with no graph. Rather than making you pay
a cold full discovery there, the first read seeds one:

```bash
git worktree add ../feature-x -b feature-x
cd ../feature-x
wd query "auth"
```

The first read prints one line to stderr naming what it seeded from and the
branch it reconciled to:

```text
[weld] seeded worktree graph from /repos/app; reconciled to feature-x@a1b2c3d4e5f6
```

Weld copies the graph from another checkout of the same repository (the
main checkout first, then any other worktree) and immediately reconciles it
against *your* tree, so the answer describes your branch rather than the one
it was seeded from. That reconcile is normally incremental, so it costs
roughly your branch's delta instead of a full pass. When the checkout it
copied from cannot account for the graph it handed over, weld re-derives from
scratch instead and says so on stderr: a partial update applied to a graph of
unknown provenance would quietly answer for the wrong code.

Worth knowing:

- **Any layout, any tool.** Sibling checkouts are found through git itself,
  so nested, sibling, temp-directory, and bare-repository-hub layouts all
  work no matter what created them. There are no path conventions to match.
- **What it needs.** The worktree's own `.weld/discover.yaml` (configuration
  always comes from the tree that owns it) and one other checkout of the
  repository that already has a graph. A plain clone has no sibling to seed
  from and keeps the normal first-run guidance — run `wd discover`, or use
  [`wd warm`](#warm-graphs-from-ci-wd-warm) to start warm from CI.
- **Track the config, or no worktree can seed.** Git only puts
  `.weld/discover.yaml` in a new checkout when the repository tracks it, so a
  project that ignores all of `.weld/` — including `wd init --ignore-all` —
  has turned seeding off for every worktree it will ever have. The default
  `.weld/.gitignore` tracks the config precisely to avoid that. If you ignore
  it anyway, `wd doctor` says so from the main checkout, and the first read in
  such a worktree names the missing file rather than only reporting that no
  graph was found. `git add -f .weld/discover.yaml` re-enables seeding.
- **What lands.** The graph plus its small state files; the optional SQLite
  index is not copied and rebuilds lazily. Deleting the worktree directory
  deletes everything weld put in it.
- **Which command triggers it.** Any read that answers from `.weld/` state,
  not just the graph searches: `wd query`, `context`, `path`, `callers`,
  `references`, `communities`, and equally `wd stale`, `wd find`, `wd stats`,
  `wd list`, `wd dump`. Checking freshness first is the natural way to start
  in a new worktree, so `wd stale` seeds and then reports the graph it just
  seeded rather than answering `no graph`. Where no seed is possible `wd stale`
  keeps answering — a freshness probe that refused would be useless — and names
  the prerequisite (the tracked-config caveat above) in a `seed_blocked_reason`
  beside its `reason`, rather than leaving `no graph` to imply that
  `wd discover` is the fix. `wd find` takes the same seed and then refuses if it
  arrived without one: a search that answered `no matches` from a file index
  that was never written would be a false negative about the whole tree, so it
  reports `error[file_index_missing]` and names the same prerequisite.
  Commands that *create* state — `wd discover`, `wd init`, `wd warm` — stay
  deliberate and seed nothing.
- **Frozen when you freeze it.** `WELD_AUTO_REFRESH=0` and `--no-refresh`
  each disable seeding along with auto-refresh, and they behave
  identically: seeding builds a graph and ends in a discovery pass, which
  is what both opt-outs exist to prevent. A worktree that has no graph yet
  therefore answers with the normal first-run guidance instead; drop the
  freeze and the next read seeds it.
- **Single-repo roots only.** A federated polyrepo root
  ([Polyrepo Federation](#polyrepo-federation)) is left alone; discovery run
  from a worktree of a workspace root already falls back to the main
  checkout for children that the worktree does not contain.

With a tracked graph ([`wd init --track-graphs`](#weldgitignore)) there is
nothing to copy — the graph is already in the tree, and so is the record of
what it read — so a fresh clone or worktree reports fresh and runs no
discovery until the sources actually drift. A repo initialised before that
record was tracked reconstructs a conservative one from the graph's own
contents instead, which costs the checkout one full discovery and then
converges.

### Confirming which checkout answered

`wd stale` reports the branch you are on next to the branch the graph was
built on (human form, trimmed):

```text
# stale
  stale: no
  ...
  graph_branch: feature-x
  branch: feature-x
```

`branch` is read live; `graph_branch` is what was checked out at discovery
time. On a detached `HEAD` and outside a git checkout there is no branch to
report — `(none)` in text output, `null` under `--json`. When the two
disagree, the graph is either about to refresh or you are reading a root you
did not mean.

Over MCP the same signal rides on every read as `freshness.branch`, and one
running server can answer for a checkout it was not started in via the read
tools' optional `root` parameter — bounded to checkouts of the same
repository. See **[docs/mcp.md](docs/mcp.md)**.

## Polyrepo Federation

Weld supports federated polyrepo workspaces where a root directory contains
several child git repositories, each owning its own `.weld/` directory. The
root maintains a meta-graph of cross-repo relationships without duplicating
child content. Children remain portable and independently publishable.

You do not need to `cd` into each child to keep the workspace usable.
`wd workspace bootstrap` onboards an entire polyrepo root in one command,
and `wd discover --recurse` refreshes every child plus the root meta-graph
in a single pass. This section walks the full lifecycle:
**bootstrap -> status -> query -> refresh**.

### Lifecycle at a glance

| Step | Command | What it does |
|---|---|---|
| Onboard | `wd workspace bootstrap` | Init the root, scan and init every nested child, discover each child, build the root meta-graph |
| Inspect | `wd workspace status` | Show every child's lifecycle state (present / missing / uninitialized / corrupt), the derived `stale` view when a present child has drifted past its graph, and git ref |
| Query | `wd query <term>` | Search the federated graph from the root; surfaces `repo:<name>` nodes and child-namespaced symbols |
| Read | `wd brief` / `context` / `path` / `callers` / `references` / `find` / `communities` / `trace` / `impact` | Every read tool federates across children (the `wd` CLI and matching MCP tools alike); `wd brief` spans child graphs from the root just like `wd query`; `trace`/`impact` reach child dependents via a read-time flatten, and `impact --from-diff`/`--working-tree` discover seeds from every present child's git repo |
| Refresh | `wd discover --recurse` (or per-child `wd discover`) | Rebuild child graphs and the root meta-graph; you choose the cadence |

### Onboarding a workspace (one-shot bootstrap)

Run `wd workspace bootstrap` at the workspace root. It is the fastest way to
go from "a directory full of git repos" to "a queryable federated graph". In
a single pass it:

1. Initializes the root (writes `.weld/discover.yaml` and a managed
   `.weld/.gitignore`) if it is not already a Weld project.
2. Scans for nested git repositories and writes `.weld/workspaces.yaml`
   listing each one as a child.
3. Initializes any child that is not yet a Weld project.
4. Runs discovery inside every present child (the same cascade as
   `wd discover --recurse`).
5. Builds the root meta-graph of `repo:<name>` nodes.

```bash
cd ~/workspace-root
wd workspace bootstrap
```

Example output for a root with three children:

```text
[weld] recurse: discovering libs-shared-models ...
[weld] recurse: libs-shared-models done
[weld] recurse: discovering services-api ...
[weld] recurse: services-api done
[weld] recurse: discovering services-auth ...
[weld] recurse: services-auth done
Bootstrapped workspace at: 3 child repo(s) discovered
  * root init: already initialized (no-op)
  * workspaces.yaml: written
  * per-child init: all children already initialized
  * discover: libs-shared-models, services-api, services-auth
  * present after bootstrap: 3 of 3
```

Bootstrap is idempotent: re-running it on an already-onboarded root re-scans,
re-discovers, and rebuilds the meta-graph without clobbering child config.
Add `--json` for a machine-readable summary with `children_discovered`,
`children_initialized`, `children_present`, and `errors` keys. Common flags:

```bash
wd workspace bootstrap --max-depth 2          # limit how deep the scan walks for nested .git
wd workspace bootstrap --exclude-path vendor  # skip a dir by name/path/glob (repeatable, persisted)
wd workspace bootstrap --respect-gitignore    # skip scan-only children ignored by Git
```

`--exclude-path` and `--respect-gitignore` are persisted into
`workspaces.yaml`, so subsequent bootstraps stay scoped without re-passing
the flags. Explicit `children` entries you add by hand always win over the
scan, even when gitignored.

> **Cross-repo edges are opt-in.** Bootstrap writes `cross_repo_strategies: []`
> -- it discovers structure but does not guess which resolvers apply. To wire
> calls between children, declare resolvers in `workspaces.yaml` and re-run
> discovery (see [Cross-repo resolvers](#cross-repo-resolvers)).

### Alternative: manual setup with `wd init`

If you prefer to onboard the root without immediately discovering children,
run `wd init` at the workspace root instead. When nested git repositories are
detected, weld scaffolds `.weld/workspaces.yaml` alongside the usual
`discover.yaml`, but does not init or discover the children for you:

```bash
cd ~/workspace-root
wd init                    # detects children, writes workspaces.yaml
wd init --max-depth 2      # limit scan depth for large directory trees
```

The `--max-depth` flag controls how many directory levels deep the scanner
looks for nested `.git` directories (default: 4). You then initialize and
discover each child yourself, or run `wd discover --recurse` at the root to
cascade discovery into every present child in one pass.

### workspaces.yaml format

The workspace registry lists every child repo and declares which cross-repo
resolvers are active:

```yaml
version: 1
scan:
  max_depth: 4
  respect_gitignore: false
  exclude_paths: [.worktrees, vendor, "scratch/**", "generated/**/*.tmp"]
children:
  - name: services-api
    path: services/api
    tags:
      category: services
  - name: services-auth
    path: services/auth
    tags:
      category: services
cross_repo_strategies: [service_graph]
```

- **version**: Schema version (currently `1`).
- **scan**: Controls automatic child detection. `max_depth` sets how deep
  the scanner walks; `respect_gitignore` opts scan-only children into Git
  ignore rules; `exclude_paths` lists directory names, relative paths, or
  glob patterns to skip. Explicit `children` entries remain authoritative
  even when gitignored. These settings decide which children are
  *registered*. Cross-repo resolvers that read manifests out of a child
  working tree always honour Git visibility -- an ignored file contributes no
  package name regardless of `respect_gitignore`. Neither does a manifest
  inside a vendored or build-output directory (`node_modules/`, `vendor/`,
  `.venv/`, `bin/`, `obj/`, ...), even in a repo that commits one: carrying a
  copy of somebody else's package is not publishing it.
- **children**: Each entry has a `path` (relative to the workspace root)
  and an optional `name` (auto-derived from the path if omitted, e.g.
  `services/api` becomes `services-api`). Optional `tags` provide
  category metadata; optional `remote` records a clone URL.
- **cross_repo_strategies**: Ordered list of resolvers that produce
  cross-repo edges in the root graph. Available resolvers include
  `service_graph`, `grpc_service_binding`, `compose_topology`,
  `channel_binding`, `package_graph` (manifest package-dependency to the
  producing repo, reading producer names from `pyproject.toml`, `go.mod`,
  npm `package.json`, MSBuild `.csproj`, and `.proto` manifests), and
  `package_import_resolver` (import evidence to a sibling package node).

### Running discovery at the workspace root

```bash
cd ~/workspace-root
wd discover --safe --output .weld/graph.json
```

When `workspaces.yaml` is present, `wd discover` operates in federation
mode. It reads each child's `.weld/graph.json`, builds `repo:<name>` nodes
for every present child, and runs the declared cross-repo resolvers to emit
edges between children. Children that are missing, uninitialized, or corrupt
degrade gracefully -- they are skipped and recorded in the workspace ledger
but do not block discovery.

A root that only federates needs no `.weld/discover.yaml` of its own: the
registry and the child graphs are its whole discovery input, and no source glob
is resolved at the root. `wd doctor` reports that absence as a `[note]` rather
than an error, so a healthy workspace root exits 0. `wd prime` says the same
thing — an `[INFO  ]` line naming federation as the reason, with no `wd init`
next step — and drops its "consider adding more sources to discover.yaml"
advisory there, because a meta-graph holding one node per child is the shape
federation is meant to produce, not a thin configuration.

Federation also re-tags `props.origin` on cross-child symbol references:
a Python target imported from a sibling child whose strategy saw it as
`external` is promoted to `project`, so "hide third-party" filters do
not lose cross-repo application code.

Discovery is safe to run from a linked git worktree of the workspace root:
the federation pass falls back to the main worktree's checkout when sibling
child repos are not present at the worktree itself. As a
defense-in-depth guard, federated discover refuses to overwrite an existing
non-empty `graph.json` with a 0-node meta-graph; pass `--allow-empty` to
intentionally tear the workspace graph down.

### Querying the federated graph

Once discovery has run at the root, query the whole workspace from the root
directory -- no need to `cd` into a child. Federation query results carry two
markers worth recognizing:

- A `repo:<name>` node represents each present child.
- Symbols that belong to a child are namespaced as `<child-name>::<node-id>`,
  so a single query spans every repo in the workspace.

```bash
cd ~/workspace-root
wd query "services-auth"
```

```text
# query: services-auth
  matches (2):
    1. repo:services-auth  [type: repo]
       label: services-auth
       confidence: definite
    2. services-api::rpc:http:out:POST:http://services-auth:8080/tokens  [type: rpc]
       label: POST http://services-auth:8080/tokens
       confidence: definite
```

When cross-repo resolvers are declared, the same query surface also reaches
the resolved endpoints in the target child (for example
`services-auth::route:POST:/tokens`), letting one lookup follow a call from
the caller's repo into the callee's. Every read tool operates on this same
federated graph -- `brief`, `query`, `context`, and `path` plus `callers`,
`references`, `communities`, `find`, `trace`, and `impact` -- on both the `wd`
CLI and the matching MCP tools, so a federated read reaches child nodes
identically on either surface. `trace` and `impact` flatten the workspace
(root + every present child) on read, so their cross-boundary and
reverse-dependency walks span child-internal edges as well as cross-repo ones.

A `repo:<name>` node is also a navigable entry point into its child:
`wd context repo:<name>` lists the child's top-level nodes, and `wd path
repo:<name> <child-name>::<node-id>` descends from the root into any reachable
child symbol. These root-to-child links are synthesized on read, so the
persisted root `graph.json` stays byte-identical (federation still writes only
`repo:<name>` nodes plus any declared cross-repo edges).

### Workspace status

Inspect the state of every registered child:

```bash
wd workspace status          # human-readable summary
wd workspace status --json   # JSON ledger, reported from disk
```

Example output:

```text
Workspace status (3 children)
Counts: present=2, missing=1, uninitialized=0, corrupt=0, stale=1
services-api: stale (refs/heads/main a1b2c3d4e5f6)
services-auth: present dirty (refs/heads/feature-x 7890abcdef01)
services-worker: missing
```

Each child shows its lifecycle status, git branch, HEAD SHA prefix, and
whether the working tree is dirty. The status header `Counts:` line tallies
how many children are in each state. A child is stored in exactly one of four
lifecycle states, and a `present` child can additionally render as `stale`:

| State | Meaning | Typical fix |
|---|---|---|
| `present` | The child directory and a valid `.weld/graph.json` are both on disk. The child participates in federation. | -- |
| `missing` | The child path declared in `workspaces.yaml` does not exist on disk (not cloned, moved, or renamed). | Clone or restore the child, or remove its entry from `workspaces.yaml`. |
| `uninitialized` | The child directory exists but has no `.weld/graph.json` yet. | Run `wd discover` inside the child, or `wd discover --recurse` / `wd workspace bootstrap` at the root. |
| `corrupt` | The child has a `.weld/graph.json` that fails to parse or validate. | Re-run `wd discover` inside the child to regenerate the graph. |
| `stale` | A *derived* view, not a stored state: a `present` child whose source has moved past the commit its graph was built from (new commits, or its `graph.json` bytes changed since the workspace ledger last recorded them). The child still participates in federation -- its graph is just behind. | Usually nothing: a root read (`wd query` / `context` / `path`) auto-refreshes stale children before serving (see [Refreshing children](#refreshing-children)). To refresh without a read, run `wd discover` inside the child or `wd discover --recurse` at the root. |

`stale` is computed at display time by running the single-repo freshness check
over each `present` child; it never overwrites a child's stored lifecycle
status. `missing`, `uninitialized`, and `corrupt` children are never reported
`stale` (they are not "behind" -- they are absent, bare, or broken). The
`Counts:` line only includes a `stale=N` column when at least one child is
stale, and that column is a *sub-count* of `present`, not a fifth bucket: a
child whose graph is behind is still checked out, so it stays counted under
`present`. `present` therefore means "on disk" on both surfaces -- the number
here is the same one `wd stale` reports as present in its child roster.

A cross-repo edge the root holds into a child that is not `present` is
*unverifiable* rather than wrong: `wd graph validate` reports it and exits
non-zero instead of passing a reference it cannot check. Restore the child and
run `wd discover` inside it, and the same edge validates cleanly.

`missing`, `uninitialized`, and `corrupt` children are skipped during
federated discovery -- they never block the root build. They are recorded in
the ledger so `wd workspace status` can surface them. The `--json` form emits
the raw ledger, including each child's `head_ref`, `head_sha`, `is_dirty`,
`graph_sha256`, and `last_seen_utc`. In `--json`, each `present` child also
gains a derived `freshness` object -- for example:

```json
"freshness": {
  "state": "stale",
  "stale": true,
  "reason": "source_changed",
  "head_sha": "a1b2c3d4e5f6...",
  "graph_sha": "0f1e2d3c4b5a...",
  "commits_behind": 1
}
```

The child's stored `status` field stays `present`; only `freshness.state` /
`freshness.stale` reflect the drift. `reason` is one of `fresh`,
`source_changed` (the child has new commits past its graph), `graph_drift`
(the child's `graph.json` bytes changed since the ledger recorded them),
`unknown_sha` (the child's graph carries no discovered-from SHA, treated
conservatively as behind), or `not a git repo` (a non-git child, never stale).

#### Ledger drift

Every lifecycle status above is re-probed on disk at read time; none of it is
taken from the stored ledger. The ledger records what the last `wd discover`
found, so anything that happened to a child since then -- deleted, cloned at
last, added to `workspaces.yaml` -- would otherwise be invisible here while
`wd stale` reported it correctly.

Where the stored ledger and the disk disagree, the difference is named below
the child lines, in a block of three parts: a header giving the count and the
source the numbers above came from, one line per child the two disagree about,
and a closing remedy line, `run: wd discover`, which re-records the ledger.
The first two parts, for a workspace whose `docs-site` child was deleted after
the last discover:

```text
Workspace status (3 children)
Counts: present=2, missing=1, uninitialized=0, corrupt=0
docs-site: missing
services-api: present (refs/heads/main a1b2c3d4e5f6)
services-auth: present dirty (refs/heads/feature-x 7890abcdef01)
Ledger drift (1) -- counts above are from disk, not from the stored ledger:
  docs-site: ledger says present, disk says missing
```

The block appears only when something drifted. In `--json` it is a top-level
`drift` array, always present and empty when the ledger agrees:

```json
"drift": [
  {"name": "docs-site", "stored": "present", "observed": "missing"}
]
```

A `null` marks the side with nothing to say: a child registered since the last
discover has `"stored": null`, and one removed from `workspaces.yaml` has
`"observed": null` -- the latter also leaves the `children` map, because it is
not a registered child any more.

`wd workspace status` reports drift; it never repairs it. Run `wd discover` at
the root to bring the ledger back into step.

`wd stats` reads the same children through the same probe, so its workspace
block never disagrees with the two commands above. Being a summary, it reports
the counts and a pointer rather than the per-child block:

```text
  workspaces: 3 registered, 2 present
  workspace ledger drift: 1 child differs from the stored ledger -- run wd workspace status for detail
```

The pointer line appears only when something drifted. In `wd stats --json` the
`workspaces` object carries `present` and `drift_count` alongside the existing
`count` and `children`; both are always present, with `drift_count` at `0` when
the ledger agrees. Each entry in `children` reports the lifecycle observed on
disk, so a child registered but never cloned reads `missing` here even before
the first `wd discover` has written a ledger.

### Refreshing children

The federated graph is only as fresh as the child graphs underneath it.
Weld both **detects** child drift and, on read commands, **refreshes** the
drifted children for you. Two surfaces report drift on demand:

- `wd workspace status` marks any `present` child that has moved past its
  graph as `stale` (see [Workspace status](#workspace-status)).
- At the root, `wd stale` runs the same per-child check and folds the result
  into its own freshness report: it lists a `children:` summary, names each
  stale child, and sets the top-level `stale` flag to *the root's own
  staleness OR any child being stale*. So a fresh root with one drifted child
  still reports `stale: yes`. The `--json` form carries the root's own signals
  under `root_source_stale` / `root_sha_behind` plus a `children` array (one
  entry per registered child, each with `name`, `state`, `reason`, and
  `commits_behind`):

```text
# stale
  stale: yes
  ...
  children: 3 registered, 3 present, 1 stale
    services-api: stale (source_changed, 1 behind)
```

  The summary counts registered children against how many are actually
  **present** on disk, so a registry that lists children none of which are
  checked out reports `3 registered, 0 present, 0 stale (missing=3)` rather
  than a bare `0 stale` that would read as "all healthy". Absent and
  unreadable children are broken out by lifecycle state (`missing`,
  `uninitialized`, `corrupt`), reusing the same vocabulary as
  `wd workspace status`.

**Auto-recurse on root reads.** A root read command (`wd query`, `wd context`,
`wd path`) auto-refreshes drift before serving: it discovers only the
*stale-or-uninitialized* children (a one-child edit refreshes one child, not
the whole workspace), rebuilds the root meta-graph so cross-repo edges
re-resolve against the fresh child graphs, and then answers — no `cd` into the
child. This mirrors the single-repo auto-refresh that read commands already
run when a graph is stale. Fresh children, and `missing` / `corrupt` children,
are left untouched. One child failing to refresh never breaks the read: the
failure is isolated and the other children still refresh.

The opt-outs are identical to the single-repo case:

- `WELD_AUTO_REFRESH=0` (or `--no-refresh` on the command) disables the
  refresh. This is the **CI / batch contract**: a pipeline that builds or
  commits child graphs independently sets `WELD_AUTO_REFRESH=0` so a root read
  never rewrites child `graph.json` files or the root meta-graph. Detection
  still works under the opt-out — `wd workspace status` and `wd stale` report
  child drift (they are read-only); only the *refresh* is suppressed.
- `--no-refresh` additionally prints a stderr warning naming the stale
  children, so you know the answer may lag their current source.

There is no background watcher: refresh is pull-based and happens on the read.
You can still drive a refresh explicitly when you want to control the cadence
(for example, to refresh fresh-but-CI-relevant children, or to refresh under
`WELD_AUTO_REFRESH=0`):

```bash
# Fast refresh: cascade discovery into every present child + rebuild the
# root meta-graph in one pass. Best for a deliberate "pick up everything".
wd discover --recurse --safe --output .weld/graph.json

# Or refresh a single child you just edited, then rebuild only the root:
(cd services/api && wd discover --safe --output .weld/graph.json)
wd discover --safe --output .weld/graph.json
```

After any refresh, `wd workspace status` reflects each child's new `head_sha`
and dirty flag, so it doubles as a "did my refresh land everywhere" check.

> **Recommended cadence for cross-repo edges.** Some framework details (for
> example FastAPI route endpoints) are captured more completely by a
> standalone `wd discover` run *inside the child* than by the root
> `--recurse` cascade. When you depend on cross-repo edges (see
> [Cross-repo resolvers](#cross-repo-resolvers)), refresh each affected child
> in its own directory first, then run a plain `wd discover` at the root to
> re-run the resolvers. Use `--recurse` (or `wd workspace bootstrap`) for the
> fast structural refresh of `repo:<name>` nodes when you do not need every
> endpoint-level edge re-derived.

### Sentinel files

Weld uses two sentinel files to distinguish workspace roots from
single-repo projects:

| File | Purpose |
|---|---|
| `.weld/workspaces.yaml` | Workspace registry -- lists children and cross-repo strategies |
| `.weld/workspace-state.json` | Workspace ledger -- lifecycle status, git SHA, graph hash per child |

The presence of `workspaces.yaml` activates federation mode in `wd discover`.
`workspace-state.json` is written automatically during discovery and read by
`wd workspace status`.

When `.weld/workspaces.yaml` is present at the bootstrap target, `wd bootstrap`
appends a federation paragraph to the copilot skill/instruction, codex skill,
and claude command directing agents to pick a child via `wd workspace status`
before querying inside it.

### Cross-repo resolvers

Resolvers are plugins that analyze child graphs and emit typed edges across
repo boundaries. They are declared in the `cross_repo_strategies` list in
`workspaces.yaml` and run in declaration order during root discovery.

| Resolver | Description |
|---|---|
| `service_graph` | Matches HTTP client call sites in one repo to API endpoint definitions in another. Emits `cross_repo:calls` edges carrying the matched host, port, path, and method. |
| `channel_binding` | Matches event-channel producers in one repo to consumers in another that reference the same `channel:<transport>:<topic>` node. Emits `cross_repo:channel_flow` edges (producer -> consumer) carrying the matched channel, transport, and topic. |
| `package_graph` | Matches a *manifest-declared* package dependency in one repo -- a C# `<PackageReference>`, a Python `pyproject.toml` `[project].dependencies` entry, an npm `package.json` runtime `dependencies` entry, or a `go.mod` `require` -- to the sibling repo that *produces* that package. Producer names come from five manifest families: a `pyproject.toml` `[project].name`, a `go.mod` module path, a `package.json` `name` (unless the package declares itself `private`, npm's own "never published"), an MSBuild `.csproj` `<PackageId>` (falling back to the project filename, NuGet's own `dotnet pack` default, unless the project declares `<IsPackable>false</IsPackable>`), or a `.proto` package name. Only npm *runtime* `dependencies` are read: `devDependencies`, `peerDependencies` and `optionalDependencies` declare no run-time dependency on a sibling repo. An npm workspace's members are read where they sit, including under `packages/`, and a dependency satisfied inside the same repo is not a cross-repo edge. Emits `cross_repo:depends_on` edges from the consuming `repo:<name>` node to the producing `repo:<name>` node, so `wd impact "repo:<producer>"` sees its consumers. This is the schema-library polyrepo shape that URL-host (`service_graph`) and topic (`channel_binding`) matching miss. Name matching is case-insensitive; no version is checked, so edges carry `confidence: inferred`. |
| `grpc_service_binding` | Matches gRPC service definitions (`rpc:grpc:*` nodes from the `grpc_proto` strategy) in one repo to gRPC client stubs and their call-site `invokes` edges (from the `grpc_bindings` strategy) in other repos. Emits `cross_repo:grpc_calls` edges from the client stub to the service definition. |
| `compose_topology` | Reads `docker-compose.yml` / `compose.yaml` / `compose.yml` at the workspace root and emits `cross_repo:depends_on` edges between the `repo:<name>` nodes of services whose images or service names map to registered child repos. |
| `package_import_resolver` | Matches *import evidence* -- consumer nodes carrying an `imports_from` list -- against package producer nodes (`type: package`) declared in sibling repos, and emits `cross_repo:depends_on` edges from the importing consumer to the producing package. Complements `package_graph`, which matches manifest declarations rather than imports. |

Resolvers are read-only with respect to child graphs -- they never modify
a child's `.weld/graph.json`. Output edges are deterministic: identical
input produces byte-identical edges across runs.

A cross-repo edge endpoint is spelled one of two ways, and which one depends
on what the endpoint names. A node that lives inside a child repo is written
`<child-name>\x1f<node-id>` and is resolved in that child's graph at read
time; a whole repository is the root's own `repo:<name>` node, written plainly
because there is nothing to resolve inside a child. `wd graph validate` at a
workspace root checks every endpoint against both, so an edge naming something
no repo holds is reported rather than carried, and `wd discover` drops such an
edge with a warning naming the resolver that produced it.

Resolvers are opt-in. A fresh `wd init` or `wd workspace bootstrap` leaves
`cross_repo_strategies: []`, so no cross-repo edges are emitted until you
declare one. To enable the example above:

```bash
# 1. Add the resolver to .weld/workspaces.yaml:
#    cross_repo_strategies: [service_graph]
# 2. Re-run discovery at the root so the resolver gets a chance to match:
wd discover --safe --output .weld/graph.json
```

`service_graph` matches strictly: the client URL's host must equal a sibling
child's name, and the client `(method, path)` must equal the server route's
`(method, path)` exactly -- no trailing-slash or prefix tolerance. A call to
an external host, or to a child whose endpoint was not captured, simply
yields no edge rather than a guess.

### Performance: eager query aggregation (default-on)

The federation pre-aggregates every fresh-sidecar child's inverted
index into a single in-memory dict at construction time. Per-query
latency then drops by 40-90% on a 30-child workspace, at the cost of
~17 ms construction overhead. This is **on by default** for
fresh-sidecar children; stale or missing-sidecar children keep the
existing per-query fallback path, so a workspace with no fresh
sidecars pays no aggregation tax. Match sets are byte-identical to the
lazy path.

To turn it off -- for example a single-shot `wd query` that should not
pay the one-time construction tax -- set `WELD_FEDERATION_EAGER=0`
(falsy values: `0`, `false`, `no`, `off`; case-insensitive). A truthy
value (`1`, `true`, `yes`, `on`) forces it on. In code, an explicit
`FederatedGraph(root, eager_index=...)` argument overrides the
environment variable in either direction.

### Rollback

To disable federation and return to single-repo behavior, delete the
workspace registry:

```bash
rm .weld/workspaces.yaml
```

This returns weld to legacy single-repo discovery at the root. Child
repositories are untouched -- each child's `.weld/` directory, graph, and
configuration remain intact and continue to work independently.

Optionally, remove the generated ledger as well:

```bash
rm .weld/workspace-state.json
```

## CLI reference

| Command | Description |
|---|---|
| `wd init` | Bootstrap `.weld/discover.yaml` (and `workspaces.yaml` when nested repos are detected); seed managed `.weld/.gitignore` (config-only default ignores generated graphs) |
| `wd init --max-depth N` | Limit nested repo scan depth during init (default: 4) |
| `wd init --respect-gitignore` | Skip scan-only nested repos ignored by Git when writing `workspaces.yaml`; explicit children can still be added later |
| `wd init --track-graphs` | Seed `.weld/.gitignore` so the warm-checkout artifacts stay tracked alongside config -- `graph.json` + `discovery-state.json`, `agent-graph.json`, `file-index.json` + `file-index-state.json`; also writes `.weld/.gitattributes` and registers the `weld-regenerable` merge driver so conflicts on them resolve by regenerating (warm-CI / warm-MCP workflow) |
| `wd init --ignore-all` | Write a fully-ignoring `.weld/.gitignore` instead of the config-only default; mutually exclusive with `--track-graphs` |
| `wd discover` | Run discovery, emit graph JSON (federation mode when `workspaces.yaml` is present); on success prints a one-line stderr summary `wrote N nodes / M edges -> path (T.Ts)`, suppressed by `--quiet` |
| `wd agents discover` | Scan AI customization assets and write `.weld/agent-graph.json`; text mode summarizes diagnostics per code and `--show-diagnostics` dumps the full list inline |
| `wd agents rediscover` | Refresh `.weld/agent-graph.json` from a new static scan |
| `wd agents list` | List discovered AI customization assets from `.weld/agent-graph.json` |
| `wd agents explain <asset>` | Explain one AI customization asset and its graph relationships |
| `wd agents impact <asset>` | Show affected Agent Graph assets for a proposed customization change |
| `wd agents audit` | Audit AI customization assets for static consistency issues |
| `wd agents plan-change "<request>"` | Plan a static AI customization behavior change |
| `wd agents viz` | Local read-only browser explorer for `.weld/agent-graph.json` |
| `wd workspace status` | Show workspace child ledger: lifecycle status (re-probed on disk at read time), git ref, dirty state, and any drift from the stored ledger |
| `wd workspace status --json` | Emit the `workspace-state.json` payload with lifecycle status reported from disk, plus a `drift` array |
| `wd workspace bootstrap` | One-shot polyrepo bootstrap: init root + every nested child, recurse-discover, rebuild root meta-graph (config-only `.weld/.gitignore` default) |
| `wd workspace bootstrap --respect-gitignore` | Skip scan-only child repos ignored by Git and persist `scan.respect_gitignore: true` into `workspaces.yaml` |
| `wd workspace bootstrap --track-graphs` | Bootstrap and seed the Mode B policy (`.gitignore` + `.gitattributes` + merge driver) in root and every child, so each tracks its warm-checkout artifacts alongside config |
| `wd workspace bootstrap --ignore-all` | Bootstrap and write a fully-ignoring `.weld/.gitignore` in root and every child; mutually exclusive with `--track-graphs` |
| `wd build-index` | Regenerate file index |
| `wd query <term>` | Hybrid-ranked tokenized graph search (strict-AND first; OR fallback when AND yields nothing on multi-word phrases — envelope is tagged with `degraded_match=or_fallback`). Multi-word phrases have common function-word stopwords (`the`, `is`, `how`, `of`, `for`, WH-words, …) dropped before matching so content tokens drive the result (a natural-language phrase like `"how does auth work"` searches on `auth`/`work`); single-word and symbol-id queries are never altered. A token written with `-` also matches the `_` spelling and vice versa, so a query typed the way a project names itself (`tree-sitter`) reaches code that spells it `tree_sitter`. Concept nodes derived from issue-tracker titles rank below code and never suppress it: because such a title quotes the query it describes, it would otherwise be the only strict-AND match and hide the very code being searched for. Shows `confidence` per match and hides `origin=unresolved` sentinels by default (`--include-speculative` restores them). Bounds the neighborhood by default (drops stdlib/unresolved/speculative-external neighbors, caps fan-out, then a byte budget prunes to fit the tool cap — all reported in `omitted_neighbors`, including `size_capped`); `--full-neighborhood` restores the raw neighborhood and `--full-size` skips only the byte budget |
| `wd find <term> [--limit N]` | Broad file-token search, separate from graph discovery; each hit carries an integer `score` (default `--limit 20`). A single word is a case-insensitive substring match; a multi-word phrase is tokenized on whitespace and ranks files by how many of the words their tokens hit (so `wd find "mcp server"` surfaces `mcp_server.py`). Its index covers more of the tree than the graph's own scope does, so it keeps its own coverage check rather than borrowing the graph's: a file inside the search surface that the index has not accounted for schedules the rebuild that ingests it, instead of being reported as no match. Where there is no index at all to read, it says so and exits non-zero (`error[file_index_missing]`, remedy `wd discover`) rather than reporting `no matches` about a tree it never searched; an index that exists and matches nothing is a real answer and still exits 0. Symlinks are never followed, so a link committed to the repository cannot pull content from outside the checkout into a searchable index |
| `wd context <id>` | Node + neighborhood (bounded by default, same as `wd query`; `--full-neighborhood` restores the full neighborhood, `--full-size` skips the byte budget) |
| `wd path <from> <to>` | Shortest path |
| `wd trace <term>` | Startup/runtime and interaction slice around a term or node (byte-bounded; `--full-size` for the whole slice) |
| `wd impact <path-or-node>` | Reverse-dependency blast radius. `affected_surfaces` buckets what the radius reaches: `cli_commands`, `mcp_tools`, `repo_tools`, `api_endpoints`, `entrypoints`, `boundaries`, `tests`. Published surfaces set `risk_level` (endpoints/entrypoints/boundaries are `HIGH`, CLI commands and MCP tools `MEDIUM`); `repo_tools` — the repo's own scripts — is reported but never raises risk, since internal tooling is not a contract anyone outside the repo depends on. `--json` is byte-bounded — dependents and surface members are pruned farthest-hop-first in one pass and reported in `warnings.size_capped`, while `risk_level` and `affected_surface_counts` always reflect the full radius; `warnings.budget_exceeded` flags a payload that is still over budget after pruning everything droppable; `--full-size` returns every dependent, and the human output is never bounded. On a `repo:<name>` target the answer depends on whether cross-repo resolvers ran: when they did, the result carries `measured_by` naming them (so `0 dependents` is a measurement, not a shrug); when they did not, it is `Risk: UNKNOWN` with a reason that says whether `cross_repo_strategies` is empty or set but never ran |
| `wd capabilities` | Runtime per-language / per-framework support matrix (`--json`, `--missing`) |
| `wd callers <symbol>` | Direct/transitive callers (`--json` is byte-bounded, drops reported in `size_capped`; `--full-size` returns every caller) |
| `wd references <name-or-node-id>` | What points at a thing, plus file-index hits. Takes a bare symbol name or a full node id. A symbol reports its callers; any other node type (build target, tool, doc) reports every node with an edge into it, since nothing *calls* those. An id weld does not know reports an `error` **and exits non-zero**, matching `wd callers` and `wd context`, so "unknown" and "nothing points at it" stay distinguishable — "no references" is what a reader sees before deleting a symbol as dead, and a typo or a moved symbol must not render as "nothing uses this" (`--json` is byte-bounded, dropping file hits before resolved callers and resolved `matches` last; `--full-size` returns everything) |
| `wd viz` | Local read-only browser graph explorer (sidebar toggles: **Hide standard library**, **Hide third-party dependencies** — see [Filtering noise in `wd viz`](#filtering-noise-in-wd-viz)) |
| `wd stale` | Check graph freshness; reports `branch` (live) beside `graph_branch` (what was checked out at discovery), and `coverage_stale` when a file discovery would resolve today is missing from the discovery inventory (or the inventory can no longer vouch for the graph on disk) — the one signal that fires without HEAD having moved, so an empty result can be told apart from a genuine absence. Where there is no freshness answer to give, `reason` says which case it is: `no graph` (nothing discovered here yet — run `wd discover`) or `not a git repo`. In the one case where `wd discover` is *not* the remedy — a linked worktree of a repository that does not track `.weld/discover.yaml`, which therefore can never seed a graph from a sibling checkout — an optional `seed_blocked_reason` names that prerequisite and the `git add -f` that fixes it repository-wide. Without it a missing graph reports the same `sha_behind: no` / `commits_behind: -1` as a graph that merely has no recorded basis. When `source_stale` is true, `stale_sources` names which path(s) tripped it and why — `changed since last discovery`, `content differs`, `ingested file vanished`, or `in-scope file never ingested` — capped at 50 entries with `stale_sources_omitted` reporting how many more there were; some stale states (no recorded SHA, unreachable history) have no file-level cause to name and leave it empty |
| `wd stale --check` | Same report, but exit 1 when the graph is stale. The freshness gate for a repo that commits its graph: `wd stale --check --no-refresh` in CI fails a commit whose tracked graph is behind its source |
| `wd <read-cmd> --root <dir>` | Answer from an explicit root. Omitted, the root is resolved from the current directory and bounded by the git worktree you are in — never another checkout ([Worktrees and multiple checkouts](#worktrees-and-multiple-checkouts)) |
| `wd <read-cmd> --no-refresh` | Skip the auto-refresh that runs when the graph is stale; a warning is emitted to stderr. Also skips the first-read seeding of a new worktree, so the command neither builds nor bootstraps a graph. `WELD_AUTO_REFRESH=0` is the same opt-out applied globally for CI / batch runs. |
| `wd graph stats` | Graph statistics |
| `wd graph communities [--format json\|markdown] [--top N] [--write]` | Detect deterministic graph communities, report top-level hubs, and optionally write derived JSON/report/index artifacts (unresolved-symbol nodes are excluded from the projected subgraph) |
| `wd stats` | Backward-compatible alias for `wd graph stats` |
| `wd graph validate` | Validate graph against the contract. At a polyrepo root it also resolves every cross-repo edge endpoint against the child graphs: an endpoint naming a node no repo holds fails, and so does one pointing into a registered child that is missing, uninitialized, or corrupt -- a reference that cannot be verified is not a verified reference. Run `wd workspace status` to see which child is in the way |
| `wd graph validate-fragment <file>` | Validate imported graph fragments and warn on trace-inert semantics |
| `wd validate` | Backward-compatible alias for `wd graph validate` |
| `wd migrate --add-confidence` | Backfill missing edge `confidence` props (`definite` / `inferred` / `speculative`) by classifying each edge from its `source_strategy`; strategies without a declared default land at `speculative`. Writes the graph back and emits a JSON report `{filled, unchanged, invalid}`. |
| `wd doctor` | Check setup health; exits 0 in directories that are not Weld projects yet. At a polyrepo root the `[Edges]` section reports cross-repo edge endpoints that resolve to nothing |
| `wd prime` | Setup status + per-framework agent surface matrix (skill / instruction / mcp) with fix commands; `--agent {auto,claude,codex,copilot,all}` forces an agent row even when its framework files are absent |
| `wd scaffold` | Write starter templates |
| `wd bootstrap` | Agent onboarding files |
| `wd brief` | Agent context briefing |
| `wd enrich` | LLM-assisted semantic enrichment; `--agent-direct` prints the no-credentials work plan instead of calling a provider |
| `wd lint` | Lint the graph for architectural violations |

`wd doctor` reports each finding at one of four levels: `[ok ]` for
healthy state, `[note]` for soft recommendations (a missing optional
provider, no MCP config), `[warn]` for a currently-degraded state (a
stale graph, missing tree-sitter grammars), and `[fail]` for invalid
setup. Only `[fail]` raises the exit code; notes and warnings are
visible but never fatal. Each note carries a stable id (e.g.
`(id: optional-copilot-cli-missing)`) that you can dismiss per project:

```bash
wd doctor --ack optional-copilot-cli-missing   # write to .weld/doctor.yaml
wd doctor --unack optional-copilot-cli-missing # restore
wd doctor --list-acks                          # list current dismissals
```

The valid note ids are `agent-graph-missing`, `mcp-config-missing`,
`optional-mcp-missing`, `optional-anthropic-missing`,
`optional-openai-missing`, `optional-ollama-missing`,
`optional-copilot-cli-missing`,
`worktree-seeding-config-ignored` (the repository's ignore rules keep
`.weld/discover.yaml` out of git, so no linked worktree can
[seed a graph](#a-new-worktree-answers-on-the-first-read)), and one
`unclaimed-source-<language>` per language flagged by the stale-config
check below. The `copilot-cli` probe walks `WELD_COPILOT_BINARY` and
`PATH` for the standalone GitHub Copilot CLI binary, so its install hint
points at
[github.com/en/copilot](https://docs.github.com/en/copilot/how-tos/use-copilot-cli)
rather than a `pip install` line.

**A workspace root that only federates.** A federated `wd discover` reads
`.weld/workspaces.yaml` and the children's graphs and resolves no source glob
at the root, so a root that only federates has nothing of its own to discover
and needs no `discover.yaml`. Where the registry is present and
`discover.yaml` is absent, doctor reports the absent config as a `[note]`
rather than a `[fail]`, and a healthy workspace root exits 0. The two
neighbouring shapes are unchanged — a root that federates *and* discovers
keeps its `[ok]` line with the source count, and a plain repository that has
simply not been initialised yet still gets the `[fail]`. `wd prime` follows
the same rule on its own two lines about the config: the absence is an
`[INFO  ]` line saying the root federates rather than an `[ACTION]` with a
`wd init` next step, and the `Graph has only N nodes — consider adding more
sources to discover.yaml` advisory is dropped, since it is a sentence about a
config this root has no use for. Its neighbouring shapes are unchanged too — a
root that federates *and* discovers keeps its `[OK    ]` line **and** the
node-count advisory, because there the config exists and the root really does
resolve sources of its own.

**Stale-config detection.** `.weld/discover.yaml` is generated once by
`wd init` and never revisited, so a checkout initialised before a
strategy shipped keeps discovering with the old config — a repo can have
100% of a language's source invisible to the graph while everything else
reports healthy. `wd doctor` and `wd prime` guard against this: each runs
the `wd init` detection pass read-only and, for any file on disk that no
wired strategy claims, emits a warning such as

```
[warn] 8 C# files present that no wired strategy claims -> run: wd init --refresh (keeps your entries) or wd init --force (regenerate from scratch)
```

Both remedies are named, non-destructive one first: `--refresh` merges the
missing entries into your config, `--force` regenerates it from a fresh
scan and discards hand edits. `wd prime` lists `wd init --refresh` under
**Next steps** for the same reason — a step you are told to run should not
be the one that throws your customisation away.

A wired entry claims the files its glob actually matches, so a config
wiring only `**/*.ts` is told about the `.tsx` files beside them — the
warning names the *unclaimed* file count, not the language's total. It
stays quiet about what you scoped on purpose: a repo that merely lacks an
optional framework extractor but does wire the language, an entry
scaffolded for files that do not exist yet, a language absent from disk,
or a subtree your config deliberately leaves out (one claimed file per
file type settles it). The check never raises the exit code and is
suppressible per language via `wd doctor --ack
unclaimed-source-<language>`. `wd init` also stamps the generating weld
version into each `discover.yaml` (`# generated-by: weld <version>`) so
config drift is visible against `wd --version`, and records what it wired
below that stamp (`# wired-entry:` lines) so `wd init --refresh` can tell an
entry it never offered from one you removed on purpose.

The `mcp SDK` probe checks the version, not just the import: the MCP
stdio server requires `mcp>=2`, so an older SDK is reported as a `[warn]`
naming the installed version and the upgrade that fixes it
(`pip install -U 'mcp>=2'`) instead of being counted as present. An SDK
that is genuinely absent still gets the install-the-extra note, because
the two states have different remedies.

### Per-language trust metrics

"Should an agent trust weld's output for language X?" is a number, not a
vibe. `wd stats --json` carries a `per_language_trust` block keyed by
language; the human-readable `wd stats` prints the same numbers under a
`per_language_trust:` heading. For each language with at least one symbol
it reports:

- **`unresolved_symbol_ratio`** — the share of that language's symbols
  the origin resolver could not place (`origin=unresolved`). A high ratio
  means cross-symbol edges and query results in that language are mostly
  speculative noise.
- **`edge_resolution_rate`** — the share of that language's `inherits`
  and `calls` edges whose target resolved to a concrete node. Edges are
  attributed to the language of their *source* (the calling / subclassing
  side); a target that resolves to a standard-library or third-party node
  still counts as resolved because a real node was found.
- **`description_coverage_pct`** — the share of that language's symbols
  carrying an enrichment description.

```bash
wd stats --json | jq '.per_language_trust'
```

`wd doctor` adds a **`[Trust]`** section built on the same numbers: it
emits a `[warn]` for any language with enough symbols to be meaningful
whose `unresolved_symbol_ratio` rises above an absolute floor (currently
35%). Weld keeps no historical baseline, so the threshold is a fixed
floor describing a currently-degraded state rather than a regression
against a previous run; like every other doctor warning it is visible but
never raises the exit code.

`wd lint` also loads custom edge rules from `.weld/lint-rules.yaml` when
present:

```yaml
rules:
  - name: no-api-to-internal
    deny:
      from: { type: file, path_match: "api/**" }
      to: { type: file, path_match: "internal/**" }
```

Rules can add an `allow` block with the same `from` / `to` selectors to
exempt specific edges from a broader deny match.

Output is signal-first: the summary line counts violations per rule,
high-signal rules (`no-circular-deps`, `boundary-enforcement`) print
before noisier ones, and `orphan-detection` runs last. By default the
orphan rule suppresses `doc`, `config`, and test-file node types
(intentional leaves in nearly every codebase) and the suppressed count
is reported in the summary. Pass `--include-noisy` to surface every
orphan. Suppressed orphans on their own do not raise the exit code.

Run `wd --help` for the full list.

The repository includes a canonical Agent System Maintainer skill at
`.agents/skills/agent-system-maintainer/SKILL.md` and a GitHub Copilot
Agent Architect at `.github/agents/agent-architect.agent.md`. They are
ordinary Agent Graph assets, so `wd agents discover`, `explain`, and
`impact` can inspect them before future customization changes.

### Edge provenance with `props.source`

`wd add-edge` accepts a strict set of edge types (see
`weld.contract.VALID_EDGE_TYPES`). When an agent, tool, or LLM emits an
edge, stamp its origin under `props.source` so downstream consumers can
filter, rank, or audit tool-generated relationships. The `--props` help
text carries the canonical example: `--props '{"source":"llm","confidence":"inferred"}'`.
The `source` value is free-form (agent name, tool name, `llm`,
`manual`, strategy id); `confidence` follows the existing vocabulary
(`definite`, `inferred`, `speculative`). This replaces the 0.3.0-era
`--source` and `--relation` flags.

## The first-run overview in `wd viz`

When you open `wd viz` with nothing selected, the cold-open view is a **curated
architecture slice** rather than a raw dump of as many nodes as fit. It shows
your project's orientation surfaces first — CLI commands, agents, workflows, the
top-level packages, plus a few of each package's principal files — laid out
hierarchically (a `dagre` layout) so the structure is legible at a glance. The
view is bounded well under the node limit, so it is never truncated, and the
inspector is seeded with the same set of entry points as clickable links so you
always have a starting point.

This is a presentation default for the unconfigured view only. The moment you
search, click a node, or pin **Node** types in the sidebar, the curated slice is
replaced by the full, unfiltered slice for that query — your choice is honored
verbatim and persists in the URL hash. To see the dense whole-graph view, pin a
node type (for example `symbol`) or run a search; the sidebar **Limit** then
controls how many nodes that slice shows. To override the overview's `dagre`
layout, pick another layout from the toolbar selector — an explicit choice
always wins and is remembered in the URL.

## Filtering noise in `wd viz`

A real codebase's graph mixes the application code you wrote with calls into
the language standard library (`print`, `len`, `std::string`) and third-party
dependencies (numpy, boost, npm packages, Cargo crates). When you open
`wd viz`, the sidebar gives you two checkboxes for collapsing that noise so
you can focus on application code:

- **Hide standard library** — drops nodes classified as language built-ins
  or stdlib (Python `builtins` and `sys.stdlib_module_names`; C++ `std::`
  and toolchain libc++/libstdc++ headers; analogous lists per language).
- **Hide third-party dependencies** — drops nodes resolved outside the
  project tree but not part of the language stdlib (PyPI / npm / Cargo /
  Go-module / vendored boost / vendored serde, etc.).

Each label shows a count next to it (for example "Hide standard library
(412)") so you can see how much each toggle would remove before applying it.
The two checkboxes are independent and compose: tick both to focus on
project-only code, tick neither to see the full graph.

Hiding is a presentation choice. The underlying graph is unchanged: every
node still exists in `.weld/graph.json`, and every other surface
(`wd query`, `wd context`, MCP) still returns the hidden nodes. `wd query
"print"` continues to surface the stdlib `print` node even when "Hide
standard library" is ticked in the visualizer.

### How a node is classified

Every `symbol`, `file`, and `module` node in the graph carries a
`props.origin` value taking one of four lower-case strings:

| Value | Meaning |
|---|---|
| `project` | Defined in this repo, or in any federated child repo of the active workspace — the application code you wrote. |
| `stdlib` | Language standard library or built-in (Python builtins, `sys.stdlib_module_names`; C++ `std::` and toolchain headers; per-language equivalents). |
| `external` | Third-party dependency resolved outside the project tree but not part of the language stdlib (PyPI, npm, Cargo, vendored libraries). |
| `unresolved` | The discovery strategy could not determine the target's source — for example a `from foo import bar` whose `foo` does not exist. Hidden by default in the overview slice (the two checkboxes only toggle `stdlib` and `external`); a custom UI or scripted call can override this by passing an explicit `hide_origins` value to the API. |

The four values are exhaustive and mutually exclusive. Strategies that
emit `props.origin` directly are authoritative; legacy graphs without
the field are classified deterministically from existing signals
(`authority`, `resolved`, `symbol:unresolved:` ID prefix, edge-side
`props.resolution`). Re-running `wd discover` upgrades a legacy graph
to explicit origin tags. The graph schema reference in
[`docs/graph-schema.md`](docs/graph-schema.md) documents `props.origin`
alongside the other optional node props, including links to the design
notes and per-language detection rules.

### Driving the same filter from the API or URL

`wd viz` exposes the same filter as a query parameter on its slice
endpoints, which is useful when scripting screenshots or driving the
visualizer from another tool:

```text
GET /api/slice?hide_origins=stdlib,external
GET /api/slice?hide_origins=stdlib
```

`hide_origins` is a comma-separated list drawn from the four values
above. Omitting it falls back to the default overview behavior (hide
`unresolved` only). The `/api/summary` payload carries
`nodes_by_origin` (a per-origin count) so a custom UI can render the
same "(412)" hint next to its own toggles.

### Reviewing what changed in `wd viz`

The inspector panel carries a "Changes" tab that lists everything that
moved since the last `wd discover` run. Clicking a row jumps to the
node and tints it on the canvas (green for added, red for removed,
amber for modified). If the previous snapshot matches the current one,
the tab shows a friendly "No changes since last `wd discover`."
message. The same data is available over HTTP for custom UIs:

```text
GET /api/diff
```

The response wraps the stable JSON contract emitted by
`wd diff --json` (`added_nodes`, `removed_nodes`, `modified_nodes`,
`added_edges`, `removed_edges`) inside the shared `viz_api_version`
envelope, so a custom UI can render the same diff without re-running
the CLI.

## Examples

- [01-python-fastapi](examples/01-python-fastapi/) — discover a FastAPI
  project: routes, Pydantic models, module structure
- [02-custom-strategy](examples/02-custom-strategy/) — write a project-local
  strategy plugin that extracts TODO/FIXME comments
- [04-monorepo-typescript](examples/04-monorepo-typescript/) — discover a
  TypeScript monorepo: workspace packages, cross-package imports, shared types
- [05-polyrepo](examples/05-polyrepo/) — set up a federated polyrepo
  workspace: workspaces.yaml, cross-repo discovery, workspace status
- [agent-graph-demo](examples/agent-graph-demo/) — inspect mixed AI
  customization assets with `wd agents discover`, `list`, `audit`,
  `explain`, `impact`, and `plan-change`

For a tour of what each command above actually prints, see
[Graph visualization examples](docs/visualization-examples.md) — real
terminal snippets captured against `wd 0.26.0`.

## Install

### Recommended: `uv tool install`

```bash
uv tool install configflux-weld

# Verify
wd --version
```

This is the single recommended install path. `uv tool install` puts
`wd` on your `PATH` in an isolated environment, is fast, and gives you a
clear update story:

```bash
uv tool upgrade configflux-weld   # or: uv tool upgrade --all
```

Don't have `uv` yet? See the [uv install
instructions](https://docs.astral.sh/uv/getting-started/installation/).

To run the stdio MCP server, install the optional MCP extra (`mcp>=2,<3`):

```bash
uv tool install "configflux-weld[mcp]"
wd mcp serve --help
```

`wd mcp config` does not require the extra; only the server process does.
If a pre-2.0 `mcp` is already installed in that environment, upgrade it
with `pip install -U "mcp>=2"`.

### Alternative install paths

The paths below are supported but secondary. Prefer `uv tool install` unless
you have a concrete reason to pick one of these.

#### `pipx` (if you already standardize on pipx)

```bash
pipx install configflux-weld
wd --version
```

Functionally equivalent to `uv tool install` for end users. Use whichever
tool manager your team already has.

#### `install.sh` (zero-dependency bootstrap)

```bash
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
```

`install.sh` is a POSIX shell script that detects a compatible Python (3.10
through 3.13) and installs via `uv`, `pipx`, or `pip --user`, in that order
of preference. Use it only when you don't have `uv` or `pipx` available and
can't install them first — for example, on a minimal CI image or a
locked-down host. It is idempotent (re-running upgrades an existing install)
and honours a `.weld-version` file in the current directory or any ancestor
to pin a specific release tag.

#### From a local checkout (development)

If you want to edit Weld itself, use a source-checkout install. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the full developer setup, including
editable installs and optional-extras commands for `tree-sitter`, `mcp`,
`openai`, `anthropic`, `ollama`, and `llm`.

#### From a Git URL

```bash
pip install "git+https://github.com/configflux/weld.git@main#subdirectory=weld"
```

Useful for pinning an unreleased commit or branch.

#### Raw source (no install)

If you cannot install anything, the module entrypoint works from a plain
checkout:

```bash
python -m weld --help
```

This is a compatibility path, not the hardened one. `python -m` places the
working directory on Python's module search path before weld starts; weld
removes that entry before importing anything of its own, but a few
standard-library modules are imported by the interpreter first. See
[Trust model](#trust-model) for the exact boundary, and prefer `wd` when
scanning a repository you do not trust.

### Python compatibility

Runtime installs support Python 3.10 through 3.13. Contributor builds and
Bazel tests use the Python 3.12 toolchain pinned in `MODULE.bazel`, so the
development toolchain can be narrower than the runtime support window.

## Release policy

`main` is the source of truth for the next release: the version recorded
in [`VERSION`](VERSION) and `weld/pyproject.toml` matches the latest
`publish/vX.Y.Z` git tag, except during a deliberately-staged window
where `main` is bumped ahead of the latest tag.

The drift shape that produced the v0.9.0 and v0.10.1 incidents -- `main`
silently regressing below the latest published wheel -- is now caught
post-release by `tools/check_main_release_consistency.py` (runs as part
of the `/release-audit` flow). To document a deliberate
"`main` is ahead of the latest tag" window, add a comment marker to
this README:

```html
<!-- release-lag: 0.11.0 staged for 2026-05-12 launch window -->
```

The check then turns the lag into a `WARN` and surfaces the reason
instead of failing. Remove the marker when the matching tag is cut.
See [`docs/release.md`](docs/release.md) for the full release
checklist (the post-release consistency check is step 9).

## Documentation

- [Full toolkit guide](weld/README.md) — architecture, design limits,
  roadmap
- [Onboarding guide](weld/docs/onboarding.md)
- [Agent workflow](weld/docs/agent-workflow.md) — when to use each
  retrieval surface
- [Agent Graph](docs/agent-graph.md) — static map of the AI
  customization layer (agents, skills, prompts, hooks, MCP servers)
- [Graph-tracking policy](docs/graph-tracking-policy.md) — commit the
  graph or rebuild it? Mode A vs Mode B, what each tracks, and how to
  switch
- [Graph visualization examples](docs/visualization-examples.md) —
  real terminal output: monorepo graph, polyrepo `repo:` nodes,
  Agent Graph, MCP config snippet
- [Platform support matrix](docs/platform-support.md) — per-platform
  support and runtime-validation status
- [Performance notes](docs/performance.md) — discovery and query
  timings on synthetic 1k/10k/100k single repos and polyrepo workspaces,
  with a reproducible recipe
- [Strategy cookbook](weld/docs/strategy-cookbook.md)
- [Glossary](weld/docs/glossary.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Weld is currently maintainer-led.
Issues, bug reports, demo repos, documentation improvements, and strategy
proposals are welcome. For larger changes, please open an issue first so we
can align on scope before implementation.

## License

Apache License, Version 2.0 — see [LICENSE](LICENSE) for details.
