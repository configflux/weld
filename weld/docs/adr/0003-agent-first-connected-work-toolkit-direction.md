# 0003. Agenting Connected Structure Toolkit Direction

Date: 2026-04-02
Status: Proposed

## Context

The current `weld` tool already does more than source-code AST extraction.
It can discover code, documentation, infrastructure, workflows, tools,
configuration, and repository-specific overlays through `discover.yaml`,
bundled strategies, and project-local strategy overrides.

That foundation is useful, but it still behaves primarily like an artifact
catalog:

- it is strong at answering "what exists?"
- it is weaker at answering "what should an agent read first?"
- it is weaker at identifying authoritative guidance, verification surfaces,
  and architectural boundaries
- it does not yet have a first-class agent briefing surface
- it can be adapted to unusual repositories today, but the onboarding model is
  not yet explicit enough for legacy, polyglot, or custom-build projects

The intended direction for `weld` is not "more AST support" in isolation. The
tool should become a portable, whole-codebase, agent-facing connected structure
toolkit that helps an LLM agent understand where implementation lives, which
documents or policies are authoritative, what boundaries constrain a change,
and what verification or operational surfaces are relevant.

This ADR sets that direction. It does not redesign storage, indexing, or
performance. Those concerns remain explicitly deferred.

## Forces

1. **Agent usefulness over parser novelty** -- the primary question is not
   whether `weld` can parse one more language, but whether it can guide an agent
   through a real repository safely and efficiently.
2. **Whole-codebase coverage** -- `weld` must continue to model code, docs,
   infrastructure, build surfaces, policy, tests, and operations rather than
   collapsing into a code-only AST extractor.
3. **Portable toolkit posture** -- projects must be able to adopt `weld` from a
   plain checkout with minimal assumptions about packaging or environment.
4. **Build-system agnosticism** -- the tool cannot depend on Bazel, CMake,
   Make, or any other build system being present or canonical.
5. **Extensibility for legacy and polyglot repos** -- maintainers should be
   able to onboard unusual repositories without forking `weld`.
6. **Explicit semantics** -- agents need normalized metadata such as authority,
   confidence, role, provenance, and boundary hints. Implicit heuristics alone
   are not enough.
7. **Graceful degradation** -- richer extraction should remain optional.
   Projects with only Python stdlib and repo-local scripts must still get a
   useful graph.

## Alternatives Considered

**A. Stay artifact-first and only add more extractors.** This improves breadth
but does not solve the agent-guidance problem. Rejected because parser coverage
alone does not make the tool meaningfully better for agent workflows.

**B. Optimize for standalone packaging first.** This could improve external
distribution but would force packaging and installation decisions ahead of the
core graph model and onboarding story. Rejected for now; the toolkit must stay
lightweight and repo-friendly first.

**C. Treat `weld` as a portable, whole-codebase, agenting toolkit with
agent-semantics-first roadmap.** This keeps the existing portable/plugin-based
foundation, expands graph meaning beyond artifacts, and adds onboarding and
retrieval surfaces that help agents work safely. Accepted.

## Decision

`weld` will evolve as a **portable agent-first connected-work toolkit** with the
following defaults:

- `weld` remains a portable toolkit first, not a packaging-heavy standalone
  product.
- `discover.yaml` remains the control plane for project onboarding.
- Whole-codebase coverage is a core requirement. `weld` must continue to model
  code, docs, infra, build, policy, tests, and operations.
- The roadmap prioritizes **agent semantics over raw parser breadth**.
- Indexing, alternate storage backends, and query-performance redesign are
  deferred to a later ADR.

### Architecture layers

`weld` is organized into four layers:

1. **Control plane**
   `discover.yaml`, static topology, and project-local overrides determine
   what should be scanned and how.
2. **Extraction plane**
   Bundled strategies, project-local strategies, optional tree-sitter, and
   repo-local external adapters extract normalized graph fragments.
3. **Graph contract**
   Nodes and edges carry standardized metadata that makes the graph usable by
   agents rather than only by humans.
