"""C++ variant narrative for the public-benchmark report.

Split out of :mod:`weld.bench._public_report` to keep both files under
the 400-line cap and to keep the variant logic isolated from the
generic per-task / per-family rendering.

The narrative is the report's "honest losing" surface for the libclang
C++ methodology: it states tree-sitter median F1 vs. libclang median
F1 across every row that carried a ``weld_libclang`` adapter slot.
When libclang was unavailable or skipped for every C++ row the
narrative says so plainly without editorialising; if libclang shows
no improvement on a header-only library, the numbers speak for
themselves rather than the report.
"""

from __future__ import annotations

import statistics

from weld.bench._public_runner import (
    PublicRunReport,
    accuracy_metrics,
)


def _fmt_metric(value: float) -> str:
    """Stable two-decimal formatting for floats in the narrative."""
    return f"{value:.2f}"


def render_cpp_variant_comparison(
    report: PublicRunReport,
) -> list[str]:
    """Build the markdown lines for the C++ variant narrative.

    Returns an empty list when no row declared a ``weld_libclang``
    slot -- that signal is how a Python-only or polyrepo-only corpus
    avoids picking up an empty C++ section.

    The summary lines are:

      - tree-sitter median F1 across all C++ rows where the
        ``weld`` adapter ran successfully.
      - libclang median F1 across all C++ rows where the
        ``weld_libclang`` adapter ran successfully, OR a stable
        explanation of why libclang did not run (skipped /
        unavailable).
    """
    cpp_rows = [
        row for row in report.rows
        if "weld_libclang" in row.adapter_results
    ]
    if not cpp_rows:
        return []

    weld_f1s: list[float] = []
    libclang_f1s: list[float] = []
    libclang_skipped = 0
    libclang_unavailable = 0
    for row in cpp_rows:
        weld_result = row.adapter_results.get("weld")
        if weld_result is not None and weld_result.status == "ok":
            metric = accuracy_metrics(
                weld_result.files, row.task.answer_files,
            )
            weld_f1s.append(metric.f1)
        libclang_result = row.adapter_results["weld_libclang"]
        if libclang_result.status == "ok":
            metric = accuracy_metrics(
                libclang_result.files, row.task.answer_files,
            )
            libclang_f1s.append(metric.f1)
        elif libclang_result.status == "skipped":
            libclang_skipped += 1
        elif libclang_result.status == "unavailable":
            libclang_unavailable += 1

    lines = ["## C++ variant comparison", ""]
    if weld_f1s:
        weld_summary = (
            f"tree-sitter median F1 = "
            f"{_fmt_metric(statistics.median(weld_f1s))} "
            f"(n={len(weld_f1s)})"
        )
    else:
        weld_summary = "tree-sitter: no rows with status=ok"
    if libclang_f1s:
        libclang_summary = (
            f"libclang median F1 = "
            f"{_fmt_metric(statistics.median(libclang_f1s))} "
            f"(n={len(libclang_f1s)})"
        )
    elif libclang_skipped and not libclang_unavailable:
        libclang_summary = (
            f"libclang: skipped on every C++ row "
            f"({libclang_skipped}/{len(cpp_rows)}; "
            "compile_commands.json not produced)"
        )
    elif libclang_unavailable and not libclang_skipped:
        libclang_summary = (
            f"libclang: unavailable on every C++ row "
            f"({libclang_unavailable}/{len(cpp_rows)}; "
            "the cpp-libclang extra is not installed in this runtime)"
        )
    else:
        libclang_summary = (
            f"libclang: no rows with status=ok "
            f"({libclang_skipped} skipped, "
            f"{libclang_unavailable} unavailable, "
            f"out of {len(cpp_rows)})"
        )
    lines.append(f"- {weld_summary}")
    lines.append(f"- {libclang_summary}")
    return lines


__all__ = ["render_cpp_variant_comparison"]
