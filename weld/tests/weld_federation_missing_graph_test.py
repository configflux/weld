"""Missing-graph guidance for graph-backed reads at a *federation* root.

Field eval v0.23.1, Finding 02 (bd ...uuxaz.2), governed by ADR 0134
("cannot answer" is a distinct outcome from "answered, empty"). At a
federation root (``.weld/workspaces.yaml`` present) with no root graph
(``.weld/graph.json`` absent), the single-repo path already refuses a
graph-backed read with an actionable "No Weld graph found." block and a
non-zero exit -- but the *federated* dispatch branch returned before ever
reaching that precondition, so ``wd query`` / ``wd context`` / ... printed a
well-formed empty result and exited 0, indistinguishable from a genuine
negative answer. The primary consumer is an agent, which cannot apply the
double-take a human might.

This suite locks the ADR 0134 contract on the federated route: every
graph-backed federated read surfaces the same cannot-answer guidance
(reasoned message + non-zero exit) the single-repo route does, while
``wd find`` -- file-index-backed, not graph-backed -- stays exempt (exit 0),
and a federation root that *does* have a root graph still serves normally.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld._graph_cli import main as graph_cli_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_EXPECTED_PREFIX = "No Weld graph found."


def _run_and_capture(argv):
    """Invoke the graph CLI *argv* and return (exit_code, stdout, stderr)."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            graph_cli_main(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            exit_code = 0
        elif isinstance(code, int):
            exit_code = code
        else:
            exit_code = 1
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def _make_federation_root(root: Path) -> None:
    """Write a minimal ``.weld/workspaces.yaml`` so *root* is a federation root.

    Deliberately writes no ``.weld/graph.json`` -- this is the graph-less
    fresh-worktree state Finding 02 reproduces. The registered child does not
    need to exist on disk; the no-graph precondition fires before any child is
    loaded.
    """
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    config = WorkspaceConfig(
        children=[ChildEntry(name="child", path="child", tags=(), remote=None)],
        cross_repo_strategies=[],
    )
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _write_empty_root_graph(root: Path) -> None:
    """Write an empty-but-present ``.weld/graph.json`` at the federation root."""
    import json

    graph = {"meta": {"version": 1}, "nodes": {}, "edges": []}
    (root / ".weld" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")


class FederationMissingGraphGuidanceTest(unittest.TestCase):
    """Graph-backed federated reads refuse with guidance when no root graph."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self._root = Path(self._tmp)
        _make_federation_root(self._root)

    def _cleanup(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _assert_cannot_answer(self, argv, retry_cmd):
        exit_code, _stdout, stderr = _run_and_capture(argv)
        self.assertNotEqual(
            exit_code, 0, f"expected non-zero exit for {argv!r}; stderr={stderr!r}",
        )
        self.assertIn(
            _EXPECTED_PREFIX, stderr,
            f"expected cannot-answer guidance for {argv!r}; stderr={stderr!r}",
        )
        self.assertIn(
            retry_cmd, stderr,
            f"expected retry command {retry_cmd!r} for {argv!r}; stderr={stderr!r}",
        )

    # ----- graph-backed federated reads: cannot-answer, non-zero exit ----

    def test_query_missing_graph(self):
        self._assert_cannot_answer(
            ["--root", self._tmp, "query", "OrderReplayer"], "wd query",
        )

    def test_context_missing_graph(self):
        self._assert_cannot_answer(
            ["--root", self._tmp, "context", "entity:Store"], "wd context",
        )

    def test_path_missing_graph(self):
        self._assert_cannot_answer(
            ["--root", self._tmp, "path", "a:b", "c:d"], "wd path",
        )

    def test_callers_missing_graph(self):
        self._assert_cannot_answer(
            ["--root", self._tmp, "callers", "symbol:py:weld.x:y"], "wd callers",
        )

    def test_references_missing_graph(self):
        self._assert_cannot_answer(
            ["--root", self._tmp, "references", "foo"], "wd references",
        )

    # ----- find stays exempt (file-index, not graph): exit 0 -------------

    def test_find_missing_graph_is_not_guarded(self):
        """``wd find`` at a federation root answers off the file-index.

        ADR 0134 keeps ``find`` a two-outcome tool: it never needed a graph,
        so a graph-less federation root is not a cannot-answer condition for
        it. "No matches" at exit 0 is the correct negative answer.
        """
        exit_code, _stdout, stderr = _run_and_capture(
            ["--root", self._tmp, "find", "OrderReplayer"],
        )
        self.assertEqual(exit_code, 0)
        self.assertNotIn(_EXPECTED_PREFIX, stderr)

    # ----- present root graph still serves: no regression ----------------

    def test_query_with_root_graph_present_does_not_trigger(self):
        """A federation root that has a root graph still serves normally."""
        _write_empty_root_graph(self._root)
        exit_code, _stdout, stderr = _run_and_capture(
            ["--root", self._tmp, "query", "OrderReplayer"],
        )
        self.assertEqual(exit_code, 0)
        self.assertNotIn(_EXPECTED_PREFIX, stderr)


if __name__ == "__main__":
    unittest.main()
