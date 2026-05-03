<!-- markdownlint-disable MD013 -->
# Changelog

All notable user-facing changes to this project are recorded here.

## v0.16.0 - 2026-05-03

### Added

- Canonical node-ID library (`weld._node_ids`) provides one slug rule and one
  shape rule for `file:`, `package:`, and entity IDs across every discovery
  strategy. Replaces three divergent local `_slug` implementations with a
  shared `canonical_slug` so paired strategies cannot drift on character-set
  rules.
  <!-- verify: file=weld/_node_ids.py grep=canonical_slug -->
- Unified `ensure_node` primitive (`weld._graph_node_registry`) replaces the
  twelve ad-hoc `_ensure_*` helpers across the agent-graph, ROS2, and
  graph-closure layers. Two creation paths for the same logical entity now
  merge into one node with set-union of `sources`, `aliases`, and list props,
  and authority-precedence resolution on conflicts. Order-independent by
  construction.
  <!-- verify: file=weld/_graph_node_registry.py grep=ensure_node -->
- Three new architectural lint rules close the closure-determinism gap by
  construction: `canonical-id-uniqueness` rejects two non-aliased nodes
  sharing a normalized canonical key; `file-anchor-symmetry` rejects file
  nodes with outgoing `contains` but no inbound edge; and
  `strategy-pair-consistency` rejects file-set drift between paired
  strategies (e.g. `python_module` vs `python_callgraph`).
  <!-- verify: file=weld/_graph_closure_invariants.py grep=check_canonical_id_uniqueness -->
- Alias-aware lookup resolves legacy node IDs to their canonical form across
  `wd query`, `wd context`, `wd path`, `wd export`, and the MCP server. The
  alias index is built once at graph-load time and rides alongside the BM25
  cache in `_query_sidecar`; the index invalidates by graph hash so old IDs
  cannot bleed across upgrades. Defensive collision guard refuses to alias
  into another node's canonical id.
  <!-- verify: file=weld/_alias_index.py grep=build_alias_index -->
- `python_package` strategy emits `package:python:<dotted>` nodes with
  `contains` edges to each `*.py` file member, including a synthetic
  `package:python:tools` namespace for `tools/` scripts that lack a Python
  package marker file. Closes 134 file-anchor-symmetry violations the rule
  would otherwise flag for the weld and tools surfaces.
  <!-- verify: file=weld/strategies/python_package.py grep=_dotted_name -->
- `python_module._extract_imports` now walks function-local lazy imports in
  addition to top-level imports, matching the qualified `from foo import _bar`
  pattern that strategy plugins use to avoid circular dependencies. Captures
  the lazy import in `weld/strategies/ros2_topology.py:347` so
  `file:weld/strategies/_ros2_py` gains an inbound `depends_on` edge.
  <!-- verify: file=weld/strategies/python_module.py grep=_extract_imports -->

### Fixed

- Skill nodes no longer duplicate when the same logical skill is reached via
  multiple discovery paths. Previously `skill:generic:architecture-decision`
  surfaced as two separate nodes (one with edges, one orphan) because the
  collision-suffix mechanism in `agent_graph_materialize._node_id_for_values`
  hashed the discovery path into the ID. The merge now happens at construction
  via `ensure_node`, with all source paths recorded under `sources` and prior
  hash-suffixed IDs preserved under `aliases`.
  <!-- verify: file=weld/agent_graph_materialize.py grep=ensure_node -->
- `file:weld/strategies/_ros2_py` is no longer a structurally orphan file
  anchor. Two independent fixes contribute: the lazy-import capture above
  surfaces the `depends_on` edge from `ros2_topology`, and the new
  `python_package` strategy supplies the `contains` edge from
  `package:python:weld.strategies`.
  <!-- verify: file=weld/_graph_closure_invariants.py grep=check_file_anchor_symmetry -->

### Breaking changes

