"""`wd diff` / `weld_diff` must surface a corrupt CURRENT graph, not hide it.

Regression for the silent-degradation bug: ``weld.diff.load_and_diff`` used to
catch ``(json.JSONDecodeError, OSError)`` for the current ``graph.json`` and
treat it as ``current=None``, so ``wd diff`` on a corrupt graph reported an
empty diff and exited 0 -- a silent failure that looks like "no changes".

``diff`` keeps an *intentional* tolerance for a missing/corrupt
``graph-previous.json`` (the previous snapshot is optional). The contract this
suite locks distinguishes the two:

* CURRENT ``graph.json`` fails to parse  -> structured ``graph_corrupt`` error
  + nonzero exit (CLI) / structured payload (MCP).
* PREVIOUS snapshot fails to parse        -> tolerated (treated as absent), the
  current graph still diffs as all-added.

Safety (ADR 0025 / ADR 0035, shared ``weld._errors`` contract): the
corrupt-graph surface must NOT echo the raw file bytes, so a secret living in a
half-written graph cannot leak to stderr, terminal scrollback, or an MCP
payload.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


from weld import _errors  # noqa: E402

_SECRET = "LEAKME-DIFF-DEADBEEF"
_CORRUPT_WITH_SECRET = '{"meta": {"api_key": "' + _SECRET + '"'


def _base_graph() -> dict:
    """Minimal valid graph (3 nodes / 2 edges) for the present-current paths.

    Inlined rather than imported from ``diff_fixtures`` so this test depends
    only on its own source file -- it shares the ``//weld:runtime`` error
    comprehension in BUILD.bazel with the sibling error-contract suites.
    """
    return {
        "meta": {"version": 1},
        "nodes": {
            "entity:Store": {"type": "entity", "label": "Store", "props": {}},
            "entity:Offer": {"type": "entity", "label": "Offer", "props": {}},
            "route:GET:/stores": {"type": "route", "label": "list", "props": {}},
        },
        "edges": [
            {"from": "entity:Offer", "to": "entity:Store",
             "type": "depends_on", "props": {}},
            {"from": "route:GET:/stores", "to": "entity:Store",
             "type": "responds_with", "props": {}},
        ],
    }


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


def _write(weld_dir: Path, name: str, text: str) -> None:
    weld_dir.mkdir(parents=True, exist_ok=True)
    weld_dir.joinpath(name).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit: load_and_diff raises on a corrupt CURRENT graph, tolerates PREVIOUS
# ---------------------------------------------------------------------------

class LoadAndDiffCorruptCurrentTest(unittest.TestCase):
    """``load_and_diff`` must not swallow a corrupt current graph."""

    def _import_diff(self):
        from weld import diff as diff_mod
        return diff_mod

    def test_corrupt_current_raises_json_decode_error(self) -> None:
        diff_mod = self._import_diff()
        root = Path(tempfile.mkdtemp())
        _write(root / ".weld", "graph.json", _CORRUPT_WITH_SECRET)
        # Must NOT return a silent empty diff -- the parse failure propagates
        # so each surface (CLI / MCP) can translate it to graph_corrupt.
        with self.assertRaises(json.JSONDecodeError):
            diff_mod.load_and_diff(root)

    def test_corrupt_previous_is_tolerated_current_all_added(self) -> None:
        diff_mod = self._import_diff()
        root = Path(tempfile.mkdtemp())
        weld_dir = root / ".weld"
        _write(weld_dir, "graph.json", json.dumps(_base_graph()))
        # Previous snapshot is half-written; diff must still succeed and
        # report the current graph as all-added (previous treated as absent).
        _write(weld_dir, "graph-previous.json", '{"nodes": ')
        result = diff_mod.load_and_diff(root)
        self.assertEqual(len(result["added_nodes"]), 3)
        self.assertEqual(result["removed_nodes"], [])

    def test_no_graph_at_all_still_empty_diff(self) -> None:
        diff_mod = self._import_diff()
        root = Path(tempfile.mkdtemp())
        (root / ".weld").mkdir(parents=True, exist_ok=True)
        # Absent current graph stays tolerant (the missing-graph guidance is a
        # separate CLI guard); load_and_diff itself returns an empty diff.
        result = diff_mod.load_and_diff(root)
        self.assertEqual(result["added_nodes"], [])


# ---------------------------------------------------------------------------
# CLI: `wd diff` emits the structured graph_corrupt line + nonzero exit
# ---------------------------------------------------------------------------

class DiffCliCorruptGraphTest(unittest.TestCase):
    """``wd diff`` turns a corrupt current graph into the shared error line."""

    def _diff_main(self):
        from weld.diff import main as diff_main
        return diff_main

    def _seed_corrupt(self) -> str:
        tmp = tempfile.mkdtemp()
        _write(Path(tmp) / ".weld", "graph.json", _CORRUPT_WITH_SECRET)
        return tmp

    def test_human_corrupt_exits_nonzero_with_code_and_hint(self) -> None:
        tmp = self._seed_corrupt()
        exit_code, stdout, stderr = _run_and_capture(self._diff_main(), [tmp])
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], stderr)
        # The dangerous "no changes" signal must NOT be emitted.
        self.assertNotIn("No changes", stdout)

    def test_json_corrupt_exits_nonzero_with_code(self) -> None:
        tmp = self._seed_corrupt()
        exit_code, _stdout, stderr = _run_and_capture(
            self._diff_main(), [tmp, "--json"],
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn(_errors.GRAPH_CORRUPT, stderr)

    def test_corrupt_line_does_not_leak_raw_bytes(self) -> None:
        tmp = self._seed_corrupt()
        _exit_code, stdout, stderr = _run_and_capture(self._diff_main(), [tmp])
        # SAFETY: the secret from the half-written graph never reaches output.
        self.assertNotIn(_SECRET, stderr)
        self.assertNotIn(_SECRET, stdout)

    def test_corrupt_message_is_one_line(self) -> None:
        tmp = self._seed_corrupt()
        _exit_code, _stdout, stderr = _run_and_capture(self._diff_main(), [tmp])
        lines = [ln for ln in stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"expected single line, got: {stderr!r}")

    def test_valid_current_no_previous_still_exits_zero(self) -> None:
        # Regression guard: the present/valid path is unchanged.
        tmp = tempfile.mkdtemp()
        _write(Path(tmp) / ".weld", "graph.json", json.dumps(_base_graph()))
        exit_code, _stdout, stderr = _run_and_capture(self._diff_main(), [tmp])
        self.assertEqual(exit_code, 0)
        self.assertNotIn(_errors.GRAPH_CORRUPT, stderr)


# ---------------------------------------------------------------------------
# MCP: weld_diff dispatch returns the structured payload (shares load_and_diff)
# ---------------------------------------------------------------------------

class WeldDiffMcpCorruptGraphTest(unittest.TestCase):
    """``weld_diff`` MCP dispatch surfaces graph_corrupt, never crashes/leaks."""

    def _root_with(self, graph_text: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        _write(tmp / ".weld", "graph.json", graph_text)
        return tmp

    def test_dispatch_corrupt_returns_structured_error(self) -> None:
        from weld import mcp_server
        root = self._root_with(_CORRUPT_WITH_SECRET)
        result = mcp_server.dispatch("weld_diff", {}, root=root)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertIn("hint", result)
        # SAFETY: the secret never appears in the payload.
        self.assertNotIn(_SECRET, json.dumps(result))

    def test_dispatch_corrupt_does_not_raise(self) -> None:
        from weld import mcp_server
        root = self._root_with('{"nodes": ')
        result = mcp_server.dispatch("weld_diff", {}, root=root)
        self.assertIn("error_code", result)

    def test_safe_dispatch_corrupt_becomes_text_payload(self) -> None:
        from weld import mcp_server
        root = self._root_with('{"meta": ')
        payload = mcp_server.dispatch_to_text_payload("weld_diff", {}, root=root)
        decoded = json.loads(payload)
        self.assertEqual(decoded.get("error_code"), _errors.GRAPH_CORRUPT)


if __name__ == "__main__":
    unittest.main()
