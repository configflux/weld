# Weld

Weld is a local codebase and agent graph for AI coding workflows.

It scans code, docs, CI, build files, runtime configs, repo boundaries, and
AI customization files into deterministic local graphs. Agents can query those
graphs through the `wd` CLI or MCP instead of rediscovering the repository from
scratch every session.

The primary graph lives at `.weld/graph.json`. Agent customization inventory
lives at `.weld/agent-graph.json`.

## Install

Recommended:

```bash
uv tool install configflux-weld
wd --version
```

Supported alternatives:

```bash
pipx install configflux-weld
pip install configflux-weld
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
```

Use the source checkout path only when developing Weld itself:

```bash
pip install -e weld/
```

Optional extras:

```bash
pip install "configflux-weld[tree-sitter]"  # broader language extraction
pip install "configflux-weld[mcp]"          # run wd mcp serve (stdio MCP server)
pip install "configflux-weld[openai]"       # or [anthropic], [ollama], [llm]
```

MCP config generation (`wd mcp config`) works in the default install. Running
the stdio MCP server requires the `mcp` extra, which pins `mcp>=2,<3`; a
pre-2.0 SDK is rejected with an upgrade hint (`pip install -U "mcp>=2"`).

## Quickstart

```bash
cd path/to/your/repo
wd init
wd discover --safe --output .weld/graph.json
wd build-index

wd query "authentication"
wd trace "how does this service start"
wd find "login"
wd brief "authentication"
wd context file:src/auth/handler
wd graph stats
wd graph communities --format markdown
wd graph validate
```

Use `wd graph communities --write` to derive `.weld/graph-communities.json`,
`.weld/graph-community-report.md`, and `.weld/graph-community-index.md` from
the current graph. The command is read-only with respect to `.weld/graph.json`.

`wd init` auto-detects per-language project shape and wires the matching
strategies into `discover.yaml` out of the box. For C# / .NET, when a repo
contains `.sln`, `.csproj`, or `Directory.Build.props` / `.targets`, the
generated config wires the solution and project graph (`ProjectReference`,
shared MSBuild properties), MSBuild targets with `BeforeTargets` /
`AfterTargets` ordering, and -- when the corresponding packages are
referenced -- ASP.NET Core controllers / routes, EF Core `DbContext` and
entities, and xUnit / NUnit / MSTest test-framework markers. No manual
edits to `discover.yaml` are required for the standard .NET layout.

When tree-sitter is available, exact identifier queries prefer definition
`symbol:` nodes for non-preview languages before falling back to owning files
or package-level matches.

The full five-minute tutorial is in the public repository:

https://github.com/configflux/weld/blob/main/docs/tutorial-5-minutes.md

## Agent Graph

Weld also maps the AI customization layer around a repository: agents, skills,
instructions, prompts, commands, hooks, MCP servers, tool permissions, and
platform-specific copies.

```bash
wd agents discover
wd agents list
wd agents audit
wd agents explain planner
wd agents impact .github/agents/planner.agent.md
wd agents plan-change "planner should always include test strategy"
wd agents viz --no-open
```

Agent Graph discovery is static and repo-bound. It reads known customization
files and does not execute project code. After discovery, `wd agents viz`
opens a local read-only browser explorer for `.weld/agent-graph.json`.

AI client platform coverage (Claude Code, Codex, Cursor, Copilot, Gemini,
OpenCode, generic MCP) is tracked in the AI client support matrix:

https://github.com/configflux/weld/blob/main/docs/platform-support.md

For programming-language coverage, see the next section.

## Language support

Weld's only built-in extractor is for Python. **Every other language listed
below depends on the `[tree-sitter]` optional extra.** Without it, the
tree-sitter strategies silently no-op on ImportError and the graph will
contain zero nodes for those languages — by design, so weld still runs in a
minimal environment. Install the extra to actually use multi-language support:

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
**Preview** (ships with documented correctness issues; not for production
use) → **Experimental** (opt-in extra, off by default) → **Not
supported**. Languages move tiers only via tier-check harness output, not
by editorial claim. The per-language Status column below is generated from
the harness baselines, so it always reflects the current verdict; a
language without a recorded baseline keeps its listed status pending its
own harness run.

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

**Frameworks** (reuse a language's extractor; status inherits from the host
language):

| Framework | Host language | Extraction surface | Status |
|---|---|---|---|
| ROS2 | C++ / Python | packages, nodes, topics, services, actions, parameters | Preview |

For the full per-language status discussion (including the C++ Tier-1
detail notes and the optional libclang path), see:

https://github.com/configflux/weld/blob/main/README.md#supported-languages

## MCP

Generate client snippets from any install:

```bash
wd mcp config --client=claude
wd mcp config --client=vscode
wd mcp config --client=cursor
```