- **Graph node IDs renamed — Python file anchors.** Python file
  IDs change shape from `file:{stem}` to `file:{rel_posix_path_without_ext}`
  in 0.16.0 to eliminate stem-only collisions across directories
  (`weld/strategies/python_module.py` and `tools/python_module.py` previously
  collapsed onto the same `file:python_module` ID). The `python_module`
  strategy also drops its unilateral `_*`-skip rule so paired strategies
  (`python_module` + `python_callgraph`) process the same input set; the
  symptom this closes is the orphan `file:weld/strategies/_ros2_py` shape
  reported on 2026-05-02.

  Old IDs are preserved on each renamed node's `props.aliases` list for one
  minor version. `wd query`, `wd context`, the MCP server, and the query
  sidecar resolve old IDs transparently through the alias index (alias-aware
  lookup ships in PR 2). Sidecar caches invalidate by graph hash on first
  discovery after upgrade; no stale-cache hits possible.

  **User-visible impact:**
  - Existing MCP conversation transcripts referencing old `file:<stem>` IDs
    continue to work via alias lookup.
  - Prior `wd query` JSON output that recorded an old ID can be fed back
    into `wd context <old-id>` and will resolve.
  - Files starting with `_` (other than the package init module) now appear
    as graph nodes; expected node-count delta is small but non-zero on this
    repo.

  **Deprecation:** aliases retire in 0.17.0. Pin to 0.16.x if external
  scripts assume the old prefix and cannot be updated before the next minor.

- **Skill node IDs.** Skill IDs no longer carry a SHA1 hash
  suffix when the same logical skill is reached via multiple discovery paths.
  Two-path collisions now merge into one node with both source paths recorded
  under `sources` and the prior suffixed IDs preserved under `aliases` for one
  minor version. Agent transcripts referencing `skill:generic:foo:abc12345`
  still resolve.
  <!-- verify: file=weld/agent_graph_materialize.py grep=legacy_skill_id_with_suffix -->

- **ROS2 cluster IDs renamed.** ROS2 package IDs change shape
  from `ros_package:<name>` to `package:ros2:<slug>` across the
  `ros2_package`, `ros2_interfaces`, `ros2_topology`, `ros2_launch`, and
  `ros2_cmake` strategies. ROS2 file-anchor IDs follow the same
  `file:<rel_posix_path_without_ext>` form as Python file anchors.

  Every renamed node carries the legacy ID under `props.aliases` for one
  minor version (retired in 0.17.0). The `_ensure_*` helpers across the ROS2
  cluster now route through the unified `weld._graph_node_registry.ensure_node`
  primitive so that two strategies materializing the same node merge their
  provenance instead of dropping the second claim.

  `.weld/discover.yaml` registers `strategy_pairs` entries for the ROS2,
  gRPC, and tree-sitter clusters. The strategy-pair-consistency rule is a
  structural no-op until a downstream workspace configures these strategies
  in `sources`; it then catches file-set drift with
  `pair_asymmetry_allowlist` entries documenting any genuine asymmetry.

  **User-visible impact:**
  - MCP transcripts and prior `wd query` JSON referencing `ros_package:<name>`
    still resolve via the alias index (alias-aware lookup landed in PR 2).
  - Sidecar caches invalidate by graph hash on first discovery after upgrade;
    no stale-cache hits possible.

  **Deprecation:** aliases retire in 0.17.0. Pin to 0.16.x if external
  scripts assume the old prefix and cannot be updated before the next minor.

- **gRPC and tree-sitter cluster IDs renamed.** gRPC
  rpc, contract, and enum IDs (`rpc:grpc:<package>.<service>.<method>`
  and friends) and the tree-sitter language family
  (`tree_sitter`, `typescript_exports`, `_csharp_tree_sitter`,
  `_java_tree_sitter`) now mint IDs through the canonical
  `weld._node_ids` contract. Mixed-case package and service names
  lower-case via `canonical_slug` (e.g. `package:csharp:Microsoft.AspNetCore.Mvc`
  -> `package:csharp:microsoft.aspnetcore.mvc`); tree-sitter file
  anchors move from `file:<stem>` to `file:<rel_posix_path_without_ext>`
  to match the form Python file anchors picked up in PR 1.

  The `runtime_contract` strategy's bespoke local `_slug` helper was
  deleted in favour of the shared `canonical_slug` so the URL-derived
  rpc IDs use the same rule as the rest of the graph. The `test_peer`
  strategy now mints `file:weld/tests/<stem>` (full path) instead of
  the legacy `file:tests/<stem>`, with the trailing `_test` suffix
  preserved on the stem so test/production semantic distinction is
  retained.

  Every renamed node carries the legacy ID under `props.aliases` for
  one minor version (retired in 0.17.0). MCP transcripts and prior
  `wd query` JSON referencing the legacy IDs continue to resolve via
  the alias index. Sidecar caches invalidate by graph hash on first
  discovery after upgrade.

  **Deprecation:** aliases retire in 0.17.0. Pin to 0.16.x if external
  scripts assume the old prefix and cannot be updated before the next
  minor.

