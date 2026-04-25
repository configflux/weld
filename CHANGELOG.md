# Changelog

All notable changes to this project are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Conventions

- Each release is an `## vX.Y.Z - YYYY-MM-DD` section. Versions follow semver:
  major bumps for breaking changes, minor for new functionality, patch for
  fixes.
- Within a release, group entries under the standard subsections in this
  order: `### Added`, `### Changed`, `### Deprecated`, `### Removed`,
  `### Fixed`, `### Security`. Omit any subsection that has no entries for
  that release. The project also uses `### Release Safety` for changes that
  affect the publish/tagging path; it lives alongside the standard sections.
- Keep entries terse and user-focused. One bullet per change. Past tense.
  No `TODO`, `TBD`, or `WIP` placeholders -- the release-notes gate
  (`tools/release_notes.py`) will reject them.
- Heading shape (`## vX.Y.Z - YYYY-MM-DD`) is enforced by the publish gate.
  Do not add an `[Unreleased]` section; the release commit adds the new
  section in place. See [`docs/release.md`](docs/release.md) step 4 for the
  full release-day workflow.

For the broader release process (version triple, smoke test, tag pair, PyPI
publish), see [`docs/release.md`](docs/release.md). Launch readers asking
"what is new?" should be pointed at this file directly; the launch material
in [`docs/launch.md`](docs/launch.md) links here.

## v0.8.3 - 2026-04-25

### Fixed

- CHANGELOG entry for v0.8.2 listed `11 graph-backed tools` and named the
  nonexistent `weld_callees`. The MCP registry has had 13 tools since v0.8.2
  (matching `docs/mcp.md` and the in-process registry); the entry is
  corrected here so changelog readers and PyPI long-description match the
  shipped surface.

### Added

- `tools/mcp_tool_count_consistency_test.py` (`bazel test //tools:mcp_tool_count_consistency_test`)
  asserts that the MCP tool count and names stay in sync across
  `weld/_mcp_tools.py`, `weld/tests/mcp_expected_tools.py`, `docs/mcp.md`,
  and `CHANGELOG.md`. CI fails on drift.
- `tools/version_consistency_test.py` now also pins the `description` field
  in `weld/pyproject.toml` (non-empty, not the deprecated v0.7 string) and
  checks `public/weld/pyproject.toml` description matches when the publish
  staging directory is present.
- `tools/release_smoke.sh` now exercises `wd security`, `wd doctor --security`,
  `wd demo list`, `wd demo monorepo --init`, `wd demo polyrepo --init`, and
  `wd agents render --help` against the installed wheel.
- `tools/release_smoke.sh` runs an installed-extra MCP phase that builds
  the wheel, installs `configflux-weld[mcp]`, performs the stdio JSON-RPC
  `initialize` + `tools/list` handshake against `python -m weld.mcp_server`,
  and asserts the wire response lists exactly the 13 expected tools.

## v0.8.2 - 2026-04-25

### Added

- `wd security` (and `wd doctor --security` mode) shows trust posture as a
  scannable view: project-local strategies under `.weld/strategies`,
  `external_json` adapters in `.weld/discover.yaml`, enrichment provider
  network use, MCP importability, and safe-mode availability. Risk level
  rolls up to `low`/`medium`/`high` with recommendations; `--json` output
  is available for tooling. ADR 0025.
- `wd agents render` (preview) writes Agent Graph artifacts with a safe
  contract: dry-run/diff by default, `--write` required to write,
  `--force` required to clobber, provenance headers on rendered files,
  and a drift audit. ADR 0026.
- `wd demo` command family wraps the new bootstrap scripts: `wd demo
  list`, `wd demo monorepo --init <dir>`, and `wd demo polyrepo --init
  <dir>` for a frictionless first-run experience.
- `scripts/create-monorepo-demo.sh` and `scripts/create-polyrepo-demo.sh`
  build deterministic demo workspaces in a tempdir without manual nested
  `git init`. Fail gracefully when Git identity is missing.
- MCP server: 13 graph-backed tools (`weld_query`, `weld_find`,
  `weld_context`, `weld_path`, `weld_brief`, `weld_stale`, `weld_callers`,
  `weld_references`, `weld_export`, `weld_trace`, `weld_impact`,
  `weld_enrich`, `weld_diff`) return an actionable error payload when
  neither `.weld/graph.json` nor `.weld/workspaces.yaml` is present.
- Installed-wheel MCP smoke test (`weld_mcp_install_smoke_test`) builds
  the wheel, installs it, and asserts `python -m weld.mcp_server --help`
  works from the installed copy. Catches packaging regressions like the
  v0.8.0 missing-`weld.cross_repo` failure.
- Public Agent Graph guide at `docs/agent-graph.md`: what the Agent Graph
  is and is not, supported asset types and platform formats, node and
  edge types, an example graph, the `wd agents` commands, authority and
  drift, the read-only-first policy, render/export status, and known
  limitations. README links to it from key features, the Agent Graph
  quickstart, and the Documentation section.
