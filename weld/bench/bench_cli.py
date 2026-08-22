"""CLI dispatch for ``wd bench``.

Extracted from :mod:`weld.bench.runner` to keep the runner module under the
400-line limit. Provides five modes:

  - default             : token-cost benchmark (grep / CLI / MCP)
  - ``--quality``       : first-context quality benchmark (brief / trace)
  - ``--compare``       : comparative agent-task benchmark (grep vs. weld)
  - ``--report``        : re-render the compare markdown report from a
                          prior JSON artifact
  - ``--public``        : public benchmark methodology (ADR 0059) -- runs
                          a SHA-pinned corpus through adapters
                          (weld/grep/tree-sitter/graphify) and emits a
                          deterministic markdown report

Each mode writes a markdown report to an out-path (or prints it when
``--print`` is passed). The compare mode additionally writes a
machine-readable ``.json`` artifact alongside the report so ``--report``
can regenerate the markdown without re-running retrieval. The public
mode supports ``--verify`` for byte-identical reproducibility checks.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from weld._safe_text import sanitize_terminal_line, sanitize_terminal_text
from weld._version import weld_version
from weld.bench.runner import (
    load_prompts,
    render_report,
    run_bench,
    tokenizer_name,
)

_DEFAULT_PROMPTS = Path("weld/tests/bench/prompts.yaml")
_DEFAULT_REPORT = Path("weld/docs/bench-results.md")
_DEFAULT_QUALITY_CASES = Path("weld/tests/bench/quality_cases.yaml")
_DEFAULT_QUALITY_REPORT = Path("weld/docs/bench-quality-results.md")
_DEFAULT_COMPARE_TASKS = Path("weld/bench_tasks/fixtures/default.yaml")
_DEFAULT_COMPARE_REPORT = Path("weld/docs/bench-compare-results.md")
_DEFAULT_PUBLIC_CORPUS = Path("weld/bench/public_corpus.yaml")
_DEFAULT_PUBLIC_REPORT = Path("docs/bench/PUBLIC-BENCHMARK.md")


def _resolve_public_report_path(root: Path) -> Path:
    """Compute the default ``--public`` report path.

    The committable artifact lives at
    ``docs/bench/PUBLIC-BENCHMARK-<version>.md`` so each release ships
    a versioned report (ADR 0059 release cadence). That version names
    the weld that earned the numbers, so it comes from the same
    :mod:`weld._version` resolver the report header uses -- one fact,
    resolved twice at different moments, must not answer twice.

    Notably *not* from ``<root>/VERSION``: ``root`` is the repository
    being benchmarked, so an installed weld pointed at someone else's
    tree used to name the report after their release history.

    ``root`` still anchors the directory -- the report is written beside
    the tree it describes. When the version cannot be resolved at all
    the unversioned path stands: the header can say "unknown" because it
    is prose, but a filename has no room to say it and must not stamp a
    version-shaped non-version instead.
    """
    report = root / _DEFAULT_PUBLIC_REPORT
    version = weld_version()
    if version:
        return report.with_stem(f"{report.stem}-{version}")
    return report


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
        # Only the tty copy is escaped; the artifact keeps its exact bytes.
        print(sanitize_terminal_text(report), end="")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(
        sanitize_terminal_line(
            f"wrote {out_path} ({len(results)} prompts, "
            f"tokenizer={tokenizer_name()})"
        )
    )
    return 0


def _run_quality_bench(args) -> int:
    """Run the first-context quality benchmark (--quality mode)."""
    from weld.bench.quality import (
        load_cases,
        render_quality_report,
        run_quality,
    )

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
        print(sanitize_terminal_text(report), end="")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    passed = sum(1 for r in results if r.passed)
    print(f"wrote {out_path} ({passed}/{len(results)} cases passed)")
    return 0


def _run_compare_bench(args) -> int:
    """Run the comparative agent-task benchmark (--compare mode)."""
    from weld.bench_tasks import (
        load_tasks,
        render_compare_report,
        run_compare,
    )

    root = args.root.resolve()
    tasks_path = args.tasks or (root / _DEFAULT_COMPARE_TASKS)
    out_path = args.out or (root / _DEFAULT_COMPARE_REPORT)
    if not tasks_path.exists():
        print(f"error: tasks fixture not found: {tasks_path}")
        return 1
    tasks = load_tasks(tasks_path)
    if not tasks:
        print(f"error: tasks fixture is empty: {tasks_path}")
        return 1
    if args.task:
        filtered = [t for t in tasks if t.id == args.task]
        if not filtered:
            known = ", ".join(t.id for t in tasks)
            print(
                f"error: task {args.task!r} not found in {tasks_path} "
                f"(known ids: {known})"
            )
            return 1
        tasks = filtered
    results = run_compare(tasks, root)
    report = render_compare_report(results)
    if args.print_only:
        print(sanitize_terminal_text(report), end="")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    artifact_path = out_path.with_suffix(".json")
    artifact = {
        "tokenizer": tokenizer_name(),
        "results": [r.to_dict() for r in results],
    }
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"wrote {out_path} ({len(results)} tasks, "
        f"artifact={artifact_path})"
    )
    return 0


def _run_report(args) -> int:
    """Re-render the comparative report from a prior JSON artifact."""
    from weld.bench_tasks import render_compare_report
    from weld.bench_tasks.compare import CompareResult

    if not args.artifact:
        print("error: --report requires --artifact PATH")
        return 1
    artifact_path = Path(args.artifact)
    if not artifact_path.exists():
        print(f"error: artifact not found: {artifact_path}")
        return 1
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: artifact is not valid JSON: {exc}")
        return 1
    results = [
        CompareResult.from_dict(r) for r in payload.get("results", [])
    ]
    report = render_compare_report(results)
    out_path = args.out
    if out_path is None or args.print_only:
        print(sanitize_terminal_text(report), end="")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"wrote {out_path} ({len(results)} tasks from {artifact_path})")
    return 0


def _run_public_bench(args) -> int:
    """Run the public benchmark (--public mode, ADR 0059)."""
    from weld.bench._public_report import render_public_report
    from weld.bench._public_runner import load_public_corpus, run_public
    from weld.bench._public_setup import materialize_corpus

    root = args.root.resolve()
    corpus_path = args.corpus or (root / _DEFAULT_PUBLIC_CORPUS)
    out_path = args.out or _resolve_public_report_path(root)
    if not corpus_path.exists():
        print(f"error: public corpus manifest not found: {corpus_path}")
        return 1
    try:
        corpus = load_public_corpus(corpus_path)
    except ValueError as exc:
        print(f"error: invalid public corpus manifest: {exc}")
        return 1

    with tempfile.TemporaryDirectory(prefix="wd-bench-public-") as workdir:
        workdir_p = Path(workdir)
        # Materialize every repo (local copy / git clone-on-demand /
        # honest skip for placeholder SHAs) before dispatch. ADR 0059
        # mandates honest output: a placeholder SHA renders SKIPPED
        # rows rather than a degraded crash.
        statuses = materialize_corpus(corpus, corpus_path, workdir_p)
        report = run_public(corpus, workdir_p, statuses=statuses)
        rendered = render_public_report(report)

    if args.verify:
        if not out_path.exists():
            print(
                f"error: --verify requires a prior report at {out_path} "
                f"(missing). Run without --verify to create one."
            )
            return 1
        existing = out_path.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                "error: --verify failed: regenerated report differs from "
                f"{out_path}. Non-determinism leaked into the benchmark."
            )
            return 1
        print(
            f"verified {out_path} byte-identical "
            f"({len(report.rows)} tasks, corpus={corpus.corpus_id})"
        )
        return 0

    if args.print_only:
        print(sanitize_terminal_text(rendered), end="")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {out_path} ({len(report.rows)} tasks, "
        f"corpus={corpus.corpus_id}, weld={report.weld_version})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run ``wd bench``.

    Usage::

        wd bench [--root REPO] [--prompts YAML] [--out MD] [--print]
        wd bench --quality [--root REPO] [--cases YAML] [--out MD]
        wd bench --compare [--root REPO] [--tasks YAML] [--task ID]
                               [--out MD] [--print]
        wd bench --report --artifact PATH [--out MD] [--print]
        wd bench --public [--corpus YAML] [--out MD] [--print] [--verify]
    """
    import argparse

    parser = argparse.ArgumentParser(prog="wd bench")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--prompts", type=Path, default=None)
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--tasks", type=Path, default=None)
    parser.add_argument("--task", default=None, help="Filter --compare to a single task id.")
    parser.add_argument("--artifact", type=Path, default=None)
    parser.add_argument(
        "--corpus", type=Path, default=None,
        help=(
            "Path to the public-corpus YAML manifest "
            "(default: weld/bench/public_corpus.yaml)."
        ),
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "With --public: re-run the corpus and assert byte-identity "
            "against the existing report at --out."
        ),
    )
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print the report to stdout instead of writing it.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--quality",
        action="store_true",
        help="Run the first-context quality benchmark instead of token cost.",
    )
    mode_group.add_argument(
        "--compare",
        action="store_true",
        help="Run the comparative agent-task benchmark (grep vs. weld).",
    )
    mode_group.add_argument(
        "--report",
        action="store_true",
        help="Re-render the comparative report from a prior --artifact.",
    )
    mode_group.add_argument(
        "--public",
        action="store_true",
        help="Run the public benchmark against a SHA-pinned corpus.",
    )
    args = parser.parse_args(argv)

    if args.report:
        return _run_report(args)
    if args.compare:
        return _run_compare_bench(args)
    if args.quality:
        return _run_quality_bench(args)
    if args.public:
        return _run_public_bench(args)
    return _run_token_bench(args)