## v0.15.0 - 2026-05-02

### Added

- `wd communities` reports projected graph community structure: the discovered
  graph is split into communities, unresolved-symbol sentinels are projected
  out, and each community surfaces its top hub nodes so users can navigate
  large graphs by topic instead of scanning a flat node list.
  <!-- verify: file=weld/graph_communities.py grep=build_graph_communities -->
- The retrieval surface (`wd query`, `wd find`, `wd context`, `wd path`,
  `wd callers`, `wd references`, `wd stale`, `wd stats`) now defaults to a
  human-readable text format. Pass `--json` for the machine-readable envelope
  used by tools and the MCP server.
  <!-- verify: file=weld/_cli_render.py grep=render_query -->
- `wd export` accepts the centre node id as a positional argument
  (`wd export <node>`); the legacy `--node <id>` flag is deprecated and prints
  a deprecation warning, but still works for one release.
  <!-- verify: file=weld/_export_cli.py grep=run_export -->
- `wd doctor` surfaces Agent Graph health as a first-class section, reporting
  agent count, broken references, and discovery diagnostics so missing or
  malformed agent definitions are caught alongside graph and provider checks.
  <!-- verify: file=weld/_doctor_agent_graph.py grep=check_agent_graph -->
- `wd lint` is signal-first: violations are grouped by rule with stable
  ordering, and orphan-detection now suppresses test files and obviously
  intentional standalone modules by default. Use the existing rule-disable
  flags to opt back in to noisier output.
  <!-- verify: file=weld/arch_lint_orphan.py grep=detect_orphans -->
- `wd discover` prints a one-line success summary to stderr (graph path,
  node and edge counts, elapsed time); pass `--quiet` to suppress it. Stdout
  still carries the canonical graph payload.
  <!-- verify: file=weld/_discover_summary.py grep=emit_summary -->
- `wd agents discover` text mode surfaces diagnostics (broken references,
  unresolved invocations, missing files) inline with the agent listing, so
  agent-graph problems are visible without dropping to `--json`.
  <!-- verify: file=weld/agent_graph_cli.py grep=_run_discover -->
- `wd stats` and `wd prime` reframe description coverage around *meaningful*
  nodes only (functions, classes, modules with non-trivial bodies), so the
  headline coverage metric reflects nodes a human would actually want to
  describe instead of being diluted by trivial graph artefacts.
  <!-- verify: file=weld/_prime_coverage.py grep=describe_meaningful_coverage -->

### Fixed

- `wd discover` re-runs strategies whose declared outputs are missing from
  the on-disk graph. Previously a partial discovery state could leave a
  strategy permanently skipped on subsequent runs; the discovery state now
  diffs declared-vs-present outputs and forces re-run when they disagree.
  <!-- verify: file=weld/discovery_state.py grep=DiscoveryState -->
- `wd communities` projects unresolved-symbol sentinels out of the community
  graph and reports top-level hubs per community. Earlier output put noisy
  unresolved nodes at the top of every community summary; the projection step
  removes them while preserving the underlying edges for hub ranking.
  <!-- verify: file=weld/graph_communities.py grep=_hub_nodes -->
- `wd find <basename>` now hits exact basenames such as `install.sh`,
  `BUILD.bazel`, or `pyproject.toml`. The file index previously only emitted
  tokenised path fragments, so bare basenames missed.
  <!-- verify: file=weld/file_index.py grep=_tokenize_path -->
- `wd enrich` lists the available providers and explains the agent-direct
  enrichment path when no provider is configured, instead of failing with a
  bare "missing provider" error.
  <!-- verify: file=weld/_enrich_safe.py grep=_format_no_provider_error -->
- `wd query` demotes unresolved-symbol sentinels (`symbol:unresolved:<name>`)
  in the ranker so resolved symbols outrank sentinels regardless of BM25
  delta; sentinels now only surface when nothing else matches.
  <!-- verify: file=weld/ranking.py grep=resolution_penalty -->
- `wd callers <bare-name>` resolves bare names the same way `wd references`
  does. Previously `wd callers DiscoveryState` errored "node not found"
  while `wd references DiscoveryState` worked; the resolver is now shared
  between both commands.
  <!-- verify: file=weld/graph.py grep=_resolve_symbol_name -->