4. **Agent retrieval plane**
   Query and briefing surfaces turn graph data into context packets that help
   an agent choose implementation, policy, verification, and operational
   context.

### Normalized metadata contract

The graph contract will standardize the following metadata fields whenever they
are derivable:

- `source_strategy`: the bundled, project-local, or external strategy that
  produced the node or edge
- `authority`: how authoritative the artifact is for the concept
- `confidence`: how reliable the relationship or classification is
- `roles`: what job the artifact plays for an agent
- `file`: canonical repository-relative path
- `span`: optional source location for a symbol or section

Normalized value vocabularies:

- `authority`: `authoritative`, `supporting`, `derived`
- `confidence`: `high`, `medium`, `low`
- `roles`: `implementation`, `documentation`, `policy`, `verification`,
  `operations`, `build`, `entrypoint`

These values are directional commitments for future implementation work. This
ADR does not claim that the current graph already emits them consistently.

### First-class node-type direction

`weld` will expand beyond the current artifact vocabulary to support these
first-class node types:

- `policy`
- `runbook`
- `build-target`
- `test-target`
- `boundary`
- `entrypoint`

Existing edge types should be reused where possible. New edge types should be
added only when current edge semantics plus normalized metadata cannot express
the relationship clearly.

### Agent-facing retrieval direction

`weld` will add a high-level agent retrieval surface:

`wd brief <term|node|path>`

`wd brief` should return a compact context packet for an agent that includes:

- likely implementation nodes
- authoritative docs and policies
- relevant verification and build surfaces
- important boundaries and entrypoints
- provenance, authority, and confidence metadata
- warnings or ambiguity notes when the graph is incomplete

The stable output contract should be JSON and optimized for LLM consumption,
not for humans reading a terminal transcript line by line.

### External adapter direction

`weld` will add a built-in adapter path:

`strategy: external_json`

This is the supported bridge for:

- clang/C++ AST pipelines
- legacy codebases with custom analysis scripts
- repositories with custom build systems
- environments where the best source of structure is an existing repo-local
  tool rather than a bundled strategy

The adapter contract will be:

- the strategy runs a repo-local command or script
- the command emits normalized JSON to stdout
- the JSON payload matches the strategy result shape used by bundled
  strategies: `nodes`, `edges`, and `discovered_from`
- adapter output must honor the same metadata vocabulary and graph-validation
  rules as bundled strategies

This keeps `weld` build-system agnostic while still allowing projects to reuse
specialized extractors such as clang-based tooling.

## Consequences

### What becomes easier

- Projects can onboard `weld` around the repository they actually have, not only
  the language set bundled with `weld`.
- Agents can ask higher-value questions about authority, boundaries,
  verification, and operational context.
- Documentation, policy, and operational artifacts become first-class
  graph inputs instead of secondary extras.
- The tool can support polyglot and legacy repositories without forcing every
  extractor into the bundled strategy set.

### What becomes harder

- The graph contract becomes more opinionated and must be validated.
- Bundled strategies need to emit more structured metadata over time.
- Onboarding docs and cookbook guidance become part of the product surface and
  must stay honest as the implementation evolves.
- Retrieval ranking must reflect authority and confidence, not only lexical
  matches.

## Deferred

The following are explicitly out of scope for this ADR:

- storage redesign
- indexing redesign
- alternate graph backends
- standalone packaging/distribution strategy
- multi-repository or remote-federated graph support
- LLM-generated enrichment that is not backed by explicit provenance

These may be addressed later, but they must not block the agent-semantics and
onboarding roadmap established here.

## What the team commits to

- Keep `weld` portable and repo-friendly first.
- Preserve whole-codebase discovery as a non-negotiable capability.
- Prioritize graph semantics and onboarding over parser-count vanity metrics.
- Add the future `wd brief` and `external_json` surfaces as first-class
  roadmap items.
- Keep future indexing/storage decisions in separate scoped work, not bundled
  into this direction-setting slice.

## Related Issues

- project-scw: direction ADR and roadmap for weld agenting-toolkit work
- project-ac5: completed query/source-hygiene foundation work
