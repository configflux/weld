# Weld Onboarding Guide

This guide describes how to adopt `weld` in a new repository when the goal is
to help a human or LLM agent work with the whole codebase, not just parse
source files.

## Onboarding goal

The outcome of onboarding is a repository where `weld` can answer:

- where implementation lives
- which docs or policies are authoritative
- what build/test/ops surfaces constrain a change
- how repository-specific or legacy analysis should plug into the graph

## 1. Start with artifact classes, not languages

Before you tune extraction, classify the repository into artifact classes:

- implementation
- documentation
- policy
- verification
- operations
- build
- entrypoints

`weld` should model all of them when they exist. A good `discover.yaml` is
organized around the repository's real surfaces, not just around file
extensions.

## 2. Bootstrap the starter config

If `weld` is installed in your environment, use `weld ...` directly. If
you are working from a raw source checkout without installing it first, use
`python -m weld ...` as the compatibility path.

Generate a starting point:

```bash
wd init
```

The starter now follows the Git-visible repo boundary by default: tracked files
and untracked non-ignored files are candidates, while ignored untracked paths
stay out unless you explicitly add them later.

Then treat the generated `.weld/discover.yaml` as a draft, not a finished
configuration.

Expected follow-up edits include:

- narrowing or widening globs
- splitting sources by artifact role
- adding missing docs/infra/build/test/policy surfaces
- selecting richer strategies where they are available
- adding static topology overlays for boundaries and system structure

## 3. Prefer the lightest viable extraction path

Use this order of preference:

1. bundled strategy
2. optional richer bundled strategy such as tree-sitter
3. project-local strategy under `.weld/strategies/`
4. external adapter command that emits normalized weld JSON

Do not create a bundled strategy for every repo-specific problem. If a
repository already has a good local analyzer, use an adapter instead.

Use `wd scaffold local-strategy <name>` and
`wd scaffold external-adapter <name>` to bootstrap bundled templates for
project-local strategies and external adapter commands. See the
[Strategy Cookbook](strategy-cookbook.md) for step-by-step instructions.

## 4. Model non-code artifacts intentionally

Do not stop after implementation extraction.

At minimum, check whether the repo has:

- ADRs or architecture docs
- runbooks or operational guides
- CI/workflow files
- build manifests or target declarations
- policy docs such as `AGENTS.md`, security guidance, or release rules
- test suites or verification manifests

These surfaces often provide more reliable agent guidance than code alone.

## 5. Add static topology where extraction is not enough

Extraction alone usually does not capture:

- system boundaries
- public vs internal surfaces
- ownership seams
- important entrypoints
- cross-package relationships that are architectural rather than lexical

Use static topology in `discover.yaml` to make those relationships explicit.
When in doubt, prefer an honest manual edge over a clever but opaque heuristic.

## 6. Validate the graph like a product surface

After tuning discovery, regenerate artifacts and inspect them directly:

```bash
wd discover > .weld/graph.json
wd build-index
wd query "<term>"
wd find "<term>"
wd stale
```

Validate all of the following:

- canonical files are present
- docs/infra/build/test/policy surfaces are present where expected
- nested repo copies and generated junk are excluded
- node and edge relationships are understandable to someone new to the repo

## 7. Plan for agent-facing semantics

The direction for `weld` is not only broader extraction. Over time, onboarded
projects should populate normalized metadata such as:

- `authority`
- `confidence`
- `roles`
- `source_strategy`
- `file`
- `span`

If you already know which artifacts are authoritative or supporting, encode
that early through topology or custom strategies instead of waiting for a
future perfect extractor.

## 8. Use repo-local adapters for legacy or specialized systems

If the best source of truth is already outside `weld`, do not fight it.

Good adapter candidates include:

- clang or compilation-database analysis for C/C++
- existing schema/build graph tools
- custom manifest processors
- legacy repository analyzers that understand proprietary layout conventions

The direction for `weld` is to support these through a normalized external JSON
adapter surface rather than trying to bundle every specialized parser.

## 9. Keep the onboarding story honest

Do not claim capabilities that are only aspirational.

A useful onboarding is one where:

- the current extraction quality is clear
- missing semantics are visible
- repo-specific assumptions are documented
- future roadmap items are linked instead of implied

If a project needs manual overlays, repo-local strategies, or external
adapters, that is a normal part of the toolkit model, not a failure.