- `wd query` falls back to a per-group OR union when a multi-token
  strict-AND query yields zero matches. Results are tagged with
  `degraded_match=or_fallback` so consumers know the result was relaxed;
  single-token queries skip the fallback.
  <!-- verify: file=weld/graph_query.py grep=query_or_fallback -->

## v0.14.0 - 2026-05-02

### Added

- `wd discover` now closes the graph deterministically across supported
  languages. Source-backed symbols link to their files, imports / includes /
  use edges resolve into deterministic dependencies, call edges carry
  provenance, and unresolved sentinels are reduced in `wd stats` and `wd viz`.
  <!-- verify: file=weld/graph_closure.py grep=close_graph -->
- New `test_peer` discovery strategy surfaces sibling `*_test.py` files for
  every Python module so `wd query` and `wd find` can locate a module's
  nearest unit test directly from the graph.
  <!-- verify: file=weld/strategies/test_peer.py grep=test_peer -->
- `wd find` and `wd query` now surface module-level Python constants
  (top-level UPPER_CASE assignments), so configuration values and defaults
  appear alongside functions and classes in search results.
  <!-- verify: file=weld/strategies/python_module.py grep=_module_constant_names -->
- `wd doctor` probes the standalone `copilot` CLI used by the `copilot-cli`
  enrichment provider, introduces a `[note]` level for soft recommendations
  (so missing optional providers and missing MCP config are no longer
  presented as `[warn]`), and adds `--ack <id>` / `--unack <id>` /
  `--list-acks` to persist per-project dismissals in `.weld/doctor.yaml`.
  <!-- verify: file=weld/_doctor_optional.py grep=copilot -->
- `wd agents discover` now infers references through `subagent_type=`,
  `Skill()` calls, and bare `/command` mentions inside agent and command
  bodies, surfaces `weld.invokes_agents` frontmatter for orchestrator
  agents, scans frontmatter descriptions for inferred references, applies
  an implicit-default `applies_to_path` to instruction files, parses
  `.codex/config.toml` as a Codex MCP source, and explodes
  `.claude/settings.json` permissions into per-entry edges. The bare
  `/command` terminator class extends to `!`, `?`, `]`, and `}`, and the
  `wd agents demo` fixture now mirrors a realistic seven-platform,
  nine-asset deployment.
  <!-- verify: file=weld/agent_graph_discovery.py -->

### Fixed

- `wd discover` keeps `.weld/file-index.json` in sync with `.weld/graph.json`.
  Stale index files were drifting after partial runs and causing `wd find` to
  miss recently-discovered files.
  <!-- verify: file=weld/discover.py grep=_persist_file_index -->

## v0.13.2 - 2026-04-30

### Added

- Workspace child scans can now opt into Git ignore rules with
  `scan.respect_gitignore: true`, `wd init --respect-gitignore`, or
  `wd workspace bootstrap --respect-gitignore`. The default remains
  compatibility-safe: gitignored child repos are still discovered unless the
  workspace opts in.
  <!-- verify: file=weld/_workspace_bootstrap_cli.py grep=--respect-gitignore -->
- `scan.exclude_paths` now accepts workspace-relative glob patterns with `*`
  and `**` in addition to bare directory names and exact paths, so workspace
  bootstraps can skip folders or extension-shaped generated directories.
  <!-- verify: file=weld/workspace_scan_filter.py grep=matches_exclude -->

## v0.13.1 - 2026-04-29

### Added

- `wd discover` now models a startup flow and trace import contract, with C# and C++ tree-sitter strategies that surface native and managed startup entrypoints alongside the existing Python entrypoint detection.
  <!-- verify: file=weld/trace_contract.py grep=TRACE_EDGE_TYPES -->
- `wd workspace bootstrap --exclude-path PATH` (repeatable) lets you pass scan exclusions on the command line; the values are persisted into the rewritten workspaces yaml so subsequent runs respect them.
  <!-- verify: file=weld/_workspace_bootstrap_cli.py grep=--exclude-path -->

### Fixed