- Platform fixtures for AGENTS.md, SKILL.md, and `.mcp.json` formats
  under `weld/tests/fixtures/agent_graph/` give deterministic Agent
  Graph coverage aligned with the platform-support claims.
- `docs/runtime-validation.md` records real-client validation entries
  (client version, date, tester, OS, scenario, result, notes) and is
  linked from `docs/platform-support.md`.
- `docs/visualization-examples.md` shows monorepo, polyrepo, Agent Graph,
  and MCP query terminal output captured from real demo workspaces.
- `docs/performance.md` reports measured `wd discover`, `wd query`, and
  `wd workspace status` timings at 1k / 10k / 100k file scales for
  single-repo and polyrepo workspaces, plus a reproducible synthetic
  generator at `weld/bench/synthetic_large_repo.py`.
- `docs/mcp-registry-submission.md` and
  `docs/mcp-registry-payload.yaml` draft the upstream MCP Registry
  submission. The submission is held until launch; this release ships
  the local draft only.

### Changed

- README, `CONTRIBUTING.md`, `docs/community.md`, `docs/launch.md`, and
  the changelog no longer reference GitHub Discussions. Open-ended
  feedback is routed to GitHub Issues; Discussions is deferred until
  there is a concrete reason to enable it.
- Public publish allowlist now includes `scripts/`, so the demo
  bootstrap scripts ship in the public package.

## v0.8.1 - 2026-04-25

### Fixed

- Hotfix: include the `weld.cross_repo`, `weld.bench`, and
  `weld.bench_tasks` subpackages in the published wheel. v0.8.0
  shipped without `weld.cross_repo`, so any fresh install crashed
  on `wd discover --help` with
  `ModuleNotFoundError: No module named 'weld.cross_repo'`. Users
  on 0.8.0 should upgrade to 0.8.1 immediately.
- Public CI: the agent-graph maintainer asset discovery test no
  longer asserts the presence of the internal-only
  `.claude/skills/agent-system-maintainer/SKILL.md`, which is
  excluded from the public overlay by `.publishignore`.

## v0.8.0 - 2026-04-25

### Added

- Agent-graph subsystem: a static, persisted graph of agents alongside the
  code graph. Schema vocabulary, persisted storage, static discovery, and
  metadata/reference parsing are now part of `wd discover`. New CLI surface:
  `wd agents discover|list|explain|impact|audit|plan-change` for inspecting
  the agent graph and reasoning about change impact, with authority-drift
  detection and a maintainer skill. Demo fixtures included.
- `wd discover --safe` refuses to run project-local strategy or extractor
  code when set, so an untrusted repository can be scanned without
  executing unreviewed Python from `.weld/strategies/`. `wd discover`
  without `--safe` now prints a one-time warning before running
  project-local code (ADR 0023/0024).
- `wd enrich --safe` refuses providers that would touch the network or an
  LLM, so enrichment can run in offline / sandboxed contexts without
  surprises.
- `wd mcp config --client={claude,vscode,cursor}` writes or merges the
  MCP-server entry for the chosen client. Malformed existing JSON in
  `--merge` mode now exits non-zero instead of silently overwriting.
- `wd stats` now surfaces top authority nodes, staleness, and a
  per-workspace breakdown by default. `--top N` controls the authority
  list size.
- `wd validate` emits actionable error diagnostics with suggested
  remediations, and gates federation bypasses on
  `schema_version: 2` so older graphs cannot accidentally use new
  cross-repo features.
- Federation: cross-repo resolvers declared in
  `cross_repo_strategies` are now executed during `wd discover` at a
  polyrepo workspace root, producing cross-repo edges in the federated
  graph.
- TypeScript discovery now pins `tree-sitter-typescript` and dispatches
  TSX files to the TSX grammar so React component exports are
  discovered correctly.
- `wd doctor` adds PM first-run UX sections covering install, init,
  discover, and graph health, and now documents and verifies its
  exit-code contract.
- Read-side commands (`wd query`, `wd context`, `wd trace`, `wd impact`,
  `wd diff`, `wd enrich`) print friendly guidance pointing to
  `wd discover` when the graph is missing, instead of stack traces.
- Examples: `examples/04-monorepo` ships a runnable PM demo with
  services, shared libs, Docker, CI, and docs.
  `examples/05-polyrepo` makes its `api` and `auth` services runnable
  via `uvicorn` and adds three children plus a cross-repo edge for
  federation demos.
- Docs: new `docs/mcp.md`, `docs/launch.md`, `docs/release.md`,
  `docs/community.md`, `docs/graph-schema.md`, and a 5-minute tutorial.
  README adds badges (CI, PyPI, Python versions, license),
  "Use Weld when…", "When not to use Weld", a comparison table, a
  trust-model section, sample output, an MCP section, and leads
  install with `uv tool install`.
- GitHub: issue templates and contact routing for incoming community
  reports; `docs/community.md` documents how feedback is organized.

### Changed

- The publish flow now ships a curated release-notes gate plus an
  install-test job that runs `wd doctor` (including against the public
  overlay) so packaging regressions surface before publish. ADR 0015
  formalizes a read-only `release-manager` agent that runs the
  pre-tag audit.
