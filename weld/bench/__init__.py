"""Weld benchmark harness: token cost and first-context quality.

Two benchmark dimensions:

**Token cost** (runner module)
  Compares the token cost of three retrieval modes for a fixed set of agent
  prompts:
    1. ``grep`` baseline -- bytes an unaided agent would land in context.
    2. ``weld`` CLI -- the JSON stdout of ``wd query``/``wd brief``/
       ``wd callers`` invoked via in-process helpers.
    3. ``weld`` MCP -- the structured ``dict`` returned by
       :func:`weld.mcp_server.dispatch`, JSON-serialized.

**First-context quality** (quality module)
  Measures whether ``wd brief`` and ``wd trace`` return relevant,
  well-bucketed results: bucket hit rate, label recall, and token budget
  compliance.

Both harnesses are **on demand**, not CI gates.
"""

from weld.bench.quality import (
    QualityCase,
    QualityResult,
    evaluate_case,
    load_cases,
    render_quality_report,
    run_quality,
)
from weld.bench.runner import (
    BenchResult,
    Prompt,
    count_tokens,
    grep_baseline,
    weld_cli_baseline,
    weld_mcp_baseline,
    load_prompts,
    render_report,
    run_bench,
)

__all__ = [
    "BenchResult",
    "Prompt",
    "QualityCase",
    "QualityResult",
    "count_tokens",
    "evaluate_case",
    "grep_baseline",
    "weld_cli_baseline",
    "weld_mcp_baseline",
    "load_cases",
    "load_prompts",
    "render_quality_report",
    "render_report",
    "run_bench",
    "run_quality",
]
