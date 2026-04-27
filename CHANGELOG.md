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

## v0.11.3 - 2026-04-27

### Fixed

- `wd workspace bootstrap` no longer misroutes a nested-git polyrepo to single-service mode after a `.weld/` reset. Two federation predicates disagreed: `wd discover` decides federation by config presence, while bootstrap used a filesystem-only scan that honoured root `.gitignore`, `DEFAULT_MAX_DEPTH=4`, and `_BUILTIN_EXCLUDE_DIRS`. After `rm -rf .weld/` the FS scan could return zero even when the operator had restored a valid `.weld/workspaces.yaml`, leaving the workspace stuck without `.weld/workspace-state.json` and breaking `wd workspace status`. Bootstrap now uses a unified merge predicate where `workspaces.yaml` is authoritative when present and the FS scan augments it; corrupt yaml falls back to scan with the parse failure surfaced in `BootstrapResult.errors`. `wd init` at a polyrepo root now also runs per-child init so every child gets its own `.weld/discover.yaml`.
  <!-- verify: file=weld/_workspace_bootstrap.py grep=yaml_listed_but_missing -->

## v0.11.2 - 2026-04-27

### Release Safety

- v0.11.0 and v0.11.1 were tagged but the wheel never reached PyPI: the public CI's `MCP extra handshake on built wheel` step returned only the `initialize` response (id=1) and not the `tools/list` response (id=2). The local task gate, smoke tests, and the new local CI replica all passed against the same wheel — a github.com-runner-specific stdio race in the wrapper's `Popen + sleep + communicate()` shape. Wheel content unchanged.
  <!-- verify: file=tools/publish_overlays/release_mcp_handshake.py grep=_read_response_with_id -->
- The handshake wrapper is now fully synchronous on both sides: send `initialize`, **block on the id=1 response from stdout** with a per-step timeout, then send `notifications/initialized` + `tools/list`, then **block on the id=2 response**. No reliance on stdin EOF or sleep heuristics. The previous race is structurally impossible.
- New `tools/local_publish_dryrun.sh`: builds the wheel locally, installs into a fresh venv with `[mcp]`, runs the OVERLAY handshake — the exact chain the public CI runs. Wired into `tools/publish.sh` as a precondition so any future overlay-vs-CI drift fails locally before any tag is pushed.
  <!-- verify: file=tools/local_publish_dryrun.sh grep=Local replica of the public -->

## v0.11.1 - 2026-04-27

### Release Safety

- v0.11.0 was tagged on both repos but the wheel never reached PyPI: the new `pre-tag-verify` job (ADR 0032) added in v0.10.2 cycle invokes `tools/release_claims_lint.py`, which is internal-only (`.publishignore`) and therefore absent on the public mirror. The job failed with `No such file or directory`, blocking `build-and-publish` via its `needs: pre-tag-verify` dependency. The wheel content is unchanged from v0.11.0 — same matter-of-substance release.
  <!-- verify: file=tools/publish_overlays/publish-pypi.yml grep=release_claims_lint.py not present -->
- v0.11.1 makes the public-repo `pre-tag-verify` step skip cleanly when the verifier is missing. The same check still runs locally via `./local-task-gate` before any tag push, so the strict CHANGELOG-vs-tree assertion is not lost — it is enforced where the script actually lives.
- Users who saw the v0.11.0 tag should install v0.11.1; `pip install configflux-weld` continued to serve v0.10.1 in the gap between the v0.11.0 tag and v0.11.1.
  <!-- verify: file=weld/pyproject.toml grep=0.11.1 -->

## v0.11.0 - 2026-04-27

### Added

- `wd bootstrap` adopts a managed-region marker model (ADR 0033). Each bundled template under `weld/templates/` declares one or more `<!-- weld-managed:start name=... -->` regions; `wd bootstrap <fw> --diff` and the writer's no-op / refuse / clobber / append paths operate **inside** those markers only. Operator-curated content outside the markers is left untouched after the first write, so a single edited line outside a managed region no longer reads as a full-file replacement in `--diff`.
  <!-- verify: file=weld/templates/weld_skill_copilot.md grep=weld-managed:start -->