- `wd-retry-hint` formatting is centralized so retry guidance is
  consistent across CLI commands.
- `CHANGELOG.md` documents Keep-a-Changelog conventions and links to
  `docs/release.md`.

### Fixed

- `wd discover` honors brace globs (e.g. `**/*.{ts,tsx}`) in the
  `typescript_exports` strategy.
- `wd doctor` no longer fails when run inside an empty directory.

### Release Safety

- ADR 0015 release-manager agent + structured GO/NO-GO audit.
- Install-test job mirrored into the public overlay to catch
  packaging regressions in both surfaces.

## v0.7.0 - 2026-04-23

### Fixed

- `wd discover` no longer overwrites `.weld/graph-previous.json` before
  parsing the current graph. A corrupt `graph.json` now leaves the last
  good recovery snapshot intact so `wd diff` and manual recovery keep
  working.
- Repo boundary checks evaluate excludes against the logical
  (non-resolved) path first, so Bazel runfiles under `.cache/bazel/...`
  that symlink back into the repo no longer leak into `discovered_from`
  or graph nodes. `.cache` is now part of the always-excluded directory
  set.
- Recursive `glob:` patterns in `.weld/discover.yaml` no longer traverse
  excluded subtrees. Discovery uses an `os.walk`-based iterator that
  prunes `EXCLUDED_DIR_NAMES`, nested repo copies, and user `exclude`
  directories before descent; symlinks are not followed.

### Changed

- `exclude:` patterns in `.weld/discover.yaml` now match against the
  full repo-relative path, not just the filename. Segmented patterns
  like `.cache/**`, `compiler/**`, and `**/*.gen.py` work as expected;
  bare filename patterns (`README.md`, `*.pyc`) continue to match via
  a basename fallback. Source-level `exclude` is applied uniformly in
  `resolve_source_files`, so strategies no longer need to opt in for
  excludes to take effect. See ADR 0020.

## v0.6.0 - 2026-04-22

### Added

- Added atomic `wd discover --output PATH` writes so discovery can preserve
  incremental context and avoid truncating the existing graph before it is
  read.
- Added `wd bootstrap --diff` and `wd bootstrap --force` so existing agent
  bootstrap files can be compared with, or upgraded to, the bundled templates.
- Added `wd prime --agent {auto,claude,codex,copilot,all}` to surface missing
  bootstrap files for the active agent framework.
- Added a curated release-notes gate to the publish flow.

### Changed

- `wd prime` now bases graph freshness guidance on source-file staleness
  instead of SHA drift alone, preserving enriched graphs when tracked sources
  have not changed.
- Agent docs, examples, templates, and warnings now recommend
  `wd discover --output .weld/graph.json` as the primary graph refresh path.

### Fixed

- `wd` output piped into commands that close early, such as `head`, now exits
  quietly instead of printing a `BrokenPipeError` traceback.
- Graph-only commits after `wd touch` no longer cause repeated stale-graph
  prompts when source files are unchanged.

### Release Safety

- Public release commits and public annotated tags now force the configured
  release author/committer identity instead of inheriting local git config.

## v0.5.1 - 2026-04-21

### Added

- Added `wd find --limit N`; file-search results now include an integer
  `score` so callers can rank broad token matches.
- Added fallback behavior for `wd context <id>`: when an exact node id is not
  found, Weld searches for likely matches instead of returning an empty result.
- Expanded the edge vocabulary with governance and provenance edge types:
  `owned_by`, `gates`, `gated_by`, `supersedes`, `validates`, `generates`,
  `migrates`, and `contracts`.
- Added `wd touch` and source-file freshness metadata so graph snapshots can
  record the current git revision without changing nodes or edges.
- Added `wd workspace bootstrap`, a one-shot polyrepo setup flow that initializes
  the root, scans nested repositories, initializes children, runs recursive
  discovery, and rebuilds the root meta-graph.

### Changed

- Tool-generated edges should now record origin through `props.source`, with
  `confidence` using the existing `definite`, `inferred`, or `speculative`
  vocabulary.
- Workspace discovery now honors root `.gitignore` entries when scanning for
  nested repositories.
- `wd prime` avoids suggesting workspace bootstrap when only the shared MCP
  surface is missing.

### Fixed

- Bootstrap progress logs now go to stderr so JSON output remains parseable.
- Workspace bootstrap refreshes `workspaces.yaml` when the filesystem scan
  diverges from persisted child state.
- Recursive bootstrap failures are mirrored into `BootstrapResult.errors`.
- The bundled Weld README template no longer contains the placeholder project
  URL.

### Release Safety

- Public and private release tags are now validated against the expected commits
  instead of being silently skipped when they already exist.
- Public and private tag annotations now record both sides of the release
  mapping: version, private source commit, public commit, paired tag name, and
  UTC publish timestamp.
- Public release commits now carry both `Source-SHA:` and `Source-Tag:` lines.
- Publish version checks now require `VERSION`, `weld/pyproject.toml`, and
  `MODULE.bazel` to agree.
- The local release smoke now builds the staged public package from the same
  parent directory shape used by public CI.