- `wd workspace bootstrap` rescans now honor the workspace's configured `scan.exclude_paths` instead of walking ignored paths. Previously a workspace root containing operational nested repositories under an excluded prefix (e.g. a quarantine directory) could derive an invalid child name and abort bootstrap with `WorkspaceConfigError: invalid character in name`. Scan-only entries whose auto-derived child name fails validation are now filtered and reported instead of failing the whole run.
  <!-- verify: file=weld/_workspace_bootstrap_cli.py grep=exclude_paths -->

## v0.13.0 - 2026-04-28

### Added

- `wd agents viz` opens a local read-only browser explorer for `.weld/agent-graph.json`, reusing the existing graph visualizer while keeping `wd viz` focused on `.weld/graph.json`.

## v0.12.0 - 2026-04-28

### Added

- Local-only telemetry recording success/failure of CLI invocations and MCP tool calls. Default-on; opt out with `WELD_TELEMETRY=off`, `--no-telemetry`, or `wd telemetry disable`. Run `wd telemetry --help` for details.
- New `copilot-cli` enrichment provider for `wd enrich`. Uses the standalone `copilot` binary, so no API key is required (auth lives in the binary itself). Set `WELD_COPILOT_BINARY` to override the binary path.

### Fixed

- `wd init --output <dir>` now writes the polyrepo workspaces file alongside the discover config in the directory named by `--output`. Previously it was dropped at the working-directory default, which leaked into the source-of-truth `.weld/` and silently flipped subsequent `wd discover` runs into federation mode.

## v0.11.6 - 2026-04-28

### Changed

- `wd discover` examples in the README quickstart and the PyPI README now default to `--safe` mode. The trust-model section explains when it is appropriate to drop the flag. Both READMEs are aligned so the GitHub and PyPI evaluators see the same first-run command.

### Added

- New runtime-pending markers in `docs/runtime-validation.md` for the three `Partial` matrix rows awaiting live-client validation (Codex, Claude Code, VS Code/Copilot). The markers make it explicit that those rows have not yet been validated against a real client and have not been promoted to `Supported`.

### Fixed

- README markdown is no longer compressed into single-line paragraphs in raw form. Long prose in the description, "Try it in 5 minutes" call-out, and demo-script blurb is reflowed to <=200 characters per line for readability when reading the README on GitHub or via `cat`/`less`.

## v0.11.5 - 2026-04-27

### Fixed

- `wd init` inside a linked git worktree of a bootstrapped polyrepo now mirrors the main checkout's `.weld/workspaces.yaml` instead of silently degrading to a single-service graph. Linked worktrees do not contain copies of nested-git child repos (git does not clone them), so the FS scan returns empty and the worktree had no way to participate in federation -- `wd discover` produced a tiny local graph (~73 nodes for the reporter) instead of the federated one. The federation **discover** path already handles linked worktrees via `resolve_child_root`; `wd init` now uses the same `git_main_checkout_path` helper to inherit the registry. After this fix, `wd init` in a worktree produces `workspaces.yaml`, `workspace-state.json`, and a federated `wd discover` graph with no manual yaml restore needed. Operator-authored worktree-local yaml is preserved (`force=False` is honoured).

## v0.11.4 - 2026-04-27

### Fixed

- `wd workspace bootstrap` no longer misses nested-git children when the children dir matches a root `.gitignore` pattern. The FS scanner previously folded root gitignore into its exclusion set; polyrepos whose operator added `services/` (or any common children-dir name) to root `.gitignore` were silently masked, sending bootstrap to single-service mode and leaving `wd workspace status` permanently broken until manual recovery. A nested `.git` directory is now treated as a workspace child by definition -- gitignore tracks VCS state, not workspace topology. Callers that need project-specific exclusions must now pass them explicitly via `exclude_paths`.
- `wd init --force` at a polyrepo root now materialises `workspace-state.json` and runs the federated graph build (delegates to `bootstrap_workspace` after the per-child init step). Previously `wd init` only wrote yaml + per-child `discover.yaml`, leaving `wd workspace status` to fail until the operator separately ran `wd workspace bootstrap`.

## v0.11.3 - 2026-04-27

### Fixed

- `wd workspace bootstrap` no longer misroutes a nested-git polyrepo to single-service mode after a `.weld/` reset. Two federation predicates disagreed: `wd discover` decides federation by config presence, while bootstrap used a filesystem-only scan that honoured root `.gitignore`, `DEFAULT_MAX_DEPTH=4`, and `_BUILTIN_EXCLUDE_DIRS`. After `rm -rf .weld/` the FS scan could return zero even when the operator had restored a valid `.weld/workspaces.yaml`, leaving the workspace stuck without `.weld/workspace-state.json` and breaking `wd workspace status`. Bootstrap now uses a unified merge predicate where `workspaces.yaml` is authoritative when present and the FS scan augments it; corrupt yaml falls back to scan with the parse failure surfaced in `BootstrapResult.errors`. `wd init` at a polyrepo root now also runs per-child init so every child gets its own `.weld/discover.yaml`.