- `wd bootstrap` ships `--include-unmanaged`: paired with `--diff`, it falls back to the whole-file unified diff for operators who want to fully resync past the managed-region scope. The flag is rejected with a clear error when used outside `--diff`.
- The publish workflow runs a Python 3.10/3.11/3.12/3.13 wheel-install smoke matrix on every release before the irreversible PyPI upload. The MCP handshake gate now succeeds on all four supported Python versions; the wrapper keeps stdin open and drains stdout incrementally so the mcp library's anyio reader is no longer cut off by an early stdin EOF on 3.10/3.11/3.13.
  <!-- verify: file=tools/publish_overlays/publish-pypi.yml grep=python-version: ${{ matrix.python-version }} -->
- `wd brief` falls back to an OR-of-tokens retrieval when its strict AND query returns zero matches on a multi-token query. The fallback result carries `degraded_match: "or_fallback"` so callers know they did not get the strict-AND ranking. `graph.query()`'s AND semantics are unchanged.
  <!-- verify: file=weld/brief.py grep=or_fallback -->
- `tools/check_main_release_consistency.py` and the new ADR 0015 check #11 fail a release if local `main` has drifted behind the latest published wheel without an explicit, documented lag.
- Live-client runtime validation now has a real Codex AGENTS.md + skill record and clearly-marked `result: pending` stubs for Claude Code MCP, Claude Code skill/subagent, and VS Code Copilot custom instructions. A new launch-copy guard rejects platform claims in launch material that are not backed by a recorded row.
  <!-- verify: file=tools/runtime_claims_launch.py grep=def lint_launch_copy -->

### Changed

- The pre-marker-layout migration: `wd bootstrap` prints an actionable message and exits non-zero on files that contain no `weld-managed:start` line; `--force` re-seeds the file with the bundled template verbatim (markers and all). No silent corruption, no heuristic anchor matching.
  <!-- verify: file=weld/bootstrap_managed.py grep=pre_marker_message -->
- The `_FEDERATION_PARAGRAPH` block appended in federation mode is itself a managed region named `federation`, so federated workspaces get the same drift-detection treatment as the rest of the bootstrap surface.
  <!-- verify: file=weld/bootstrap.py grep=name=federation -->
- README's comparison-table row for Sourcegraph drops the misleading "you commit with your code" line; the row now describes the actual config-only default and the `wd init --track-graphs` opt-in.
  <!-- verify: file=README.md grep=lives next to your code -->
- The Copilot bundled skill template (`wd bootstrap copilot`) installs `weld` via `uv tool install configflux-weld` instead of the contributor `pip install -e ./weld` path.
- `tools/check_main_release_consistency.py` parses the version with a `[project]`-section-anchored regex so a future `[tool.foo]` table cannot shadow the canonical project version.
- Bootstrap design and migration semantics captured in [ADR 0033](docs/adrs/0033-bootstrap-managed-content.md).
  <!-- verify: file=docs/adrs/0033-bootstrap-managed-content.md grep=Managed-vs-curated -->

### Fixed

- `wd discover` in federated workspaces now stamps `meta.git_sha` on the root meta-graph. Single-repo discover already did so; the federated path skipped it, which made `wd prime --agent all` always print "graph.json has no git SHA — may be stale" immediately after a successful discover.
  <!-- verify: file=weld/federation_root.py grep=git_sha -->

### Security

- The release-claim verifier (ADR 0032) bounds user-supplied regex patterns at 256 chars and file content at 10 MB before running `re.search`, eliminating a CHANGELOG-bullet-authored CI DoS lever via catastrophic backtracking.
  <!-- verify: file=tools/release_claims_bounds.py grep=MAX_REGEX_LEN -->
- Every job in `tools/publish_overlays/publish-pypi.yml` now SHA-pins `actions/checkout` and `actions/setup-python` to immutable commits, matching the existing pin on `pypa/gh-action-pypi-publish`. No moving major-version tags remain in the publish workflow.

### Release Safety

- ADR 0015 grew checks #10 (release-claim verifier — Guardrail-1, ADR 0032) and #11 (public-main consistency check) since v0.10.1. Drift between CHANGELOG bullets and the working tree at tag time is now blocked by the local gate and the publish workflow's `pre-tag-verify` job, not just by reviewer attention.
  <!-- verify: file=docs/adrs/0015-release-manager-agent.md grep=check 11 -->

