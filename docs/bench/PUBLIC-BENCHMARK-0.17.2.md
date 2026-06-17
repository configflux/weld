# Weld public benchmark (0.17.2)

Published methodology and corpus results per the public benchmark methodology.

## Methodology

This report is produced by ``wd bench --public``. The methodology is
defined as follows:

- Public corpus is SHA-pinned (see the Corpus manifest section).
- Each task has a ground-truth answer key (repo-relative files).
- Adapters under test: weld, grep, tree-sitter-cli, graphify. C++
  rows additionally exercise ``weld_libclang`` -- the libclang variant
  of the weld stack -- which runs only when the ``cpp-libclang`` extra
  is installed AND the repo's setup hook produced a
  ``compile_commands.json``; otherwise it is reported as ``unavailable``
  or ``SKIPPED: <reason>``.
- Metrics computed per task and per family:
  precision, recall, F1, tokens, and (where applicable) cost-per-task.
- An adapter whose external binary is missing is reported as
  ``unavailable`` and excluded from the per-family aggregate so its
  absence does not depress its own median scores.
- A repo whose corpus SHA is a placeholder (or whose clone failed) is
  reported as ``SKIPPED: <reason>`` per-task and excluded from
  per-family aggregates, again to keep numbers honest.
- Reproducibility: ``wd bench --public --verify`` re-runs the corpus
  and asserts byte-identical output to this report.
- Every metric where weld lost or was degraded is listed under
  Caveats (honest losing).


## Corpus manifest

Corpus id: `public-v0` (schema 1).

| repo | tasks | families | status |
|------|-------|----------|--------|
| flask | 5 | callgraph, cross_repo, dependency, impact, navigation | materialized |
| eshop | 1 | navigation | skipped - placeholder SHA (corpus entry not yet pinned) |
| nlohmann_json | 5 | callgraph, cross_repo, dependency, impact, navigation | materialized |
| polyrepo_fixture | 1 | cross_repo | skipped - placeholder SHA (corpus entry not yet pinned) |
| ros2_sample | 1 | navigation | skipped - placeholder SHA (corpus entry not yet pinned) |


## Per-task results

| id | family | repo | weld | grep | tree_sitter | graphify |
|---|---|---|---|---|---|---|
| flask-nav-01 | navigation | flask | F1=0.00 P=0.00 R=0.00 tokens=85 status=ok | F1=0.00 P=0.00 R=0.00 tokens=27059 status=ok | unavailable | unavailable |
| flask-dep-01 | dependency | flask | F1=0.00 P=0.00 R=0.00 tokens=20 status=ok | F1=0.08 P=0.04 R=1.00 tokens=36264 status=ok | unavailable | unavailable |
| flask-cg-01 | callgraph | flask | F1=0.22 P=0.12 R=1.00 tokens=314 status=ok | F1=0.00 P=0.00 R=0.00 tokens=32515 status=ok | unavailable | unavailable |
| flask-impact-01 | impact | flask | F1=0.00 P=0.00 R=0.00 tokens=20 status=ok | F1=0.00 P=0.00 R=0.00 tokens=2485 status=ok | unavailable | unavailable |
| flask-xrepo-01 | cross_repo | flask | F1=0.00 P=0.00 R=0.00 tokens=19 status=ok | F1=0.00 P=0.00 R=0.00 tokens=32001 status=ok | unavailable | unavailable |
| eshop-nav-01 | navigation | eshop | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) |
| njson-nav-01 | navigation | nlohmann_json | F1=0.00 P=0.00 R=0.00 tokens=87 status=ok | F1=0.00 P=0.00 R=0.00 tokens=27430 status=ok | unavailable | unavailable |
| njson-dep-01 | dependency | nlohmann_json | F1=0.00 P=0.00 R=0.00 tokens=19 status=ok | F1=0.00 P=0.00 R=0.00 tokens=37081 status=ok | unavailable | unavailable |
| njson-cg-01 | callgraph | nlohmann_json | F1=0.06 P=0.03 R=1.00 tokens=3154 status=ok | F1=0.00 P=0.00 R=0.00 tokens=20812 status=ok | unavailable | unavailable |
| njson-impact-01 | impact | nlohmann_json | F1=0.00 P=0.00 R=0.00 tokens=22 status=ok | F1=0.00 P=0.00 R=0.00 tokens=0 status=ok | unavailable | unavailable |
| njson-xrepo-01 | cross_repo | nlohmann_json | F1=0.00 P=0.00 R=0.00 tokens=20 status=ok | F1=0.00 P=0.00 R=0.00 tokens=20533 status=ok | unavailable | unavailable |
| poly-xrepo-01 | cross_repo | polyrepo_fixture | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) |
| ros2-nav-01 | navigation | ros2_sample | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) | SKIPPED: placeholder SHA (corpus entry not yet pinned) |

## Per-family aggregates

Median F1 (precision / recall) per family per adapter. Tasks where an adapter was unavailable are excluded from its family rollup.

| family | weld | grep | tree_sitter | graphify |
|--------|---|---|---|---|
| callgraph | F1=0.14 P=0.08 R=1.00 (n=2) | F1=0.00 P=0.00 R=0.00 (n=2) | unavailable | unavailable |
| cross_repo | F1=0.00 P=0.00 R=0.00 (n=2) | F1=0.00 P=0.00 R=0.00 (n=2) | unavailable | unavailable |
| dependency | F1=0.00 P=0.00 R=0.00 (n=2) | F1=0.04 P=0.02 R=0.50 (n=2) | unavailable | unavailable |
| impact | F1=0.00 P=0.00 R=0.00 (n=2) | F1=0.00 P=0.00 R=0.00 (n=2) | unavailable | unavailable |
| navigation | F1=0.00 P=0.00 R=0.00 (n=2) | F1=0.00 P=0.00 R=0.00 (n=2) | unavailable | unavailable |

## C++ variant comparison

- tree-sitter median F1 = 0.00 (n=5)
- libclang median F1 = 0.00 (n=5)

## Caveats

### dependency
- flask/flask-dep-01: weld F1=0.00, grep F1=0.08