## v0.11.0 - 2026-04-27

### Added

- `wd bootstrap` adopts a managed-region marker model. Each bundled template under `weld/templates/` declares one or more `<!-- weld-managed:start name=... -->` regions; `wd bootstrap <fw> --diff` and the writer's no-op / refuse / clobber / append paths operate **inside** those markers only. Operator-curated content outside the markers is left untouched after the first write, so a single edited line outside a managed region no longer reads as a full-file replacement in `--diff`.
- `wd bootstrap` ships `--include-unmanaged`: paired with `--diff`, it falls back to the whole-file unified diff for operators who want to fully resync past the managed-region scope. The flag is rejected with a clear error when used outside `--diff`.
- `wd brief` falls back to an OR-of-tokens retrieval when its strict AND query returns zero matches on a multi-token query. The fallback result carries `degraded_match: "or_fallback"` so callers know they did not get the strict-AND ranking. `graph.query()`'s AND semantics are unchanged.
- Live-client runtime validation now has a real Codex AGENTS.md + skill record and clearly-marked `result: pending` stubs for Claude Code MCP, Claude Code skill/subagent, and VS Code Copilot custom instructions. A new launch-copy guard rejects platform claims in launch material that are not backed by a recorded row.

### Changed

- The pre-marker-layout migration: `wd bootstrap` prints an actionable message and exits non-zero on files that contain no `weld-managed:start` line; `--force` re-seeds the file with the bundled template verbatim (markers and all). No silent corruption, no heuristic anchor matching.
- The `_FEDERATION_PARAGRAPH` block appended in federation mode is itself a managed region named `federation`, so federated workspaces get the same drift-detection treatment as the rest of the bootstrap surface.
- README's comparison-table row for Sourcegraph drops the misleading "you commit with your code" line; the row now describes the actual config-only default and the `wd init --track-graphs` opt-in.
- The Copilot bundled skill template (`wd bootstrap copilot`) installs `weld` via `uv tool install configflux-weld` instead of the contributor `pip install -e ./weld` path.
- Bootstrap design and migration semantics finalized for the managed-region template model.

### Fixed

- `wd discover` in federated workspaces now stamps `meta.git_sha` on the root meta-graph. Single-repo discover already did so; the federated path skipped it, which made `wd prime --agent all` always print "graph.json has no git SHA — may be stale" immediately after a successful discover.

## v0.10.0 - 2026-04-26

### Added

- `wd init --track-graphs` is now actually shipped in the wheel. The opt-in keeps generated graphs (`graph.json`, `query_state.bin`, etc.) tracked in git so warm-CI / warm-MCP setups continue to work; without the flag the managed `.weld/.gitignore` follows the config-only default.
- Public install/contributor docs split into separate audiences in `README.md` and `CONTRIBUTING.md` so downstream consumers do not have to skim past contributor-only setup.

### Changed

- Public-facing runtime-validation copy tightened, and a dedicated `runtime_claims_lint` checks that documented runtime claims match the code.

## v0.9.0 - 2026-04-26

### Added

- `wd agents audit --strict` surfaces previously-suppressed canonical/rendered group pairs (they no longer hide audit findings when strict mode is set).
- `WELD_INIT_FRAMEWORK_CAP` env override lets forensic re-runs of `wd init` raise or remove the per-language framework sample cap; `0` disables the cap, custom positive integers set a custom cap, unset/empty/negative/non-numeric values fall back to the built-in default silently.
- Query state sidecar: `wd query` now persists the inverted index and BM25 corpus to `.weld/query_state.bin` after `wd discover`, so cold-path query startup drops from ~1.28 s to ~0.54 s on a representative 100k-node graph (about 58% faster). The sidecar is content-addressed via blake2b digest + node count + weld schema version + format-version envelope; on freshness mismatch or corruption the sidecar is silently rebuilt.
- `wd demo polyrepo --init` auto-bootstraps the workspace before discovery so the first run produces a populated graph instead of an empty one.
- Bootstrap traceback surfaced under `WELD_DEBUG=1` in `wd demo polyrepo` so the demo's bootstrap exception handler shows the underlying cause when set.