## v0.10.1 - 2026-04-26

### Release Safety

- v0.10.0 was tagged on both repos and a GitHub Release page was created, but the wheel was never uploaded to PyPI: the new wire-level MCP-handshake gate added in v0.10.0 (invoked from `publish-pypi.yml`) hung on Python 3.11 in CI — the server returned the `initialize` response but no `tools/list` response after stdin EOF, and the gate aborted the upload before the irreversible PyPI step. Local smokes on Python 3.12 pass against the same wheel, so this is a CI-environment bug, not a wheel bug.
- v0.10.1 unblocks the publish path by pinning `publish-pypi.yml` to Python 3.12 for both the build-and-publish and verify-install jobs. Root-cause investigation of the 3.11 hang is tracked in a follow-up bd issue; the gate's expected-tool fixture and behavior are unchanged.
- Users who saw the v0.10.0 GitHub Release should install v0.10.1 — `pip install configflux-weld` continued to serve v0.9.0 in the gap between the v0.10.0 tag and v0.10.1.
  <!-- verify: file=weld/pyproject.toml grep=0.10.1 -->

## v0.10.0 - 2026-04-26

### Added

- `wd init --track-graphs` is now actually shipped in the wheel. The opt-in keeps generated graphs (`graph.json`, `query_state.bin`, etc.) tracked in git so warm-CI / warm-MCP setups continue to work; without the flag the managed `.weld/.gitignore` follows the config-only default.
- Public PyPI publish workflow runs an MCP-handshake smoke against the freshly built wheel before the irreversible upload. If the wheel cannot start the MCP server end-to-end, the upload is aborted.
- `markdownlint-cli2` is now part of the repo lint pass, scoped to shipped docs (README, `docs/**/*.md`).
- `tools/doc_version_lint.py` blocks stale `wd <version>` references in shipped docs so older versions cannot leak into a newer release's documentation.
- Public install/contributor docs split into separate audiences in `README.md` and `CONTRIBUTING.md` so downstream consumers do not have to skim past contributor-only setup.

### Changed

- Public-facing runtime-validation copy tightened, and a dedicated `runtime_claims_lint` checks that documented runtime claims match the code.

### Fixed

- Publish allowlist accepts the new `.markdownlint.json` and `.markdownlintignore` files at the repo root, so the markdownlint addition does not block `tools/audit_publish.sh`. A redundant entry in the markdownlint ignore that referenced the bd ledger directory was also removed; markdownlint was never scanning that path.

### Release Safety

- v0.9.0's CHANGELOG promised `wd init --track-graphs`, but the implementing commit landed after the v0.9.0 tag. The v0.9.0 PyPI wheel therefore does **not** contain the flag. v0.10.0 closes that gap; users on v0.9.0 should upgrade to get the documented behavior.

## v0.9.0 - 2026-04-26

### Added

- `wd agents audit --strict` surfaces ADR-0029-suppressed groups (canonical/rendered pairs no longer hide audit findings when strict mode is set).
- `WELD_INIT_FRAMEWORK_CAP` env override lets forensic re-runs of `wd init` raise or remove the per-language framework sample cap; `0` disables the cap, custom positive integers set a custom cap, unset/empty/negative/non-numeric values fall back to the built-in default silently.
- Query state sidecar (ADR 0031): `wd query` now persists the inverted index and BM25 corpus to `.weld/query_state.bin` after `wd discover`, so cold-path query startup drops from ~1.28 s to ~0.54 s on a representative 100k-node graph (about 58% faster). The sidecar is content-addressed via blake2b digest + node count + weld schema version + format-version envelope; on freshness mismatch or corruption the sidecar is silently rebuilt.
- `wd init` and `wd workspace bootstrap` seed a managed `.weld/.gitignore` on init and bootstrap. The default policy is **config-only**: it tracks source-of-truth config (`discover.yaml`, `workspaces.yaml`, `agents.yaml`, `strategies/`, `adapters/`) and ignores everything weld can rebuild, including generated graphs (`graph.json`, `agent-graph.json`) and per-machine state (`discovery-state.json`, `graph-previous.json`, `workspace-state.json`, `workspace.lock`, `query_state.bin`). Pass `--track-graphs` to also commit the canonical graphs (warm-CI / warm-MCP workflow), or `--ignore-all` to ignore every weld file. The two opt-in flags are mutually exclusive. Pre-existing `.weld/.gitignore` files are not rewritten; manual migration is `rm .weld/.gitignore && wd init`.
- `wd demo polyrepo --init` auto-bootstraps the workspace before discovery so the first run produces a populated graph instead of an empty one.
- Bootstrap traceback surfaced under `WELD_DEBUG=1` in `wd demo polyrepo` so the demo's bootstrap exception handler shows the underlying cause when set.

