# Launch Material

This is the canonical "what do I point people at?" document for Weld. When
someone asks for a one-liner, a longer pitch, a demo, or what Weld does
*not* do, the answer lives here. Maintainers and contributors should treat
this page as the source of truth for launch copy and reuse the wording in
posts, talks, and slide decks.

This document is **scope-limited** by product decision: it does not contain
draft text for specific channels (HN, Show HN, /r/programming, LinkedIn,
MCP community announcements). Channel-specific drafts and scheduling are
maintainer-side artifacts owned outside `bd`. This page provides the raw
ingredients those drafts pull from.

For mechanics that are out of scope here:

- The PyPI release runbook (versioning, publish, smoke test, tag pair)
  lives in [`docs/release.md`](release.md). Launch and release are
  related but distinct -- a launch is a public announcement; a release is
  a version cut.
- The pre-release audit posture (eight checks, GO/NO-GO) is described in
  [`docs/adrs/0015-release-manager-agent.md`](adrs/0015-release-manager-agent.md)
  and invoked via the `release-manager` agent (`/release-audit`).
- Community channels (GitHub Discussions categories, enablement runbook)
  live in [`docs/community.md`](community.md). Launch posts should link
  readers there for open-ended feedback.
- Released features per version are tracked in [`CHANGELOG.md`](../CHANGELOG.md),
  which follows the Keep-a-Changelog convention. Launch readers asking
  "what is new?" should land there.

---

## One-line pitch

> Weld gives AI coding agents a local, deterministic graph of your
> codebase -- code, docs, CI, configs, and repo boundaries -- queryable
> through CLI or MCP, so agents stop rediscovering your repo on every run.

Use this verbatim in tag lines, social bios, and the GitHub repo
description. Keep it under 200 characters so it fits where it has to.

## Longer pitch (~paragraph)

> Weld is a local codebase graph for AI coding agents. It scans source
> code, documentation, CI workflows, build files, runtime configs, and
> repo boundaries into a deterministic graph that lives on disk under
> `.weld/`. Agents -- Claude Code, Codex, Copilot, Cursor, anything that
> speaks MCP -- query the same graph through the bundled MCP server or
> the `wd` CLI, so context follows the repo instead of the chat session.
> Discovery is config-driven and pluggable: point `.weld/discover.yaml`
> at your tree, drop custom strategies in `.weld/strategies/`, and Weld
> covers what an IDE, grep, vector store, or hosted code-search service
> were never built to handle: cross-language structure that includes
> docs, CI, contracts, and federation across multiple repositories.

Use this paragraph for blog posts, README descriptions on aggregators,
and the "About" section of channel-specific drafts. It is intentionally
honest about what Weld is and where it sits next to other tools.

---

## Demo commands (copy-paste ready)

These are the commands launch readers should be able to run after
installing Weld and arriving at any small-to-medium repo. They take
under a minute end-to-end on a typical Python or TypeScript project.

```bash
# 1. Install (uv tool is the recommended path; see README "Install"
#    for pipx, install.sh, and from-source alternatives).
uv tool install configflux-weld

# 2. Bootstrap config in your repo.
cd path/to/your/repo
wd init

# 3. Run discovery and write the graph to disk.
wd discover --output .weld/graph.json

# 4. Query the graph.
wd query "authentication"
wd find "login"
wd context file:src/auth/handler
wd path symbol:foo symbol:bar
wd stats
wd stale
```

The five-minute walkthrough in
[`docs/tutorial-5-minutes.md`](tutorial-5-minutes.md) extends this with
in-tree mono- and polyrepo examples (`examples/04-monorepo-typescript`
and `examples/05-polyrepo`). Point demo viewers there if they want a
guided tour rather than a copy-paste run.

For polyrepo workspaces (one root with several child git repos), see
the **Polyrepo Federation** section of the root [`README.md`](../README.md):
`wd init` at the root scaffolds `.weld/workspaces.yaml`, and discovery
emits cross-repo nodes plus runs the resolvers declared in
`cross_repo_strategies`.

---

## Output snippet: CLI

