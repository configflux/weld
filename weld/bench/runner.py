"""Token-cost benchmark runner for weld retrieval modes.

See :mod:`weld.bench` for an overview. The runner loads prompts, runs each
retrieval mode in-process, counts tokens, and writes a markdown report.
It does not spawn the real CLI or the real MCP stdio server -- it calls
the same helpers those entry points call, which keeps the benchmark
hermetic and fast and avoids any subprocess hangs.

Token counting prefers ``tiktoken`` (``cl100k_base``) when available and
falls back to ``ceil(bytes / 4)`` so the harness works in environments
without the optional dependency.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from weld._yaml import parse_yaml

# Re-export shared primitives so legacy callers keep their import sites.
from weld.bench.primitives import (  # noqa: F401
    Prompt,
    count_tokens,
    grep_baseline,
    tokenizer_name,
)


# -- Prompt loading ----------------------------------------------------------


def load_prompts(path: Path) -> list[Prompt]:
    """Load prompts from a YAML fixture (top-level ``prompts:`` list)."""
    data = parse_yaml(path.read_text(encoding="utf-8"))
    items = data.get("prompts", []) if isinstance(data, dict) else data
    out: list[Prompt] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            Prompt(
                id=str(item.get("id", "")),
                prompt=str(item.get("prompt", "")),
                category=str(item.get("category", "")),
                term=str(item.get("term", "")),
                symbol=str(item["symbol"]) if item.get("symbol") else None,
            )
        )
    return out

# The grep baseline and tokenizer now live in weld.bench.primitives so both
# this runner and the comparative agent-task bench can reuse them.


# -- weld CLI baseline (in-process; same code path as the CLI) ----------------

def _serialize_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)

def weld_cli_baseline(prompt: Prompt, root: Path) -> str:
    """Return the JSON stdout an unaided ``weld`` CLI invocation would emit.

    Surface choice by category:
      navigation -> ``wd brief``
      dependency -> ``wd query``
      callgraph  -> ``wd references`` (when a symbol is supplied)
    """
    from weld.brief import brief as _brief
    from weld.file_index import find_files as _find_files
    from weld.file_index import load_file_index as _load_file_index
    from weld.graph import Graph as _Graph

    g = _Graph(root)
    g.load()
    if prompt.category == "navigation":
        result = _brief(g, prompt.term, limit=20)
    elif prompt.category == "callgraph" and prompt.symbol:
        refs = g.references(prompt.symbol)
        try:
            index = _load_file_index(root)
            refs["files"] = _find_files(index, prompt.symbol).get("files", [])
        except FileNotFoundError:
            refs.setdefault("files", [])
        result = refs
    else:
        result = g.query(prompt.term, limit=20)
    return _serialize_json(result)

# -- weld MCP baseline (calls dispatch directly; transport is irrelevant) -----

def weld_mcp_baseline(prompt: Prompt, root: Path) -> str:
    """Return the JSON the weld MCP server would send back to a client.

    Bypasses stdio entirely and calls :func:`weld.mcp_server.dispatch` so the
    bench is hermetic. Tool choice mirrors :func:`weld_cli_baseline`.
    """
    from weld.mcp_server import dispatch as _dispatch

    if prompt.category == "navigation":
        tool, args = "weld_brief", {"area": prompt.term}
    elif prompt.category == "callgraph" and prompt.symbol:
        tool, args = "weld_references", {"symbol_name": prompt.symbol}
    else:
        tool, args = "weld_query", {"term": prompt.term}
    try:
        result = _dispatch(tool, args, root=root)
    except Exception as exc:  # pragma: no cover - defensive
        result = {"error": str(exc), "tool": tool, "args": args}
    return _serialize_json(result)

# -- Orchestration + report rendering ---------------------------------------

@dataclass
class BenchResult:
    prompt: Prompt
    grep_tokens: int
    cli_tokens: int
    mcp_tokens: int

    @property
    def reduction_cli(self) -> float | None:
        if self.grep_tokens <= 0:
            return None
        return 1.0 - (self.cli_tokens / self.grep_tokens)

    @property
    def reduction_mcp(self) -> float | None:
        if self.grep_tokens <= 0:
            return None
        return 1.0 - (self.mcp_tokens / self.grep_tokens)

def run_bench(prompts: Iterable[Prompt], root: Path) -> list[BenchResult]:
    """Run all retrieval modes for each prompt and return raw results."""
    results: list[BenchResult] = []
    for prompt in prompts:
        results.append(
            BenchResult(
                prompt=prompt,
                grep_tokens=count_tokens(grep_baseline(prompt, root)),
                cli_tokens=count_tokens(weld_cli_baseline(prompt, root)),
                mcp_tokens=count_tokens(weld_mcp_baseline(prompt, root)),
            )
        )
    return results

def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"

def _summary_lines(label: str, vals: list[float]) -> list[str]:
    if not vals:
        return [f"- {label}: n/a (no positive grep baselines)"]
    median = statistics.median(vals)
    ranked = sorted(vals)
    p90 = ranked[max(0, math.ceil(0.9 * len(ranked)) - 1)]
    return [f"- {label}: median {_fmt_pct(median)}, P90 {_fmt_pct(p90)}"]

def render_report(results: list[BenchResult], *, tokenizer: str | None = None) -> str:
    """Render the markdown report shown in `weld/docs/bench-results.md`."""
    tokenizer = tokenizer or tokenizer_name()
    lines: list[str] = [
        "# Weld retrieval benchmark",
        "",
        "Token cost comparison across retrieval modes for the prompt fixture "
        "in `weld/tests/bench/prompts.yaml`. See `weld/bench/runner.py` for the "
        "harness.",
        "",
        f"Tokenizer: `{tokenizer}`",
        "",
        "| id  | category   | grep tokens | weld CLI tokens | weld MCP tokens | "
        "CLI reduction | MCP reduction |",
        "|-----|------------|-------------|---------------|---------------|"
        "---------------|---------------|",
    ]
    for r in results:
        lines.append(
            "| {id} | {cat} | {g} | {c} | {m} | {rc} | {rm} |".format(
                id=r.prompt.id,
                cat=r.prompt.category,
                g=r.grep_tokens,
                c=r.cli_tokens,
                m=r.mcp_tokens,
                rc=_fmt_pct(r.reduction_cli),
                rm=_fmt_pct(r.reduction_mcp),
            )
        )
    cli_red = [r.reduction_cli for r in results if r.reduction_cli is not None]
    mcp_red = [r.reduction_mcp for r in results if r.reduction_mcp is not None]
    lines += ["", "## Summary", ""]
    lines += _summary_lines("weld CLI vs grep", cli_red)
    lines += _summary_lines("weld MCP vs grep", mcp_red)
    by_cat: dict[str, list[BenchResult]] = {}
    for r in results:
        by_cat.setdefault(r.prompt.category, []).append(r)
    if by_cat:
        lines += ["", "### By category", ""]
        for cat in sorted(by_cat):
            cli_vals = [
                r.reduction_cli for r in by_cat[cat] if r.reduction_cli is not None
            ]
            mcp_vals = [
                r.reduction_mcp for r in by_cat[cat] if r.reduction_mcp is not None
            ]
            cli_med = _fmt_pct(statistics.median(cli_vals)) if cli_vals else "n/a"
            mcp_med = _fmt_pct(statistics.median(mcp_vals)) if mcp_vals else "n/a"
            lines.append(
                f"- **{cat}**: CLI median {cli_med}, MCP median {mcp_med} "
                f"(n={len(by_cat[cat])})"
            )
    return "\n".join(lines).rstrip() + "\n"

# -- CLI entry point: `wd bench` ----------------------------------------
# Moved to weld.bench.bench_cli to keep runner.py under the 400-line limit.
# Legacy ``main`` import for backward compat and ``__main__.py``.

def main(argv: list[str] | None = None) -> int:
    """Delegate to :func:`weld.bench.bench_cli.main`."""
    from weld.bench.bench_cli import main as _cli_main

    return _cli_main(argv)

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