### Changed

- Edge-type weighted impact and plan-change ranking (ADR 0030): `_score_asset()` and `_secondary_assets` consult an edge-weight table (semantic=5.0, related=2.0, incidental=0.5) and a `SECONDARY_THRESHOLD=1.0`. Canonical-authority assets bypass the secondary threshold so authoritative nodes always render even when only attached via low-weight edges (ADR 0030 amendment).
- `wd init` framework detection merged into a single classifier pass (`_init_classify.py`); per-file `detect_*` walks coalesce, dropping a representative `wd init` cold run from 41.2 s to 8.6 s on a 100k synthetic tree (about 79% faster). No behavior change vs. multi-pass detection — same constants, same heuristics.
- `wd discover` now warns on stderr when the prior `graph.json` is unreadable instead of silently rewriting it. The previous graph is preserved untouched if the load fails; operators see the failure and can decide whether to rerun.
- `agent_graph_render_pairs` only honors `render_paths` from `authority="canonical"` nodes (ADR 0029 §5 trust-boundary amendment). Non-canonical nodes can no longer suppress duplicate-name audit findings via render-paths.

### Fixed

- Go gin framework detection now matches the canonical `github.com/gin-gonic/gin` import path; the quoted-path matcher pre-filters block comments and raw-string literals so commented-out imports and string-fixture content no longer trigger false positives.
- `unused_skill` audit suppression tightened to a word-boundary regex match and respects skill name mentions in agent body / instruction text, eliminating substring false positives.
- Bench `test_discover_stability` no longer flakes against tiny-time clocks (sub-millisecond mtime resolution).
- Internal references sanitized in the gitignore writer so the seeded `.weld/.gitignore` does not leak internal-repo conventions.

### Refactor (internal)

- `agent_graph_assets` extracted to break a circular import in the agent-graph rendering path.
- `_CLEAR_DESCRIPTION_TYPES` and `_VAGUE_DESCRIPTIONS` shared via `_agent_graph_constants` to remove duplicate audit constants.
- `atomic_write_bytes` promoted to a shared workspace-state helper alongside `atomic_write_text` for binary sidecar writes.

## v0.8.3 - 2026-04-25

### Fixed

- CHANGELOG entry for v0.8.2 listed `11 graph-backed tools` and named the
  nonexistent `weld_callees`. The MCP registry has had 13 tools since v0.8.2
  (matching `docs/mcp.md` and the in-process registry); the entry is
  corrected here so changelog readers and PyPI long-description match the
  shipped surface.

### Added

- Internal release gate now asserts the MCP tool count and names stay in
  sync across the in-process registry, the expected-tools fixture,
  `docs/mcp.md`, and `CHANGELOG.md`. CI fails on drift.
- Internal release gate pins the `description` field in
  `weld/pyproject.toml` against the deprecated v0.7 string, so a stale
  description cannot ship.
- Internal release smoke now exercises `wd security`,
  `wd doctor --security`, `wd demo list`, `wd demo monorepo --init`,
  `wd demo polyrepo --init`, and `wd agents render --help` against the
  installed wheel.
- Internal release smoke installs `configflux-weld[mcp]` and runs a
  stdio JSON-RPC `initialize` + `tools/list` handshake against
  `python -m weld.mcp_server`, asserting the wire response lists the
  expected 13 tools. The public publish workflow ships an equivalent
  wire-handshake check (see v0.9.0 release safety wiring).

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
