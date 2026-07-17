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

<!-- evaluator-note: latest=v0.22.0 -->
> **Evaluators: start with v0.19.1.** v0.19.1 is the current
> recommended starting point. Headline features added since v0.14.0:
> a 14-tool MCP server for graph-backed agent context
> (`weld_query`, `weld_find`, `weld_context`, `weld_path`,
> `weld_brief`, `weld_stale`, `weld_callers`, `weld_references`,
> `weld_export`, `weld_trace`, `weld_impact`, `weld_enrich`,
> `weld_diff`, `weld_review`); `wd impact` blast-radius queries
> driven by node, file list, working tree, or git diff range, with a
> stale-graph gate; `wd review` JSON-first triage for speculative
> edges; an end-to-end C# strategy stack (solution/project parsing,
> MSBuild targets, test-framework detection, ASP.NET routes, EF Core,
> inheritance edges, per-method call graphs) that auto-wires on
> `wd init` when matching artifacts are present; `wd init`
> auto-wiring of the interface strategies (gRPC `.proto` services and
> Python bindings, Kafka / Celery / Redis event channels,
> `runtime-contract.md` healthchecks, and generic DDS `.idl` data
> contracts and topic channels) when matching artifacts are
> present; multi-language origin classification, Bazel `srcs` /
> `deps` edges, Dockerfile and
> Compose copy edges, and multi-language test-peer edges across
> Python, Go, TypeScript / JavaScript, Rust, Java, C#, and C++;
> `wd communities` topic-level navigation of large graphs; opt-in
> eager inverted-index aggregation for faster cold-cache queries on
> large federations; a C++ amalgamation-file rank boost so single-file
> headers (e.g. `nlohmann/json`) surface ahead of incidental mentions;
> alias-aware lookup that resolves legacy node IDs through one minor
> version; and human-readable text output by default for the
> retrieval surface, with `--json` available for tools and the MCP
> server. ROS2 is labeled Tier 2 (preview) until its own harness pass
> runs; every other language family (Python, C#, Java, C++) follows
> the Tier-1 language support contract for entrypoints, modules,
> call graphs, test peers, and origin classification. See
> [`CHANGELOG.md`](CHANGELOG.md) for the per-release entries from
> v0.15.0 onward.

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
  `[weld] safe mode: ...` stderr line for each refused path.
- **Advanced strategies**: project-local strategies are Python modules
  loaded at discovery time, and `strategy: external_json` executes
  configured commands from `discover.yaml`. Only enable these on
  repositories you trust.

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
| TypeScript | exports, classes, imports | `tree-sitter-typescript` | **Tier 1** |
| JavaScript | exports, classes, imports | `tree-sitter-javascript` | Tier 2 |
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
configured, the prompt is replaced by a tip to run `/enrich-weld`.

For a source-checkout install (contributors editing Weld itself), see
[CONTRIBUTING.md](CONTRIBUTING.md).

Agents can also enrich nodes without provider extras or API keys by reading the
relevant source or documentation and writing reviewed enrichment manually:

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
re-enrich it. Manual inferred edges should use explicit provenance such as
`{"source": "manual"}` after the relationship is verified from source content.
`wd graph communities --write` derives `.weld/graph-communities.json`,
`.weld/graph-community-report.md`, and `.weld/graph-community-index.md`
from the existing graph without modifying `.weld/graph.json`.

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

Running the stdio MCP server requires the optional MCP SDK extra:

```bash
uv tool install "configflux-weld[mcp]"
python -m weld.mcp_server --help
```

Point your client at `python -m weld.mcp_server`:

```json
{"mcpServers": {"weld": {"command": "python", "args": ["-m", "weld.mcp_server"]}}}
```

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

### `.weld/.gitignore`

`wd init` and `wd workspace bootstrap` write a managed `.weld/.gitignore`
the first time they touch a `.weld/` directory (idempotent — never
overwrites an existing file). Three policies are available:

- **Default — config-only.** Tracks the source-of-truth config
  (`discover.yaml`, `workspaces.yaml`, `agents.yaml`, `strategies/`,
  `adapters/`, `README.md`) and ignores everything else weld writes,
  including the generated graphs (`graph.json`, `agent-graph.json`),
  graph-community reports (`graph-communities.json`,
  `graph-community-report.md`, `graph-community-index.md`),
  and per-machine state (`discovery-state.json`, `graph-previous.json`,
  `workspace-state.json`, `workspace.lock`, `query_state.bin`). A
  fresh contributor gets a clean `git status` after the first run.
- **Track-graphs (opt-in team workflow for warm CI / warm MCP).** Pass
  `--track-graphs` to widen the default so the canonical graphs are
  committed alongside config. Use this when every contributor should
  share a pre-built graph:

  ```bash
  wd init --track-graphs
  wd workspace bootstrap --track-graphs
  ```

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
**not** rewritten — the helper is idempotent. To pick up the new
default, delete the file and re-run init:

```bash
rm .weld/.gitignore
wd init                  # config-only default, generated graphs ignored
# or wd init --track-graphs   to keep tracking the graphs as before
```

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
| Read | `wd context` / `path` / `callers` / `references` / `find` / `communities` / `trace` / `impact` | Every read tool federates across children (the `wd` CLI and matching MCP tools alike); `trace`/`impact` reach child dependents via a read-time flatten, and `impact --from-diff`/`--working-tree` discover seeds from every present child's git repo |
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
  even when gitignored.
- **children**: Each entry has a `path` (relative to the workspace root)
  and an optional `name` (auto-derived from the path if omitted, e.g.
  `services/api` becomes `services-api`). Optional `tags` provide
  category metadata; optional `remote` records a clone URL.
- **cross_repo_strategies**: Ordered list of resolvers that produce
  cross-repo edges in the root graph. Available resolvers include
  `service_graph`, `grpc_service_binding`, `compose_topology`, and
  `channel_binding`.

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
federated graph -- `query`, `context`, and `path` plus `callers`,
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
wd workspace status --json   # raw JSON ledger
```

Example output:

```text
Workspace status (3 children)
Counts: present=1, missing=1, uninitialized=0, corrupt=0, stale=1
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
stale.

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
  children: 3 (1 stale)
    services-api: stale (source_changed, 1 behind)
```

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

Resolvers are read-only with respect to child graphs -- they never modify
a child's `.weld/graph.json`. Output edges are deterministic: identical
input produces byte-identical edges across runs.

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
| `wd init --track-graphs` | Seed `.weld/.gitignore` so canonical graphs (`graph.json` + `agent-graph.json`) stay tracked alongside config (warm-CI / warm-MCP workflow) |
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
| `wd workspace status` | Show workspace child ledger: lifecycle status, git ref, dirty state |
| `wd workspace status --json` | Emit the raw `workspace-state.json` payload |
| `wd workspace bootstrap` | One-shot polyrepo bootstrap: init root + every nested child, recurse-discover, rebuild root meta-graph (config-only `.weld/.gitignore` default) |
| `wd workspace bootstrap --respect-gitignore` | Skip scan-only child repos ignored by Git and persist `scan.respect_gitignore: true` into `workspaces.yaml` |
| `wd workspace bootstrap --track-graphs` | Bootstrap and seed `.weld/.gitignore` in root and every child to track canonical graphs alongside config |
| `wd workspace bootstrap --ignore-all` | Bootstrap and write a fully-ignoring `.weld/.gitignore` in root and every child; mutually exclusive with `--track-graphs` |
| `wd build-index` | Regenerate file index |
| `wd query <term>` | Hybrid-ranked tokenized graph search (strict-AND first; OR fallback when AND yields nothing on multi-word phrases — envelope is tagged with `degraded_match=or_fallback`). Multi-word phrases have common function-word stopwords (`the`, `is`, `how`, `of`, `for`, WH-words, …) dropped before matching so content tokens drive the result (a natural-language phrase like `"how does auth work"` searches on `auth`/`work`); single-word and symbol-id queries are never altered. Shows `confidence` per match and hides `origin=unresolved` sentinels by default (`--include-speculative` restores them). Bounds the neighborhood by default (drops stdlib/unresolved/speculative-external neighbors, caps fan-out, then a byte budget prunes to fit the tool cap — all reported in `omitted_neighbors`, including `size_capped`); `--full-neighborhood` restores the raw neighborhood and `--full-size` skips only the byte budget |
| `wd find <term> [--limit N]` | Broad file-token search, separate from graph discovery; each hit carries an integer `score` (default `--limit 20`). A single word is a case-insensitive substring match; a multi-word phrase is tokenized on whitespace and ranks files by how many of the words their tokens hit (so `wd find "mcp server"` surfaces `mcp_server.py`) |
| `wd context <id>` | Node + neighborhood (bounded by default, same as `wd query`; `--full-neighborhood` restores the full neighborhood, `--full-size` skips the byte budget) |
| `wd path <from> <to>` | Shortest path |
| `wd trace <term>` | Startup/runtime and interaction slice around a term or node |
| `wd impact <path-or-node>` | Reverse-dependency blast radius |
| `wd capabilities` | Runtime per-language / per-framework support matrix (`--json`, `--missing`) |
| `wd callers <symbol>` | Direct/transitive callers |
| `wd viz` | Local read-only browser graph explorer (sidebar toggles: **Hide standard library**, **Hide third-party dependencies** — see [Filtering noise in `wd viz`](#filtering-noise-in-wd-viz)) |
| `wd stale` | Check graph freshness |
| `wd <read-cmd> --no-refresh` | Skip the auto-refresh that runs when the graph is stale; a warning is emitted to stderr. Set `WELD_AUTO_REFRESH=0` to disable globally for CI / batch runs. |
| `wd graph stats` | Graph statistics |
| `wd graph communities [--format json\|markdown] [--top N] [--write]` | Detect deterministic graph communities, report top-level hubs, and optionally write derived JSON/report/index artifacts (unresolved-symbol nodes are excluded from the projected subgraph) |
| `wd stats` | Backward-compatible alias for `wd graph stats` |
| `wd graph validate` | Validate graph against the contract |
| `wd graph validate-fragment <file>` | Validate imported graph fragments and warn on trace-inert semantics |
| `wd validate` | Backward-compatible alias for `wd graph validate` |
| `wd migrate --add-confidence` | Backfill missing edge `confidence` props (`definite` / `inferred` / `speculative`) by classifying each edge from its `source_strategy`; strategies without a declared default land at `speculative`. Writes the graph back and emits a JSON report `{filled, unchanged, invalid}`. |
| `wd doctor` | Check setup health; exits 0 in directories that are not Weld projects yet |
| `wd prime` | Setup status + per-framework agent surface matrix (skill / instruction / mcp) with fix commands; `--agent {auto,claude,codex,copilot,all}` forces an agent row even when its framework files are absent |
| `wd scaffold` | Write starter templates |
| `wd bootstrap` | Agent onboarding files |
| `wd brief` | Agent context briefing |
| `wd enrich` | LLM-assisted semantic enrichment |
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

The valid note ids are `mcp-config-missing`, `optional-mcp-missing`,
`optional-anthropic-missing`, `optional-openai-missing`,
`optional-ollama-missing`, and `optional-copilot-cli-missing`. The
`copilot-cli` probe walks `WELD_COPILOT_BINARY` and `PATH` for the
standalone GitHub Copilot CLI binary, so its install hint points at
[github.com/en/copilot](https://docs.github.com/en/copilot/how-tos/use-copilot-cli)
rather than a `pip install` line.

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
terminal snippets captured against `wd 0.22.0`.

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

To run the stdio MCP server, install the optional MCP extra:

```bash
uv tool install "configflux-weld[mcp]"
python -m weld.mcp_server --help
```

`wd mcp config` does not require the extra; only the server process does.

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
