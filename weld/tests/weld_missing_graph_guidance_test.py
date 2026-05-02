"""Missing-graph guidance for read commands (tracked issue, tracked issue).

When a user runs a graph-backed read command (``wd brief`` / ``wd query`` /
``wd context`` / ``wd path`` / ``wd callers`` / ``wd references`` /
``wd trace`` / ``wd impact`` / ``wd diff`` / ``wd enrich``) in a directory
that does not yet contain ``.weld/graph.json``, the CLI used to silently
load an empty graph and return an empty-payload success. This test suite
locks the friendlier behavior: a multi-line actionable block on stderr and
a nonzero exit code. ``wd find`` is intentionally exempt -- it reads the
file-index, not the graph.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# Ensure weld package is importable from the repo root.
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._graph_cli import main as graph_cli_main  # noqa: E402
from weld.brief import main as brief_main  # noqa: E402
from weld.diff import main as diff_main  # noqa: E402
from weld.enrich import main as enrich_main  # noqa: E402
from weld.impact import main as impact_main  # noqa: E402
from weld.trace import main as trace_main  # noqa: E402


_EXPECTED_PREFIX = "No Weld graph found."
_EXPECTED_INIT_HINT = "wd init"
_EXPECTED_DISCOVER_HINT = "wd discover"
_EXPECTED_RETRY_HINT = "Then retry:"


def _run_and_capture(fn, argv):
    """Invoke *fn(argv)* and return (exit_code, stdout, stderr)."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            fn(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            exit_code = 0
        elif isinstance(code, int):
            exit_code = code
        else:
            exit_code = 1
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


class MissingGraphGuidanceTest(unittest.TestCase):
    """All read commands print the actionable block + nonzero exit."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # No .weld directory at all -- cleanest first-run scenario.
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _assert_guidance(self, stderr: str, retry_cmd: str) -> None:
        self.assertIn(_EXPECTED_PREFIX, stderr,
                      f"stderr missing prefix: {stderr!r}")
        self.assertIn(_EXPECTED_INIT_HINT, stderr,
                      f"stderr missing init hint: {stderr!r}")
        self.assertIn(_EXPECTED_DISCOVER_HINT, stderr,
                      f"stderr missing discover hint: {stderr!r}")
        self.assertIn(_EXPECTED_RETRY_HINT, stderr,
                      f"stderr missing retry hint: {stderr!r}")
        self.assertIn(retry_cmd, stderr,
                      f"stderr missing retry command {retry_cmd!r}: {stderr!r}")

    # ----- brief --------------------------------------------------------

    def test_brief_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            brief_main, ["foo", "--root", self._tmp],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd brief")

    # ----- _graph_cli read commands ------------------------------------

    def test_query_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main, ["--root", self._tmp, "query", "foo"],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd query")

    def test_context_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "context", "entity:Store"],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd context")

    def test_path_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "path", "a:b", "c:d"],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd path")

    def test_callers_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "callers", "symbol:py:weld.x:y"],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd callers")

    def test_references_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "references", "foo"],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd references")

    # ----- trace / impact / diff / enrich (tracked issue) -----------------

    def test_trace_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            trace_main, ["foo", "--root", self._tmp],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd trace")

    def test_trace_missing_graph_with_node(self):
        """The --node form of `wd trace` also surfaces guidance."""
        exit_code, _stdout, stderr = _run_and_capture(
            trace_main, ["--node", "entity:Store", "--root", self._tmp],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd trace")

    def test_impact_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            impact_main, ["entity:Store", "--root", self._tmp],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd impact")

    def test_diff_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            diff_main, [self._tmp],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd diff")

    def test_enrich_missing_graph(self):
        exit_code, _stdout, stderr = _run_and_capture(
            enrich_main, ["--root", self._tmp],
        )
        self.assertNotEqual(exit_code, 0)
        self._assert_guidance(stderr, "wd enrich")

    # ----- find is exempt (reads the file-index, not the graph) ---------

    def test_find_missing_graph_is_not_guarded(self):
        """``wd find`` queries the file-index; missing graph is not an error.

        Per the issue scope, ``find`` is intentionally excluded from the
        missing-graph guidance. Users can build and query a file-index
        without ever running ``wd discover``.
        """
        # Provide an empty file-index so `find` has something to search.
        weld_dir = os.path.join(self._tmp, ".weld")
        os.makedirs(weld_dir)
        with open(os.path.join(weld_dir, "file-index.json"), "w") as fh:
            fh.write('{"files": {}}')
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main, ["--root", self._tmp, "find", "foo"],
        )
        self.assertEqual(exit_code, 0)
        self.assertNotIn(_EXPECTED_PREFIX, stderr)

    # ----- does NOT fire when graph is present --------------------------

    def test_query_with_graph_present_does_not_trigger(self):
        """Sanity check: existing empty graph keeps old success behavior.

        The CLI defaults to a human-readable rendering per ADR 0040, so the
        empty-matches signal is the literal "no matches" line. The legacy
        JSON envelope is still reachable via --json and is checked
        separately below.
        """
        import json

        weld_dir = os.path.join(self._tmp, ".weld")
        os.makedirs(weld_dir)
        graph = {"meta": {"version": 1}, "nodes": {}, "edges": []}
        with open(os.path.join(weld_dir, "graph.json"), "w") as fh:
            json.dump(graph, fh)
        exit_code, stdout, stderr = _run_and_capture(
            graph_cli_main, ["--root", self._tmp, "query", "foo"],
        )
        # Old behavior preserved: exit 0, no guidance text. Default is now
        # human-readable; "no matches" is the rendered empty signal.
        self.assertEqual(exit_code, 0)
        self.assertNotIn(_EXPECTED_PREFIX, stderr)
        self.assertIn("no matches", stdout)
        # And --json still emits the JSON envelope unchanged.
        exit_code, stdout, _ = _run_and_capture(
            graph_cli_main, ["--root", self._tmp, "query", "foo", "--json"],
        )
        self.assertEqual(exit_code, 0)
        self.assertIn('"matches": []', stdout)

    def test_diff_with_graph_present_does_not_trigger(self):
        """Sanity check: `wd diff` still works against an existing graph.

        ``load_and_diff`` historically tolerated a missing graph by emitting
        an empty diff; the new guard must not regress the present-graph
        path -- it should still exit 0 and skip the guidance block.
        """
        import json

        weld_dir = os.path.join(self._tmp, ".weld")
        os.makedirs(weld_dir)
        graph = {"meta": {"version": 1}, "nodes": {}, "edges": []}
        with open(os.path.join(weld_dir, "graph.json"), "w") as fh:
            json.dump(graph, fh)
        exit_code, _stdout, stderr = _run_and_capture(
            diff_main, [self._tmp, "--json"],
        )
        self.assertEqual(exit_code, 0)
        self.assertNotIn(_EXPECTED_PREFIX, stderr)


if __name__ == "__main__":
    unittest.main()
