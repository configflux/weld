"""CLI dispatch for ``cortex bench``.

Extracted from :mod:`cortex.bench.runner` to keep the runner module under the
400-line limit. Provides both the token-cost benchmark (default) and the
first-context quality benchmark (``--quality``).

"""

from __future__ import annotations

from pathlib import Path

from cortex.bench.runner import (
    load_prompts,
    render_report,
    run_bench,
    tokenizer_name,
)

_DEFAULT_PROMPTS = Path("cortex/tests/bench/prompts.yaml")
_DEFAULT_REPORT = Path("cortex/docs/bench-results.md")
_DEFAULT_QUALITY_CASES = Path("cortex/tests/bench/quality_cases.yaml")
_DEFAULT_QUALITY_REPORT = Path("cortex/docs/bench-quality-results.md")

def _run_token_bench(args) -> int:
    """Run the token-cost benchmark (default mode)."""
    root = args.root.resolve()
    prompts_path = args.prompts or (root / _DEFAULT_PROMPTS)
    out_path = args.out or (root / _DEFAULT_REPORT)
    if not prompts_path.exists():
        print(f"error: prompts fixture not found: {prompts_path}")
        return 1
    prompts = load_prompts(prompts_path)
    if not prompts:
        print(f"error: prompts fixture is empty: {prompts_path}")
        return 1
    results = run_bench(prompts, root)
    report = render_report(results)
    if args.print_only:
        print(report, end="")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(
        f"wrote {out_path} ({len(results)} prompts, "
        f"tokenizer={tokenizer_name()})"
    )
    return 0

def _run_quality_bench(args) -> int:
    """Run the first-context quality benchmark (--quality mode)."""
    from cortex.bench.quality import load_cases, render_quality_report, run_quality

    root = args.root.resolve()
    cases_path = args.cases or (root / _DEFAULT_QUALITY_CASES)
    out_path = args.out or (root / _DEFAULT_QUALITY_REPORT)
    if not cases_path.exists():
        print(f"error: quality cases fixture not found: {cases_path}")
        return 1
    cases = load_cases(cases_path)
    if not cases:
        print(f"error: quality cases fixture is empty: {cases_path}")
        return 1
    results = run_quality(cases, root)
    report = render_quality_report(results)
    if args.print_only:
        print(report, end="")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    passed = sum(1 for r in results if r.passed)
    print(f"wrote {out_path} ({passed}/{len(results)} cases passed)")
    return 0

def main(argv: list[str] | None = None) -> int:
    """Run ``cortex bench``.

    Usage::

        cortex bench [--root REPO] [--prompts YAML] [--out MD] [--print]
        cortex bench --quality [--root REPO] [--cases YAML] [--out MD] [--print]
    """
    import argparse

    parser = argparse.ArgumentParser(prog="cortex bench")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--prompts", type=Path, default=None)
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print the report to stdout instead of writing it.",
    )
    parser.add_argument(
        "--quality",
        action="store_true",
        help="Run the first-context quality benchmark instead of token cost.",
    )
    args = parser.parse_args(argv)

    if args.quality:
        return _run_quality_bench(args)
    return _run_token_bench(args)
