"""CLI structured error contract: machine-readable code + hint on failure.

Companion to ``weld_missing_graph_guidance_test`` (which locks the
``graph_missing`` path). This suite locks the *other* graph-load failure
modes the shared ``weld._errors`` layer must turn into a one-line
``error_code`` + ``hint`` on stderr with a nonzero exit, rather than a raw
traceback:

* corrupt / truncated ``graph.json`` -> ``graph_corrupt``
* a ``meta.schema_version`` newer than this build supports -> ``schema_mismatch``
* a bad / unknown node id for ``wd context`` -> ``node_not_found``

Safety (ADR 0025 / ADR 0035): the corrupt-graph line must NOT echo the raw
file bytes, so a secret accidentally living in a half-written graph cannot
leak to stderr or the terminal scrollback.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


from weld import _errors  # noqa: E402
from weld._graph_cli import main as graph_cli_main  # noqa: E402


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


class CliErrorContractTest(unittest.TestCase):
    """Corrupt / schema-mismatch / bad-node-id all exit nonzero with a code."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._weld = os.path.join(self._tmp, ".weld")
        os.makedirs(self._weld)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_graph(self, text: str) -> None:
        with open(os.path.join(self._weld, "graph.json"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _write_valid_graph(self) -> None:
        graph = {"meta": {"version": 1}, "nodes": {}, "edges": []}
        self._write_graph(json.dumps(graph))

    # ----- corrupt graph -------------------------------------------------

    def test_corrupt_graph_emits_code_and_exits_nonzero(self):
        # Half-written JSON containing a secret value.
        self._write_graph('{"meta": {"api_key": "LEAKME-DEADBEEF"')
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "query", "foo", "--no-refresh"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], stderr)

    def test_directory_graph_emits_code_and_exits_nonzero(self):
        # bd 9yc8: a directory sitting where graph.json should be a file
        # used to escape as a raw IsADirectoryError traceback (worse than
        # the corrupt-JSON case, which at least got the one-line contract).
        graph_path = os.path.join(self._weld, "graph.json")
        os.makedirs(graph_path)
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "stale", "--no-refresh", "--json"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], stderr)
        # SAFETY: no absolute filesystem path and no raw traceback leak.
        self.assertNotIn(self._tmp, stderr)
        self.assertNotIn("Traceback", stderr)

    def test_malformed_shape_graph_emits_code_and_exits_nonzero(self):
        # bd 5038-1c7o: a graph.json that parses fine as JSON but is
        # missing "nodes"/"edges" used to escape Graph.load() as a raw
        # KeyError traceback instead of the one-line contract every other
        # malformed-graph case here gets.
        self._write_graph(json.dumps({"meta": {"version": 1}}))
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "query", "foo", "--no-refresh"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], stderr)
        self.assertNotIn("Traceback", stderr)

    def test_bare_list_graph_emits_code_and_exits_nonzero(self):
        # bd 5038-w0r4: a graph.json whose top-level value is a bare list
        # parses fine as JSON but used to escape Graph.load() as a raw
        # AttributeError ('list' object has no attribute 'get') instead of
        # the one-line contract every other malformed-graph case here gets.
        self._write_graph(json.dumps([1, 2, 3]))
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "query", "foo", "--no-refresh"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], stderr)
        self.assertNotIn("Traceback", stderr)

    def test_scalar_graph_emits_code_and_exits_nonzero(self):
        # Same crash class as the bare-list case above, for a bare scalar
        # top-level payload ('42').
        self._write_graph(json.dumps(42))
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "query", "foo", "--no-refresh"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], stderr)
        self.assertNotIn("Traceback", stderr)

    def test_corrupt_graph_line_does_not_leak_raw_bytes(self):
        self._write_graph('{"meta": {"api_key": "LEAKME-DEADBEEF"')
        _exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "query", "foo", "--no-refresh"],
        )
        # SAFETY: secret bytes from the corrupt file never reach stderr.
        self.assertNotIn("LEAKME-DEADBEEF", stderr)

    def test_corrupt_graph_message_is_one_line(self):
        self._write_graph('{"nodes": ')
        _exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "context", "entity:X", "--no-refresh"],
        )
        # Exactly one non-empty line on stderr (the structured error).
        lines = [ln for ln in stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"expected single line, got: {stderr!r}")

    # ----- schema mismatch ----------------------------------------------

    def test_schema_mismatch_emits_code_and_exits_nonzero(self):
        # schema_version far above what this build supports.
        graph = {
            "meta": {"version": 1, "schema_version": 999},
            "nodes": {},
            "edges": [],
        }
        self._write_graph(json.dumps(graph))
        exit_code, _stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "query", "foo", "--no-refresh"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.SCHEMA_MISMATCH, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.SCHEMA_MISMATCH], stderr)

    # ----- bad node id ---------------------------------------------------

    def test_context_unknown_node_emits_node_not_found(self):
        self._write_valid_graph()
        exit_code, stdout, stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "context", "entity:DoesNotExist",
             "--no-refresh", "--json"],
        )
        # Bad node id is a real failure: nonzero exit + node_not_found code.
        self.assertNotEqual(exit_code, 0)
        combined = stdout + stderr
        self.assertIn(_errors.NODE_NOT_FOUND, combined)

    # ----- valid graph regression ---------------------------------------

    def test_valid_graph_query_still_succeeds(self):
        self._write_valid_graph()
        exit_code, stdout, _stderr = _run_and_capture(
            graph_cli_main,
            ["--root", self._tmp, "query", "foo", "--no-refresh"],
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("no matches", stdout)


class CliReadCommandCorruptGraphTest(unittest.TestCase):
    """The other graph-backed read commands also avoid a raw traceback.

    ``wd brief`` / ``wd trace`` / ``wd impact`` / ``wd enrich`` load the graph
    through the same shared guard as ``_graph_cli``; a corrupt graph must turn
    into the one-line structured error, not a ``JSONDecodeError`` traceback.
    """

    def setUp(self):
        from weld.brief import main as brief_main
        from weld.enrich import main as enrich_main
        from weld.impact import main as impact_main
        from weld.trace import main as trace_main

        self._mains = {
            "brief": brief_main,
            "trace": trace_main,
            "impact": impact_main,
            "enrich": enrich_main,
        }
        self._tmp = tempfile.mkdtemp()
        weld = os.path.join(self._tmp, ".weld")
        os.makedirs(weld)
        with open(os.path.join(weld, "graph.json"), "w", encoding="utf-8") as fh:
            fh.write('{"meta": {"secret": "LEAKME-CLI"')
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _argv(self, cmd: str) -> list:
        if cmd == "enrich":
            return ["--root", self._tmp]
        return ["foo", "--root", self._tmp, "--no-refresh"]

    def test_each_read_command_emits_structured_error(self):
        for cmd, fn in self._mains.items():
            with self.subTest(cmd=cmd):
                exit_code, _stdout, stderr = _run_and_capture(
                    fn, self._argv(cmd)
                )
                self.assertNotEqual(exit_code, 0, f"{cmd} should exit nonzero")
                self.assertIn(_errors.GRAPH_CORRUPT, stderr, f"{cmd}: {stderr!r}")
                # SAFETY: secret bytes never reach stderr on any surface.
                self.assertNotIn("LEAKME-CLI", stderr, f"{cmd} leaked bytes")


class CliExportCorruptGraphTest(unittest.TestCase):
    """``wd export`` also avoids a raw traceback on a bad graph (bd tl32).

    Unlike every other graph-backed read command, ``wd export`` had *no*
    structured-error guard anywhere in its call chain before this fix:
    ``weld/_export_cli.py`` delegated straight to ``weld.export.export()``,
    which calls ``Graph(...).load()`` with no try/except -- so a corrupt or
    truncated ``graph.json`` (or a directory left at the graph path) crashed
    with a raw traceback instead of the one-line
    ``error[<code>]: ... | hint: ...`` contract every sibling command gives.
    """

    def setUp(self):
        from weld._export_cli import run_export

        self._run_export = run_export
        self._tmp = tempfile.mkdtemp()
        self._weld = os.path.join(self._tmp, ".weld")
        os.makedirs(self._weld)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_graph(self, text: str) -> None:
        with open(os.path.join(self._weld, "graph.json"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_corrupt_graph_emits_code_and_exits_nonzero(self):
        # Half-written JSON containing a secret value.
        self._write_graph('{"meta": {"api_key": "LEAKME-EXPORT"')
        exit_code, _stdout, stderr = _run_and_capture(
            self._run_export, ["--root", self._tmp, "--no-refresh"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], stderr)
        # SAFETY: secret bytes from the corrupt file never reach stderr.
        self.assertNotIn("LEAKME-EXPORT", stderr)

    def test_directory_graph_emits_code_and_exits_nonzero(self):
        # A directory sitting where graph.json should be a file. MCP's
        # weld_export already handled this correctly pre-fix (IsADirectoryError
        # is not a ValueError); the CLI had no guard at all, same as the
        # corrupt-JSON case above.
        graph_path = os.path.join(self._weld, "graph.json")
        os.makedirs(graph_path)
        exit_code, _stdout, stderr = _run_and_capture(
            self._run_export, ["--root", self._tmp, "--no-refresh"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], stderr)
        # SAFETY: no absolute filesystem path and no raw traceback leak.
        self.assertNotIn(self._tmp, stderr)
        self.assertNotIn("Traceback", stderr)

    def test_valid_graph_export_still_succeeds(self):
        # No behavior change for a valid graph: the export streams the
        # same Mermaid output as before this fix.
        graph = {"meta": {"version": 1}, "nodes": {}, "edges": []}
        self._write_graph(json.dumps(graph))
        exit_code, stdout, _stderr = _run_and_capture(
            self._run_export, ["--root", self._tmp, "--no-refresh"],
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("flowchart", stdout)

    def test_valid_graph_export_reads_graph_json_exactly_once(self):
        # bd 6vq7: run_export() used to load_graph_or_exit(Graph(...)) for
        # the structured-error guard and then discard that Graph, so
        # weld.export.export() loaded a second Graph internally from the
        # same graph.json -- two reads+JSON-parses per `wd export`
        # invocation. Threading the already-loaded Graph through export()'s
        # graph= parameter must collapse that back to one.
        import weld.graph as graph_mod

        graph = {"meta": {"version": 1}, "nodes": {}, "edges": []}
        self._write_graph(json.dumps(graph))
        with patch(
            "weld.graph.load_graph_file", wraps=graph_mod.load_graph_file,
        ) as mock_load:
            exit_code, stdout, _stderr = _run_and_capture(
                self._run_export, ["--root", self._tmp, "--no-refresh"],
            )
        self.assertEqual(exit_code, 0)
        self.assertIn("flowchart", stdout)
        mock_load.assert_called_once()


if __name__ == "__main__":
    unittest.main()
