"""Regression tests for the ``wd`` CLI flag-naming convention.

The convention is:

- Node id arguments are positional, named ``<node>`` in help.
- Project root uses ``--root``.
- Output path uses ``--output``.
- Format toggles use ``--json`` / ``--format=mermaid|dot|d2``.

This module asserts each subcommand's primary input form matches the
convention, and that deprecated aliases (``wd export --node``) still work
but emit a ``DeprecationWarning`` to stderr for one release.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.cli import _HELP, main as cli_main  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402


def _write_minimal_graph(root: Path) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-04-06T00:00:00+00:00",
                },
                "nodes": {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {"file": "domain/store.py"},
                    },
                },
                "edges": [],
            }
        ),
        encoding="utf-8",
    )


class ExportPositionalNodeTest(unittest.TestCase):
    """``wd export <node>`` is the canonical form for the node id arg."""

    def test_positional_node_routes_to_export(self) -> None:
        with mock.patch("weld.export.export") as mock_export:
            mock_export.return_value = ""
            rc = cli_main(["export", "entity:Store"])
        self.assertEqual(rc, 0)
        self.assertEqual(mock_export.call_count, 1)
        kwargs = mock_export.call_args.kwargs
        self.assertEqual(kwargs.get("node_id"), "entity:Store")

    def test_positional_node_with_format(self) -> None:
        with mock.patch("weld.export.export") as mock_export:
            mock_export.return_value = ""
            rc = cli_main(["export", "entity:Store", "--format", "dot"])
        self.assertEqual(rc, 0)
        args = mock_export.call_args
        self.assertEqual(args.args[0], "dot")
        self.assertEqual(args.kwargs.get("node_id"), "entity:Store")

    def test_no_node_argument_still_works(self) -> None:
        with mock.patch("weld.export.export") as mock_export:
            mock_export.return_value = ""
            rc = cli_main(["export", "--format", "mermaid"])
        self.assertEqual(rc, 0)
        self.assertIsNone(mock_export.call_args.kwargs.get("node_id"))


class ExportDeprecatedNodeFlagTest(unittest.TestCase):
    """``wd export --node <id>`` still works but warns once per call."""

    def test_deprecated_node_flag_still_routes(self) -> None:
        buf = io.StringIO()
        with mock.patch("weld.export.export") as mock_export:
            mock_export.return_value = ""
            with redirect_stderr(buf):
                rc = cli_main(["export", "--node", "entity:Store"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            mock_export.call_args.kwargs.get("node_id"), "entity:Store"
        )

    def test_deprecated_node_flag_emits_deprecation_warning(self) -> None:
        buf = io.StringIO()
        with mock.patch("weld.export.export") as mock_export:
            mock_export.return_value = ""
            with redirect_stderr(buf):
                cli_main(["export", "--node", "entity:Store"])
        stderr = buf.getvalue()
        self.assertIn("DeprecationWarning", stderr)
        self.assertIn("--node", stderr)

    def test_positional_does_not_emit_deprecation(self) -> None:
        buf = io.StringIO()
        with mock.patch("weld.export.export") as mock_export:
            mock_export.return_value = ""
            with redirect_stderr(buf):
                cli_main(["export", "entity:Store"])
        self.assertNotIn("DeprecationWarning", buf.getvalue())


class ExportHelpTextTest(unittest.TestCase):
    """``wd export --help`` describes the positional ``<node>`` arg."""

    def test_export_help_lists_positional_node(self) -> None:
        # argparse prints help to stdout and exits 0 via SystemExit.
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                cli_main(["export", "--help"])
            except SystemExit:
                pass
        out = buf.getvalue()
        # Positional metavar should be ``node`` (argparse uppercases by
        # default, so check for either form).
        self.assertTrue("node" in out.lower())

    def test_export_help_marks_node_flag_deprecated(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                cli_main(["export", "--help"])
            except SystemExit:
                pass
        out = buf.getvalue().lower()
        self.assertIn("--node", out)
        self.assertIn("deprecated", out)


class PositionalNodeCommandsRegressionTest(unittest.TestCase):
    """``impact`` / ``context`` / ``callers`` / ``references`` keep
    accepting a positional node id (no regression after this change)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        _write_minimal_graph(self.tmp)

    def test_context_accepts_positional(self) -> None:
        # ``wd context entity:Store`` should not raise an argparse error.
        # We invoke the parser directly to avoid running graph traversal
        # on the minimal fixture (still asserts argparse contract).
        from weld._graph_cli_parser import build_parser

        parser = build_parser()
        ns = parser.parse_args(["context", "entity:Store"])
        self.assertEqual(ns.node_id, "entity:Store")

    def test_callers_accepts_positional(self) -> None:
        from weld._graph_cli_parser import build_parser

        parser = build_parser()
        ns = parser.parse_args(["callers", "_load_strategy"])
        self.assertEqual(ns.symbol, "_load_strategy")

    def test_references_accepts_positional(self) -> None:
        from weld._graph_cli_parser import build_parser

        parser = build_parser()
        ns = parser.parse_args(["references", "_load_strategy"])
        self.assertEqual(ns.name, "_load_strategy")

    def test_impact_accepts_positional(self) -> None:
        # ``wd impact`` lives in ``weld.impact`` with its own parser; it
        # must still take the target as the first positional. We assert
        # the argparse contract by introspecting the parser ``main``
        # builds, rather than executing a full graph traversal.
        import argparse

        from weld import impact as impact_mod

        # Capture the parser by patching parse_args -- we just need to
        # observe argparse acceptance of a positional ``target``.
        captured: dict[str, argparse.Namespace] = {}
        original = argparse.ArgumentParser.parse_args

        def _capture(self, argv=None):  # type: ignore[no-untyped-def]
            ns = original(self, argv)
            captured["ns"] = ns
            raise SystemExit(0)  # short-circuit before graph dispatch

        with mock.patch.object(argparse.ArgumentParser, "parse_args", _capture):
            with self.assertRaises(SystemExit):
                impact_mod.main(["entity:Store"])
        self.assertEqual(captured["ns"].target, "entity:Store")


class HelpTextDescribesNodeMetavarTest(unittest.TestCase):
    """``wd <cmd> --help`` for node-id commands shows ``node`` in usage."""

    def _help_for(self, cmd: str) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                cli_main([cmd, "--help"])
            except SystemExit:
                pass
        return buf.getvalue().lower()

    def test_context_help_shows_node(self) -> None:
        self.assertIn("node", self._help_for("context"))


class HelpListsExportTest(unittest.TestCase):
    """The top-level help still mentions ``export`` after the rename."""

    def test_export_in_top_help(self) -> None:
        self.assertIn("export", _HELP)


if __name__ == "__main__":
    unittest.main()