Run the stdio MCP server from an environment that includes the optional SDK
at `mcp>=2,<3`:

```bash
uv tool install "configflux-weld[mcp]"
wd mcp serve --help
wd mcp serve
```

`wd` is a console script, so the directory the server is launched in -- the
repository being served -- is never placed on its module search path.
`python -m weld.mcp_server` remains supported for running from a source
checkout, where serving the checkout rather than an installed copy is the
point.

MCP documentation:

https://github.com/configflux/weld/blob/main/docs/mcp.md

## Trust Model

- Default discovery reads repository files and writes local graph data. It does
  not execute discovered application code and does not open network connections.
- `wd discover --safe` disables project-local strategies and external adapters.
- `wd enrich --safe` refuses network and LLM providers. `wd enrich
  --agent-direct` still works under it: it prints the enrichment work plan for
  the calling agent to follow and makes no network call.
- Project-local strategies and `external_json` adapters are trusted-repository
  features because they can execute code or commands during discovery.

Security policy:

https://github.com/configflux/weld/blob/main/SECURITY.md

## Local Telemetry

Weld records the success or failure of every `wd` CLI invocation and MCP tool
call to a local-only file. Nothing leaves your machine; there is no remote
endpoint and no upload.

Each event is one JSON line with a strict allowlist: subcommand or tool name,
exit code, duration in milliseconds, and the exception class name on failure.
Paths, query strings, error messages, flag values, and usernames are never
recorded. The redaction runs at write time, so the file on disk is already
safe to attach to a bug report.

In a single repo the file is `<repo>/.weld/telemetry.jsonl`. In a polyrepo
workspace every event aggregates into `<workspace_root>/.weld/telemetry.jsonl`.
Invocations outside any project fall back to
`${XDG_STATE_HOME:-~/.local/state}/weld/telemetry.jsonl`. The file is
gitignored and rotates at 1 MiB.

Opt out with any one of: `WELD_TELEMETRY=off`, `--no-telemetry`, or
`wd telemetry disable`. Use `wd telemetry --help` to inspect, export, or
clear the file. The full event schema and design are documented in
[`docs/telemetry.md`](https://github.com/configflux/weld/blob/main/docs/telemetry.md).

## Worktrees and multiple checkouts

Graph-backed reads answer from the checkout you are standing in. The root is
resolved from the current directory -- nearest enclosing `.weld/`, bounded by
the current git worktree, then the worktree root -- so a subdirectory of a
worktree never answers from another checkout, and the walk never crosses into
one. `--root` overrides it; `wd discover`, `wd init`, and `wd warm` keep
explicit-root semantics and default to the current directory.

A fresh `git worktree add` starts without a graph, so the first read seeds one
from another checkout of the same repository that already has a graph,
reconciles it to your branch, and prints a one-line notice. `wd stale` reports
the live `branch` beside the `graph_branch` the graph was built on.
`WELD_AUTO_REFRESH=0` disables the seeding along with auto-refresh.

Seeding reads the worktree's own `.weld/discover.yaml`, which git only puts
there when the repository tracks it -- the default `.weld/.gitignore` does.
A project that ignores all of `.weld/` disables seeding for every worktree;
`wd doctor` reports that, and the first read in such a worktree names the
missing file instead of only reporting that no graph was found.

Details:

https://github.com/configflux/weld/blob/main/README.md#worktrees-and-multiple-checkouts

## Polyrepo Federation

Weld supports workspace roots that contain multiple child Git repositories.
Each child keeps its own `.weld/graph.json`; the root graph records repo nodes
and cross-repo relationships without duplicating child content.

Start with:

```bash
wd init
wd workspace status
wd discover --safe --output .weld/graph.json
```

Workspace child scans discover gitignored child repos by default for
compatibility. To opt into Git ignore rules for scan-only children, set
`scan.respect_gitignore: true` in `.weld/workspaces.yaml` or run
`wd workspace bootstrap --respect-gitignore`. `scan.exclude_paths` also
accepts bare directory names, relative paths, and `*` / `**` glob patterns.

Example:

https://github.com/configflux/weld/tree/main/examples/05-polyrepo

## More

- Toolkit onboarding: [docs/onboarding.md](docs/onboarding.md)
- Agent workflow guide: [docs/agent-workflow.md](docs/agent-workflow.md)
- Strategy cookbook: [docs/strategy-cookbook.md](docs/strategy-cookbook.md)
- Templates: run `wd scaffold` to write starter strategy files.
- Repository: https://github.com/configflux/weld
- Changelog: https://github.com/configflux/weld/blob/main/CHANGELOG.md
- Community and support: https://github.com/configflux/weld/blob/main/docs/community.md