This is the kind of output a `wd query` produces against a small repo.
It demonstrates that results are structured nodes with provenance, not a
top-k similarity list:

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
  ]
}
```

What this shows that "fuzzy search" cannot:

- The match has a stable `id` you can pass back to `wd context` or `wd path`.
- The neighbour list points at a `route:/login` node -- a non-code entity
  Weld discovered alongside the symbol.
- The `calls` edge is a typed relationship, not a co-occurrence guess.

## Output snippet: MCP tool call

When an MCP-capable agent (Claude Code, Cursor, Codex) is configured to
talk to Weld's MCP server, it calls structured tools rather than running
shell commands. A `weld_query` invocation looks like this:

```json
{
  "tool": "weld_query",
  "input": { "term": "authentication", "limit": 5 },
  "output": {
    "matches": [
      {
        "id": "symbol:src/auth/handler.py:authenticate",
        "label": "authenticate",
        "type": "function",
        "score": 0.92
      }
    ]
  }
}
```

Demos for agent-tool audiences should land here: it makes the MCP
integration concrete and shows the exact shape an agent receives. The
full MCP tool reference (including `weld_find`, `weld_context`,
`weld_path`, `weld_brief`, `weld_enrich`, and `weld_stale`) lives in
[`docs/mcp.md`](mcp.md).

---

## Comparison table (canonical source: README)

The honest, side-by-side comparison of Weld against `grep`/`ripgrep`,
`ctags`/LSP, Sourcegraph, vector DB / RAG, and Copilot / Claude Code /
OpenCode is maintained in the root README under
[**How Weld compares**](../README.md#how-weld-compares). Launch copy
should **link to that section rather than duplicate the table**, so we
do not maintain two drifting versions.

The same README also carries the **Use Weld when…** and **When not to
use Weld** sections directly above the table. Both are worth pointing
launch readers at: they signal we know where Weld does not belong, which
is more credible than a pitch alone.

---

## Known limitations

Be upfront about these. Repeat them in posts and talks rather than
hide them -- audiences trust honest scope statements far more than
marketing copy.

- **Discovery is pragmatic, not compiler-grade.** Tree-sitter strategies
  cover the common shape of each language but will miss edge cases and
  unusual macros, generated code, or dynamic imports. Weld is not a type
  checker or a dataflow engine.
- **Graph freshness is on the user.** The graph is a file on disk; it
  goes stale when the repo changes. Use `wd stale` in CI or pre-commit,
  and re-run `wd discover` after large changes. There is no background
  watcher today.
- **Bundled languages are finite.** Python, TypeScript/JS, Go, Rust, C#,
  C++, and ROS2 ship as built-in strategies. Anything else needs a custom
  strategy in `.weld/strategies/` (see `examples/02-custom-strategy`).
- **Semantic enrichment is opt-in and LLM-bounded.** `wd enrich` adds
  human-readable descriptions to nodes, but only as far as the configured
  provider can. The `--safe` flag refuses any network or LLM call so
  CI-bound runs stay deterministic.
- **Polyrepo federation requires nested git repos.** The federated mode
  expects each child to be its own git repository under the workspace
  root with its own `.weld/`. Submodule-flattened layouts and monorepos
  inside a polyrepo are not the federation target -- a regular `wd
  discover` covers a single repo.
- **No hosted indexer, no fleet mode.** Weld is a local-first tool. There
  is no SaaS, no shared graph store, and no organisation-wide search
  surface. Teams that need cross-repo search across hundreds of repos
  with central hosting should keep using Sourcegraph or similar; Weld
  complements them, it does not replace them.
- **MCP-tool API surface is still settling.** The tool names listed in
  the README and `docs/mcp.md` are stable for the current minor version,
  but expect refinements. Pin to a specific Weld version in client
  configs if your workflow cannot tolerate tool-surface change between
  releases.

---

## Short FAQ

### Is this another RAG tool?

No. RAG and vector stores return top-k fuzzy chunks; Weld returns exact
nodes and edges with provenance. Agents can follow relationships --
"which routes call this function?", "which docs cover this module?",
"which CI workflow gates this directory?" -- without re-embedding the
repo. Weld and RAG are complementary, not competitive.

### Do I have to use Claude Code?

No. Weld speaks MCP, so any MCP-capable agent works: Claude Code,
Codex, Cursor, VS Code, and others. The CLI (`wd`) also works on its
own without any agent at all -- humans use it directly to navigate
unfamiliar codebases.

### Does Weld send my code anywhere?

By default, no. Discovery is local; the graph is a file on disk under
`.weld/`. The only network-touching surface is `wd enrich` with a
configured LLM provider, and it can be locked off with `--safe`. There
is no telemetry, no upload, and no hosted backend.

### How big a repo can Weld handle?

Discovery is designed to be deterministic and scoped, not exhaustive.
Repositories with tens of thousands of files run in tens of seconds on
a laptop. Polyrepo workspaces fan out per child repo and assemble a
federated meta-graph; each child stays the size of its own repo.

### What does Weld not do?

Weld does not provide go-to-definition (an LSP job), full-text search
with regex flags (`grep` / `ripgrep`), or compiler-grade dataflow.
The README's **When not to use Weld** section is the canonical list;
launch copy should link there rather than re-state the same caveats.

### Where do I send feedback?

Bug reports and concretely-scoped feature requests go in GitHub Issues.
Open-ended feedback (architecture ideas, setup show-and-tell, MCP
client questions, strategy requests, polyrepo recipes) belongs in
GitHub Discussions. The categories and enablement runbook for
Discussions live in [`docs/community.md`](community.md). Launch posts
should point readers there.

### How do I keep up with releases?

The [`CHANGELOG.md`](../CHANGELOG.md) at the repo root follows the
Keep-a-Changelog convention with `Added` / `Changed` / `Fixed` /
`Removed` sections per release. The release process itself --
versioning, publish, smoke test, tag pair -- is documented in
[`docs/release.md`](release.md). Watch the GitHub repo or the PyPI
project page for new versions; tags are signed and paired across the
internal and public repositories.

---

## Maintenance

Treat this document like any other launch asset: keep it short, keep
it honest, and update it when the underlying claims change. Specifically:

- If the **comparison table** in the README changes, do not duplicate
  the change here -- the link to the README section is the contract.
- If the **bundled languages** list changes, update the *Known
  limitations* bullet so readers see an accurate scope statement.
- If the **MCP tool surface** changes in a way that affects the example
  call shape, update the *Output snippet: MCP tool call* section so
  demos reflect what agents actually see.
- If new **community channels** open or close, edit the FAQ entry that
  routes feedback rather than scattering links in multiple places.

When in doubt, keep this file under the 400-line cap and prefer linking
to the canonical doc (README, CHANGELOG, `docs/release.md`,
`docs/community.md`, `docs/mcp.md`) over inlining content here.
