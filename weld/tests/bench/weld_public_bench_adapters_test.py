"""Adapter-specific unit tests for ``wd bench --public`` (ADR 0059).

Split out of ``weld_public_bench_test.py`` so each test file stays
within the 400-line cap. The split is by surface (adapter dispatch
vs. corpus / runner / report / family aggregation).

Covers:

  - grep baseline adapter wraps the existing primitive correctly
  - weld adapter returns ``ok`` or ``degraded`` without raising
  - tree-sitter adapter reports ``unavailable`` when binary is missing
  - graphify adapter ``unavailable`` fallback, subprocess timeout
    handling, JSON output parsing
  - dispatcher routes by name and rejects typos
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from weld.bench._public_runner import (  # noqa: E402
    PublicTask,
    dispatch_adapter,
    materialize_smoke_corpus,
)
from weld.bench.adapters import graphify as graphify_adapter  # noqa: E402
from weld.bench.adapters import grep as grep_adapter  # noqa: E402
from weld.bench.adapters import tree_sitter as ts_adapter  # noqa: E402
from weld.bench.adapters import weld as weld_adapter  # noqa: E402

_SMOKE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "bench"
    / "fixtures"
    / "public_corpus_smoke"
)
_SMOKE_MANIFEST = _SMOKE_DIR / "smoke_corpus.yaml"


def _task(family: str = "navigation", **overrides) -> PublicTask:
    """Tiny factory so each test focuses on adapter behavior, not boilerplate."""
    defaults = {
        "repo_id": "repo_a",
        "id": "t1",
        "family": family,
        "prompt": "x",
        "term": "Store",
        "symbol": "Store",
        "answer_files": ("src/store.py",),
    }
    defaults.update(overrides)
    return PublicTask(**defaults)


class GrepAdapterTest(unittest.TestCase):
    def test_returns_answer_files(self) -> None:
        # Run grep adapter against a materialized copy of repo_a. The
        # smoke corpus must be materialized into a temp dir so the file
        # walker can see the fixture files (the fixture itself sits
        # inside a parent git repo, where `git ls-files` returns an
        # empty list because the fixture files are not yet committed).
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            materialize_smoke_corpus(_SMOKE_MANIFEST, tp)
            result = grep_adapter.run(_task(), tp / "repo_a")
            self.assertEqual(result.status, "ok")
            self.assertIn("src/store.py", result.files)
            self.assertGreaterEqual(result.duration_ms, 0)
            # Token count is non-zero on a non-empty match.
            self.assertGreater(result.tokens, 0)


class WeldAdapterTest(unittest.TestCase):
    def test_runs_against_local_repo(self) -> None:
        # Build a temp graph + file index for repo_a, then run the weld
        # adapter. Without a graph, weld_adapter still returns a valid
        # result with status="degraded" rather than crashing.
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            materialize_smoke_corpus(_SMOKE_MANIFEST, tp)
            result = weld_adapter.run(_task(), tp / "repo_a")
            # Adapter must always produce a result envelope (ok | degraded).
            self.assertIn(result.status, ("ok", "degraded"))
            self.assertGreaterEqual(result.duration_ms, 0)


class TreeSitterAdapterTest(unittest.TestCase):
    def test_unavailable_when_binary_missing(self) -> None:
        # tree-sitter CLI is not present in CI by default.
        with patch.object(ts_adapter, "_which", return_value=None):
            result = ts_adapter.run(_task(), _SMOKE_DIR / "repo_a")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.files, [])


class GraphifyAdapterTest(unittest.TestCase):
    def test_unavailable_when_binary_missing(self) -> None:
        # graphify is an external CLI -- when missing, adapter MUST NOT crash.
        with patch.object(graphify_adapter, "_which", return_value=None):
            result = graphify_adapter.run(_task(), _SMOKE_DIR / "repo_a")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.files, [])
        self.assertEqual(result.tokens, 0)
        self.assertEqual(result.cost_usd, 0.0)

    def test_invokes_subprocess_when_available(self) -> None:
        # When the binary IS available, we shell out via subprocess.run.
        fake_stdout = json.dumps(
            {"files": ["src/store.py", "src/use_store.py"]}
        )

        class _FakeResult:
            returncode = 0
            stdout = fake_stdout
            stderr = ""

        with patch.object(
            graphify_adapter, "_which", return_value="/usr/bin/graphify"
        ), patch.object(
            graphify_adapter, "_run_subprocess", return_value=_FakeResult()
        ):
            result = graphify_adapter.run(_task(), _SMOKE_DIR / "repo_a")
        self.assertEqual(result.status, "ok")
        self.assertIn("src/store.py", result.files)

    def test_subprocess_timeout_marks_degraded(self) -> None:
        def _boom(*args, **kw):  # noqa: ARG001
            raise subprocess.TimeoutExpired(cmd=["graphify"], timeout=1)

        with patch.object(
            graphify_adapter, "_which", return_value="/usr/bin/graphify"
        ), patch.object(
            graphify_adapter, "_run_subprocess", side_effect=_boom
        ):
            result = graphify_adapter.run(_task(), _SMOKE_DIR / "repo_a")
        self.assertEqual(result.status, "degraded")

    def test_malformed_json_marks_degraded(self) -> None:
        class _FakeResult:
            returncode = 0
            stdout = "{not valid json"
            stderr = ""

        with patch.object(
            graphify_adapter, "_which", return_value="/usr/bin/graphify"
        ), patch.object(
            graphify_adapter, "_run_subprocess", return_value=_FakeResult()
        ):
            result = graphify_adapter.run(_task(), _SMOKE_DIR / "repo_a")
        self.assertEqual(result.status, "degraded")
        self.assertIn("malformed JSON", result.error)

    def test_nonzero_exit_marks_degraded(self) -> None:
        class _FakeResult:
            returncode = 2
            stdout = ""
            stderr = "graphify: nothing to do"

        with patch.object(
            graphify_adapter, "_which", return_value="/usr/bin/graphify"
        ), patch.object(
            graphify_adapter, "_run_subprocess", return_value=_FakeResult()
        ):
            result = graphify_adapter.run(_task(), _SMOKE_DIR / "repo_a")
        self.assertEqual(result.status, "degraded")
        self.assertIn("nothing to do", result.error)


class DispatchAdapterTest(unittest.TestCase):
    def test_dispatch_routes_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            materialize_smoke_corpus(_SMOKE_MANIFEST, tp)
            result = dispatch_adapter("grep", _task(), tp / "repo_a")
            self.assertEqual(result.status, "ok")

    def test_dispatch_unknown_adapter_raises(self) -> None:
        task = PublicTask(
            repo_id="r",
            id="t1",
            family="navigation",
            prompt="x",
            term="x",
            symbol=None,
            answer_files=(),
        )
        with self.assertRaises(ValueError):
            dispatch_adapter("nope", task, _SMOKE_DIR / "repo_a")

    def test_dispatch_returns_envelope_for_unavailable(self) -> None:
        # tree_sitter / graphify both return AdapterResult envelopes via
        # dispatch even when their binaries are missing -- no exception
        # should leak through.
        with patch.object(ts_adapter, "_which", return_value=None), \
             patch.object(graphify_adapter, "_which", return_value=None):
            ts_result = dispatch_adapter(
                "tree_sitter", _task(), _SMOKE_DIR / "repo_a",
            )
            gr_result = dispatch_adapter(
                "graphify", _task(), _SMOKE_DIR / "repo_a",
            )
        self.assertEqual(ts_result.status, "unavailable")
        self.assertEqual(gr_result.status, "unavailable")


if __name__ == "__main__":
    unittest.main()