### Changed

- Edge-type weighted impact and plan-change ranking: `_score_asset()` and `_secondary_assets` consult an edge-weight table (semantic=5.0, related=2.0, incidental=0.5) and a `SECONDARY_THRESHOLD=1.0`. Canonical-authority assets bypass the secondary threshold so authoritative nodes always render even when only attached via low-weight edges.
- `wd init` framework detection merged into a single classifier pass (`_init_classify.py`); per-file `detect_*` walks coalesce, dropping a representative `wd init` cold run from 41.2 s to 8.6 s on a 100k synthetic tree (about 79% faster). No behavior change vs. multi-pass detection — same constants, same heuristics.
- `wd discover` now warns on stderr when the prior `graph.json` is unreadable instead of silently rewriting it. The previous graph is preserved untouched if the load fails; operators see the failure and can decide whether to rerun.
- `agent_graph_render_pairs` only honors `render_paths` from `authority="canonical"` nodes. Non-canonical nodes can no longer suppress duplicate-name audit findings via render-paths.

### Fixed

- Go gin framework detection now matches the canonical `github.com/gin-gonic/gin` import path; the quoted-path matcher pre-filters block comments and raw-string literals so commented-out imports and string-fixture content no longer trigger false positives.
- `unused_skill` audit suppression tightened to a word-boundary regex match and respects skill name mentions in agent body / instruction text, eliminating substring false positives.
- Bench `test_discover_stability` no longer flakes against tiny-time clocks (sub-millisecond mtime resolution).

## v0.8.3 - 2026-04-25

### Fixed

- CHANGELOG entry for v0.8.2 listed `11 graph-backed tools` and named the
  nonexistent `weld_callees`. The MCP registry has had 13 tools since v0.8.2
  (matching `docs/mcp.md` and the in-process registry); the entry is
  corrected here so changelog readers and PyPI long-description match the
  shipped surface.

## v0.8.2 - 2026-04-25

### Added

- `wd security` (and `wd doctor --security` mode) shows trust posture as a
  scannable view: project-local strategies under `.weld/strategies`,
  `external_json` adapters in `.weld/discover.yaml`, enrichment provider
  network use, MCP importability, and safe-mode availability. Risk level
  rolls up to `low`/`medium`/`high` with recommendations; `--json` output
  is available for tooling.
- `wd agents render` (preview) writes Agent Graph artifacts with a safe
  contract: dry-run/diff by default, `--write` required to write,
  `--force` required to clobber, provenance headers on rendered files,
  and a drift audit.
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
  project-local code.
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
- GitHub: issue templates and contact routing for incoming community
  reports; `docs/community.md` documents how feedback is organized.

### Changed

- `wd-retry-hint` formatting is centralized so retry guidance is
  consistent across CLI commands.

### Fixed

- `wd discover` honors brace globs (e.g. `**/*.{ts,tsx}`) in the
  `typescript_exports` strategy.
- `wd doctor` no longer fails when run inside an empty directory.

## v0.7.0 - 2026-04-23

### Fixed

- `wd discover` no longer overwrites `.weld/graph-previous.json` before
  parsing the current graph. A corrupt `graph.json` now leaves the last
  good recovery snapshot intact so `wd diff` and manual recovery keep
  working.

### Changed

- `exclude:` patterns in `.weld/discover.yaml` now match against the
  full repo-relative path, not just the filename. Segmented patterns
  like `.cache/**`, `compiler/**`, and `**/*.gen.py` work as expected;
  bare filename patterns (`README.md`, `*.pyc`) continue to match via
  a basename fallback. Source-level `exclude` is applied uniformly in
  `resolve_source_files`, so strategies no longer need to opt in for
  excludes to take effect.

## v0.6.0 - 2026-04-22

### Added

- Added atomic `wd discover --output PATH` writes so discovery can preserve
  incremental context and avoid truncating the existing graph before it is
  read.
- Added `wd bootstrap --diff` and `wd bootstrap --force` so existing agent
  bootstrap files can be compared with, or upgraded to, the bundled templates.
- Added `wd prime --agent {auto,claude,codex,copilot,all}` to surface missing
  bootstrap files for the active agent framework.

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
