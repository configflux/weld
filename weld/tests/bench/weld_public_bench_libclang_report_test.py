"""Report-rendering tests for the libclang variant column.

The markdown report from the public-benchmark runner already iterates
over whichever adapters appear in the first row. These tests assert:

  - When both ``weld`` and ``weld_libclang`` produce ``ok`` results,
    they render in adjacent cells with the same metric schema.
  - When ``weld_libclang`` reports ``unavailable`` (extra not installed
    in the runtime) the cell renders as ``unavailable`` and is excluded
    from the per-family aggregate.
  - When ``weld_libclang`` reports ``skipped`` (cmake not on PATH or
    compile_commands.json absent) the cell renders as
    ``SKIPPED: <reason>`` and is excluded from the per-family aggregate.
  - The narrative section reports per-language F1 deltas (tree-sitter
    vs. libclang) without editorializing the result.
"""

from __future__ import annotations

import unittest


from weld.bench._public_report import (  # noqa: E402
    aggregate_by_family,
    render_public_report,
)
from weld.bench._public_runner import (  # noqa: E402
    AdapterResult,
    PublicRowResult,
    PublicRunReport,
    PublicTask,
)


def _cpp_task(task_id: str = "njson-nav-01") -> PublicTask:
    return PublicTask(
        repo_id="nlohmann_json",
        id=task_id,
        family="navigation",
        prompt="x",
        term="basic_json",
        symbol="basic_json",
        answer_files=("single_include/nlohmann/json.hpp",),
    )


def _report(rows: list[PublicRowResult]) -> PublicRunReport:
    return PublicRunReport(
        corpus_id="public-v0",
        schema_version=1,
        weld_version="0.17.2",
        rows=rows,
    )


class BothColumnsRenderTest(unittest.TestCase):
    """When both variants produce ``ok``, both cells render side by side."""

    def test_both_columns_appear_in_per_task_table(self) -> None:
        row = PublicRowResult(
            task=_cpp_task(),
            adapter_results={
                "weld": AdapterResult(
                    status="ok",
                    files=["single_include/nlohmann/json.hpp"],
                    tokens=10,
                    duration_ms=1.0,
                ),
                "weld_libclang": AdapterResult(
                    status="ok",
                    files=["single_include/nlohmann/json.hpp"],
                    tokens=15,
                    duration_ms=2.5,
                ),
            },
        )
        md = render_public_report(_report([row]))
        self.assertIn("weld_libclang", md)
        self.assertIn("weld", md)
        # The cell schema is F1=... P=... R=... tokens=N status=ok.
        self.assertIn("F1=1.00", md)
        # Tokens reflect the libclang adapter's reported count.
        self.assertIn("tokens=15", md)


class LibclangUnavailableTest(unittest.TestCase):
    """``unavailable`` cell renders distinctly and is excluded from agg."""

    def test_unavailable_cell_renders(self) -> None:
        row = PublicRowResult(
            task=_cpp_task(),
            adapter_results={
                "weld": AdapterResult(
                    status="ok",
                    files=["single_include/nlohmann/json.hpp"],
                    tokens=10,
                    duration_ms=1.0,
                ),
                "weld_libclang": AdapterResult(
                    status="unavailable",
                    files=[],
                    tokens=0,
                    duration_ms=0.0,
                    error="libclang extra not installed",
                ),
            },
        )
        md = render_public_report(_report([row]))
        # The libclang column is in the table header.
        self.assertIn("weld_libclang", md)
        # And the cell renders as "unavailable".
        self.assertIn("unavailable", md)

    def test_unavailable_excluded_from_aggregate(self) -> None:
        row = PublicRowResult(
            task=_cpp_task(),
            adapter_results={
                "weld_libclang": AdapterResult(
                    status="unavailable",
                    files=[],
                    tokens=0,
                    duration_ms=0.0,
                    error="libclang extra not installed",
                ),
            },
        )
        agg = aggregate_by_family([row], "weld_libclang")
        self.assertEqual(agg, {})


class LibclangSkippedTest(unittest.TestCase):
    """``skipped`` cell renders ``SKIPPED: <reason>`` deterministically."""

    def test_skipped_cell_includes_reason(self) -> None:
        row = PublicRowResult(
            task=_cpp_task(),
            adapter_results={
                "weld_libclang": AdapterResult(
                    status="skipped",
                    files=[],
                    tokens=0,
                    duration_ms=0.0,
                    error="compile_commands.json not produced (cmake unavailable)",
                ),
            },
        )
        md = render_public_report(_report([row]))
        self.assertIn("SKIPPED", md)
        self.assertIn("compile_commands.json", md)
        self.assertIn("cmake", md)

    def test_skipped_excluded_from_aggregate(self) -> None:
        row = PublicRowResult(
            task=_cpp_task(),
            adapter_results={
                "weld_libclang": AdapterResult(
                    status="skipped",
                    files=[],
                    tokens=0,
                    duration_ms=0.0,
                    error="placeholder",
                ),
            },
        )
        agg = aggregate_by_family([row], "weld_libclang")
        self.assertEqual(agg, {})


class CppVariantNarrativeTest(unittest.TestCase):
    """The report includes a per-language tree-sitter vs libclang summary."""

    def test_narrative_present_when_cpp_rows_exist(self) -> None:
        rows = [
            PublicRowResult(
                task=_cpp_task("njson-nav-01"),
                adapter_results={
                    "weld": AdapterResult(
                        status="ok",
                        files=["single_include/nlohmann/json.hpp"],
                        tokens=10,
                        duration_ms=1.0,
                    ),
                    "weld_libclang": AdapterResult(
                        status="ok",
                        files=["single_include/nlohmann/json.hpp"],
                        tokens=10,
                        duration_ms=1.0,
                    ),
                },
            ),
        ]
        md = render_public_report(_report(rows))
        # A new section calls out the variant comparison.
        self.assertIn("C++ variant comparison", md)
        # Both per-variant F1s appear in the narrative.
        self.assertIn("tree-sitter", md.lower())
        self.assertIn("libclang", md.lower())

    def test_narrative_when_libclang_skipped(self) -> None:
        rows = [
            PublicRowResult(
                task=_cpp_task("njson-nav-01"),
                adapter_results={
                    "weld": AdapterResult(
                        status="ok",
                        files=["single_include/nlohmann/json.hpp"],
                        tokens=10,
                        duration_ms=1.0,
                    ),
                    "weld_libclang": AdapterResult(
                        status="skipped",
                        files=[],
                        tokens=0,
                        duration_ms=0.0,
                        error="cmake not available",
                    ),
                },
            ),
        ]
        md = render_public_report(_report(rows))
        self.assertIn("C++ variant comparison", md)
        # When skipped, narrative names the gap honestly.
        self.assertIn("skipped", md.lower())

    def test_no_cpp_narrative_when_no_cpp_rows(self) -> None:
        # Python-only corpus row: the C++ variant section must NOT appear.
        py_task = PublicTask(
            repo_id="flask",
            id="flask-nav-01",
            family="navigation",
            prompt="x",
            term="Flask",
            symbol="Flask",
            answer_files=("src/flask/app.py",),
        )
        rows = [
            PublicRowResult(
                task=py_task,
                adapter_results={
                    "weld": AdapterResult(
                        status="ok",
                        files=["src/flask/app.py"],
                        tokens=10,
                        duration_ms=1.0,
                    ),
                },
            ),
        ]
        md = render_public_report(_report(rows))
        self.assertNotIn("C++ variant comparison", md)


if __name__ == "__main__":
    unittest.main()
