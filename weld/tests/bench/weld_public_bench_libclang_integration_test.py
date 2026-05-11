"""End-to-end integration tests for the libclang variant.

Covers the runner-level dispatch behavior: language-scoped adapter
selection and the way ``run_public`` populates the per-row
``adapter_results`` dict. Adapter-level unit tests (preconditions,
env-var lifecycle, graph backup/restore) live in
``weld_public_bench_libclang_adapter_test.py``; this file is the
integration layer that ties the adapter to the runner.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.bench.adapters import weld_libclang as libclang_adapter  # noqa: E402


class LanguageScopingTest(unittest.TestCase):
    """``weld_libclang`` only applies to repos whose language is cpp."""

    def test_adapter_applies_for_cpp_repo(self) -> None:
        from weld.bench._public_corpus import (
            CorpusSource,
            PublicRepo,
        )
        from weld.bench._public_runner import _adapter_applies

        cpp = PublicRepo(
            id="x",
            language="cpp",
            source=CorpusSource(kind="local", path="x"),
            tasks=(),
        )
        self.assertTrue(_adapter_applies("weld_libclang", cpp))

    def test_adapter_does_not_apply_for_python_repo(self) -> None:
        from weld.bench._public_corpus import (
            CorpusSource,
            PublicRepo,
        )
        from weld.bench._public_runner import _adapter_applies

        py = PublicRepo(
            id="x",
            language="python",
            source=CorpusSource(kind="local", path="x"),
            tasks=(),
        )
        self.assertFalse(_adapter_applies("weld_libclang", py))

    def test_generic_adapter_always_applies(self) -> None:
        # weld / grep / tree_sitter / graphify are language-agnostic.
        from weld.bench._public_corpus import (
            CorpusSource,
            PublicRepo,
        )
        from weld.bench._public_runner import _adapter_applies

        py = PublicRepo(
            id="x",
            language="python",
            source=CorpusSource(kind="local", path="x"),
            tasks=(),
        )
        for name in ("weld", "grep", "tree_sitter", "graphify"):
            self.assertTrue(
                _adapter_applies(name, py),
                f"{name} must apply to all languages",
            )


def _cpp_task() -> "object":
    """Local factory so test classes don't share state across files."""
    from weld.bench._public_runner import PublicTask

    return PublicTask(
        repo_id="cpp_repo",
        id="cpp-nav-01",
        family="navigation",
        prompt="x",
        term="basic_json",
        symbol="basic_json",
        answer_files=("x.hpp",),
    )


class LibclangCppIntegrationTest(unittest.TestCase):
    """End-to-end: a cpp corpus row gets the libclang column rendered."""

    def test_run_public_dispatches_libclang_for_cpp_row(self) -> None:
        # Synthesize a tiny cpp corpus, run it through run_public with
        # libclang mocked as unavailable, and verify the row carries a
        # weld_libclang slot in its adapter_results dict.
        from weld.bench._public_corpus import (
            CorpusSource,
            PublicCorpus,
            PublicRepo,
            PublicTask,
        )
        from weld.bench._public_runner import run_public

        cpp_task = PublicTask(
            repo_id="cpp_repo",
            id="cpp-nav-01",
            family="navigation",
            prompt="x",
            term="basic_json",
            symbol="basic_json",
            answer_files=("x.hpp",),
        )
        cpp_repo = PublicRepo(
            id="cpp_repo",
            language="cpp",
            source=CorpusSource(kind="local", path="cpp_repo"),
            tasks=(cpp_task,),
        )
        corpus = PublicCorpus(
            schema_version=1,
            corpus_id="cpp_smoke",
            description="",
            repos=(cpp_repo,),
        )
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "cpp_repo").mkdir(parents=True, exist_ok=True)
            with patch.object(
                libclang_adapter, "_is_libclang_available",
                return_value=False,
            ):
                report = run_public(
                    corpus,
                    workdir,
                    adapters=("weld_libclang",),
                    statuses={"cpp_repo": "materialized"},
                )
            self.assertEqual(len(report.rows), 1)
            row = report.rows[0]
            self.assertIn("weld_libclang", row.adapter_results)
            self.assertEqual(
                row.adapter_results["weld_libclang"].status, "unavailable",
            )

    def test_run_public_skips_libclang_for_python_row(self) -> None:
        # Same shape but language=python -- libclang must NOT appear in
        # the row's adapter_results so the rendered table doesn't grow
        # a useless column.
        from weld.bench._public_corpus import (
            CorpusSource,
            PublicCorpus,
            PublicRepo,
            PublicTask,
        )
        from weld.bench._public_runner import run_public

        py_task = PublicTask(
            repo_id="py_repo",
            id="py-nav-01",
            family="navigation",
            prompt="x",
            term="Store",
            symbol="Store",
            answer_files=("src/store.py",),
        )
        py_repo = PublicRepo(
            id="py_repo",
            language="python",
            source=CorpusSource(kind="local", path="py_repo"),
            tasks=(py_task,),
        )
        corpus = PublicCorpus(
            schema_version=1,
            corpus_id="py_smoke",
            description="",
            repos=(py_repo,),
        )
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "py_repo").mkdir(parents=True, exist_ok=True)
            report = run_public(
                corpus,
                workdir,
                adapters=("weld_libclang",),
                statuses={"py_repo": "materialized"},
            )
            self.assertEqual(len(report.rows), 1)
            row = report.rows[0]
            # The python row has NO libclang slot at all -- the renderer
            # then omits the column from the per-task table.
            self.assertNotIn("weld_libclang", row.adapter_results)


if __name__ == "__main__":
    unittest.main()
