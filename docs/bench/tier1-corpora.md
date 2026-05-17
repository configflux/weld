# Tier-1 corpora -- pinned commit SHAs

This document is the canonical record of the corpora that the Tier-1
language-support harness (`tools/tier_check.py`) runs against, per
[ADR 0064](../adrs/0064-tier-1-language-support-contract.md). Each
language declares three corpora -- a desktop monorepo, a framework
monorepo, and a polyrepo workspace -- pinned to specific commit SHAs.

The machine-readable manifest is
[`tier1-corpora.yaml`](tier1-corpora.yaml). This Markdown view is the
human-friendly mirror. The two files must agree; the manifest is
authoritative and the gate enforces parsing.

## Bump discipline

A corpus pin bump is itself a tracked event. Open a tracking issue
under the C# (or per-language) Tier-1 epic before changing any SHA
below, and link the issue in the commit message. The harness CI
compares run outputs against the recorded pins, so a silent bump
degrades all downstream regression evidence to noise.

If you must record a "best-known" pin in the absence of network
(corpus unreachable from the dev host), write `TBD` with a short
parenthetical and file a follow-up issue to backfill the SHA.

## Materialising the corpora

`tools/tier1_corpus.py` is the helper that reads this manifest and
clones each pinned repo at its SHA into a host directory:

```bash
# List the pin set for a language.
python3 tools/tier1_corpus.py --language csharp --list --json

# Materialise checkouts under /tmp/weld-tier1-corpora (idempotent).
python3 tools/tier1_corpus.py --language csharp --fetch \
    --output /tmp/weld-tier1-corpora

# Verify an existing checkout matches the pinned SHA without fetching.
python3 tools/tier1_corpus.py --language csharp --verify \
    --output /tmp/weld-tier1-corpora
```

The polyrepo workspace template lives at
`tests/fixtures/tier1/csharp-polyrepo/.weld/workspaces.yaml` and is
copied (or symlinked) into the fetched workspace root by the tool.

## C\#

C# is the first language attempting Tier-1 promotion (ADR 0064
§ Implementation order). All three corpora are pinned.

### Desktop monorepo -- ShareX

| Field             | Value                                                          |
|-------------------|----------------------------------------------------------------|
| Repository        | https://github.com/ShareX/ShareX                              |
| Pinned SHA        | `42465d3e9bee52c780c0a905c639b9fb0a958a00`                    |
| C# files          | ~2200                                                          |
| LOC               | _unmeasured at pin time_                                       |

ShareX is a mature Windows desktop screenshot and capture tool with a
multi-project solution (Windows Forms + WPF + several class-library
projects). It exercises classes, structs, records, enums, properties,
async/await, P/Invoke, and the inheritance hierarchies that
criterion 1 (kind correctness) and criterion 2 (class-level edge
accuracy) target. The 2026-05-15 dogfood pass that motivated ADR 0064
ran against this same checkout.

### Framework monorepo -- dotnet-architecture/eShopOnWeb

| Field             | Value                                                          |
|-------------------|----------------------------------------------------------------|
| Repository        | https://github.com/dotnet-architecture/eShopOnWeb             |
| Pinned SHA        | `4da8212117e87d808d4bbc7da6286fd2147ce606`                    |
| C# files          | _unmeasured at pin time_                                       |
| LOC               | _unmeasured at pin time_                                       |

The Microsoft .NET team's reference ASP.NET Core web application.
Exercises every framework strategy declared by ADR 0056:

- `csharp_aspnet_routes` -- MVC controllers, Razor pages, minimal-API
  endpoints.
- `csharp_efcore` -- `DbContext` + `DbSet<>` definitions and
  configuration in `Infrastructure/Data/`.
- `csharp_test_framework` -- xUnit, FunctionalTests, integration tests.
- `csharp_msbuild_targets` -- `Directory.Build.props`,
  `Directory.Build.targets`, per-project `.csproj` solution structure.

### Polyrepo workspace -- ShareX + eShopOnWeb + Newtonsoft.Json

The synthetic workspace combines the desktop and framework corpora with
a small public C# library (Newtonsoft.Json) so the harness can exercise
cross-repo `package:csharp:*` resolution end-to-end. The workspace
template (skeleton `.weld/workspaces.yaml`, declaring children and
`cross_repo_strategies: [package_import_resolver]`) lives at
[`tests/fixtures/tier1/csharp-polyrepo/`](../../tests/fixtures/tier1/csharp-polyrepo/).

| Child              | Repository                                                     | Pinned SHA                                  |
|--------------------|----------------------------------------------------------------|---------------------------------------------|
| ShareX             | https://github.com/ShareX/ShareX                              | `42465d3e9bee52c780c0a905c639b9fb0a958a00` |
| eShopOnWeb         | https://github.com/dotnet-architecture/eShopOnWeb             | `4da8212117e87d808d4bbc7da6286fd2147ce606` |
| Newtonsoft.Json    | https://github.com/JamesNK/Newtonsoft.Json                    | `4f73e74372445108d2c1bda37b36e6f5e43402e0` |

The actual checkouts live outside the repository tree (e.g. under
`/tmp/weld-tier1-corpora/`) and are not vendored. The fetcher writes
them into the layout that the workspace template expects.

## Python

_TBD when Python enters T1 promotion._ Python is targeted under
ADR 0064's Implementation order step 5 (after C# greens). The pin set
will follow the same desktop/framework/polyrepo shape.

## C++

_TBD when C++ enters T1 promotion._ C++ currently sits at Preview per
ADR 0057 and is not yet eligible.

## Java

_TBD when Java enters T1 promotion._ Sequenced after C# and Python per
ADR 0064 § Implementation order.

## Description-coverage targets (criterion 6, advisory)

Per the ADR 0064 amendment (2026-05-16), criterion 6 (description
coverage post-enrich) is **advisory** -- the harness measures and
reports coverage in the JSON envelope but does not use it to gate
Tier-1 promotion. The thresholds below remain pinned as reporting
targets for benchmark publication and for any future per-provider
quality scorecard, but a language can reach Tier 1 with criterion 6
below the threshold.

The C# definition is:

- **Meaningful kinds**: `class`, `interface`, `struct`, `enum`,
  `record`, `method`, `property`, `function`.
- **Advisory threshold**: 70% of meaningful symbols carry a non-empty
  `description` field after one `wd enrich --provider <name>` pass.

Other languages will declare their advisory thresholds here when they
enter T1 promotion.
