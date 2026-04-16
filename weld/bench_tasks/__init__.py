"""Comparative agent benchmarking for weld.

This package extends the on-demand wd bench harness with a
**comparative** mode that evaluates how well each retrieval approach
(grep baseline vs. weld CLI) serves a realistic agent task.

Each ``AgentTask`` in a YAML fixture pairs a natural-language prompt with
an *answer key* -- the repository-relative paths an unaided operator would
consider correct hits. For every task the comparative runner measures:

  - **token cost**     -- how many tokens the mode lands in the agent window
  - **accuracy**       -- precision, recall, and F1 vs. the answer key
  - **latency**        -- wall-clock ms per retrieval call

The public CLI surfaces this harness through two subcommands:

  ``wd bench --compare [--task ID] [--tasks YAML] [--out MD]``
      run the full comparative pass and write a markdown report plus a
      machine-readable JSON artifact.

  ``wd bench --report --artifact PATH [--out MD]``
      re-render the markdown report from a prior artifact (no retrieval).

Like its siblings in :mod:`weld.bench`, this harness is on-demand and is
not a CI gate.
"""

from weld.bench_tasks.compare import (
    CompareMetrics,
    CompareResult,
    run_compare,
)
from weld.bench_tasks.report import render_compare_report
from weld.bench_tasks.tasks import AgentTask, load_tasks

__all__ = [
    "AgentTask",
    "CompareMetrics",
    "CompareResult",
    "load_tasks",
    "render_compare_report",
    "run_compare",
]
