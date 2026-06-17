"""Tests for the libclang variant adapter in the public benchmark.

The libclang adapter mirrors the tree-sitter ``weld`` adapter but:

  1. Requires the optional ``[cpp-libclang]`` extra (``clang.cindex``).
  2. Requires a ``compile_commands.json`` already on disk (produced by
     the manifest's ``setup:`` clause; otherwise the adapter reports
     ``skipped`` with a stable reason).
  3. Injects ``WELD_CPP_LIBCLANG=1`` into the discovery environment so
     the C++ best-in-class methodology activates.

All three preconditions are gated independently so a missing piece
surfaces a distinct, stable reason in the report.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from weld.bench._public_runner import PublicTask  # noqa: E402
from weld.bench.adapters import weld_libclang as libclang_adapter  # noqa: E402


def _cpp_task(**overrides) -> PublicTask:
    defaults = {
        "repo_id": "njson",
        "id": "njson-nav-01",
        "family": "navigation",
        "prompt": "x",
        "term": "basic_json",
        "symbol": "basic_json",
        "answer_files": ("single_include/nlohmann/json.hpp",),
    }
    defaults.update(overrides)
    return PublicTask(**defaults)


class LibclangAdapterPreconditionTest(unittest.TestCase):
    """Each precondition surfaces a distinct status / reason."""

    def test_extra_missing_reports_unavailable(self) -> None:
        # When clang.cindex is not importable, the adapter must report
        # unavailable so the row is excluded from per-family aggregates.
        with tempfile.TemporaryDirectory() as repo_root:
            with patch.object(
                libclang_adapter, "_is_libclang_available",
                return_value=False,
            ):
                result = libclang_adapter.run(
                    _cpp_task(), Path(repo_root),
                )
            self.assertEqual(result.status, "unavailable")
            self.assertEqual(result.files, [])
            self.assertEqual(result.tokens, 0)
            self.assertIn("libclang", result.error.lower())

    def test_extra_present_but_no_db_reports_skipped(self) -> None:
        # Extra available but no compile_commands.json -- adapter must
        # render SKIPPED (this is the cmake-not-on-PATH branch in CI).
        with tempfile.TemporaryDirectory() as repo_root:
            with patch.object(
                libclang_adapter, "_is_libclang_available",
                return_value=True,
            ):
                result = libclang_adapter.run(
                    _cpp_task(), Path(repo_root),
                )
            self.assertEqual(result.status, "skipped")
            self.assertEqual(result.files, [])
            self.assertIn("compile_commands.json", result.error)


class LibclangAdapterEnvVarTest(unittest.TestCase):
    """Active libclang path injects ``WELD_CPP_LIBCLANG=1`` for discovery."""

    def test_env_var_set_during_discovery(self) -> None:
        # When everything lines up (extra present, compile-db present),
        # the adapter must set WELD_CPP_LIBCLANG=1 inside the call that
        # runs discovery + queries the graph.
        seen_env: list[str] = []

        def _capture_env(task, repo_root):  # noqa: ARG001
            # The adapter must have flipped the env var by now.
            seen_env.append(os.environ.get("WELD_CPP_LIBCLANG", ""))
            return {"results": [{"file": "single_include/nlohmann/json.hpp"}]}

        with tempfile.TemporaryDirectory() as repo_root:
            root = Path(repo_root)
            # Lay down a compile-db so the precondition check passes.
            (root / "build").mkdir(parents=True, exist_ok=True)
            (root / "build" / "compile_commands.json").write_text(
                "[]", encoding="utf-8",
            )
            with patch.object(
                libclang_adapter, "_is_libclang_available",
                return_value=True,
            ), patch.object(
                libclang_adapter, "_run_query_with_env",
                side_effect=_capture_env,
            ):
                # Make sure we start from a known env state.
                os.environ.pop("WELD_CPP_LIBCLANG", None)
                result = libclang_adapter.run(_cpp_task(), root)
            # The captured env value taken inside the discovery call.
            self.assertEqual(seen_env, ["1"])
            # And the adapter unwinds back to whatever the parent process
            # had (no leak into the test runner).
            self.assertNotIn("WELD_CPP_LIBCLANG", os.environ)
            self.assertEqual(result.status, "ok")
            self.assertIn(
                "single_include/nlohmann/json.hpp", result.files,
            )

    def test_ensure_graph_does_not_reuse_existing_graph(self) -> None:
        # CRITICAL: a graph written by the tree-sitter ``weld`` adapter
        # would NOT include libclang ground truth. The libclang adapter
        # must force a fresh discovery rather than reusing a stale graph.
        from weld.bench.adapters.weld_libclang import _ensure_graph

        with tempfile.TemporaryDirectory() as repo_root:
            root = Path(repo_root)
            graph_path = root / ".weld" / "graph.json"
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            graph_path.write_text(
                "{\"stale\": \"tree-sitter-built graph\"}",
                encoding="utf-8",
            )
            ok = _ensure_graph(root)
            # The pre-existing graph should have been backed up and
            # replaced. After this call the file content must differ.
            self.assertTrue(ok)
            new_content = graph_path.read_text(encoding="utf-8")
            self.assertNotIn("tree-sitter-built graph", new_content)

    def test_restore_graph_returns_original_to_disk(self) -> None:
        # CRITICAL: after the libclang adapter runs, the pre-libclang
        # graph state must be restored so subsequent adapters (and the
        # weld adapter on the next task in the same repo) do not see a
        # libclang-flavoured graph.
        from weld.bench.adapters.weld_libclang import (
            _ensure_graph,
            _restore_graph,
        )

        original = '{"flavor": "tree-sitter"}'
        with tempfile.TemporaryDirectory() as repo_root:
            root = Path(repo_root)
            graph_path = root / ".weld" / "graph.json"
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            graph_path.write_text(original, encoding="utf-8")
            _ensure_graph(root)
            # Between ensure and restore, the graph is libclang-shaped.
            mid = graph_path.read_text(encoding="utf-8")
            self.assertNotEqual(mid, original)
            _restore_graph(root)
            # After restore, the original tree-sitter graph is back.
            self.assertEqual(
                graph_path.read_text(encoding="utf-8"), original,
            )
            # The backup is gone.
            self.assertFalse(
                (root / ".weld" / "graph.json.weld_libclang_backup").exists()
            )

    def test_restore_graph_removes_libclang_graph_when_no_prior(self) -> None:
        # If there was no pre-existing graph, restore must remove the
        # libclang one so the next adapter starts from scratch.
        from weld.bench.adapters.weld_libclang import (
            _ensure_graph,
            _restore_graph,
        )

        with tempfile.TemporaryDirectory() as repo_root:
            root = Path(repo_root)
            graph_path = root / ".weld" / "graph.json"
            self.assertFalse(graph_path.exists())
            _ensure_graph(root)
            self.assertTrue(graph_path.exists())
            _restore_graph(root)
            self.assertFalse(graph_path.exists())

    def test_ensure_graph_failure_returns_degraded(self) -> None:
        # When all gates pass but discovery itself fails to produce a
        # graph, the adapter must surface degraded rather than ok with
        # empty files.
        with tempfile.TemporaryDirectory() as repo_root:
            root = Path(repo_root)
            (root / "compile_commands.json").write_text(
                "[]", encoding="utf-8",
            )
            with patch.object(
                libclang_adapter, "_is_libclang_available",
                return_value=True,
            ), patch.object(
                libclang_adapter, "_ensure_graph", return_value=False,
            ):
                os.environ.pop("WELD_CPP_LIBCLANG", None)
                result = libclang_adapter.run(_cpp_task(), root)
            self.assertEqual(result.status, "degraded")
            self.assertIn("discovery failed", result.error)
            # Env restoration still happens even on early-return.
            self.assertNotIn("WELD_CPP_LIBCLANG", os.environ)

    def test_env_var_restored_on_exception(self) -> None:
        # If the underlying discovery raises, the env var must still be
        # cleaned up so subsequent calls don't carry stale state.

        def _boom(task, repo_root):  # noqa: ARG001
            raise RuntimeError("libclang exploded")

        with tempfile.TemporaryDirectory() as repo_root:
            root = Path(repo_root)
            (root / "compile_commands.json").write_text(
                "[]", encoding="utf-8",
            )
            with patch.object(
                libclang_adapter, "_is_libclang_available",
                return_value=True,
            ), patch.object(
                libclang_adapter, "_run_query_with_env", side_effect=_boom,
            ):
                os.environ.pop("WELD_CPP_LIBCLANG", None)
                result = libclang_adapter.run(_cpp_task(), root)
            self.assertEqual(result.status, "degraded")
            self.assertIn("exploded", result.error)
            self.assertNotIn("WELD_CPP_LIBCLANG", os.environ)


class LibclangAdapterDispatchTest(unittest.TestCase):
    """The runner dispatch table recognises ``weld_libclang``."""

    def test_dispatch_accepts_libclang_name(self) -> None:
        from weld.bench._public_runner import dispatch_adapter

        with tempfile.TemporaryDirectory() as repo_root:
            with patch.object(
                libclang_adapter, "_is_libclang_available",
                return_value=False,
            ):
                result = dispatch_adapter(
                    "weld_libclang", _cpp_task(), Path(repo_root),
                )
            # Unavailable because we mocked the gate; what matters is
            # that dispatch did NOT raise ValueError for an unknown name.
            self.assertEqual(result.status, "unavailable")

    def test_libclang_in_default_adapter_set(self) -> None:
        from weld.bench._public_runner import DEFAULT_ADAPTERS

        self.assertIn("weld_libclang", DEFAULT_ADAPTERS)


if __name__ == "__main__":
    unittest.main()
