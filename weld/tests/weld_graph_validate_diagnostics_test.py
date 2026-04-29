"""Actionable diagnostics for ``wd validate`` / ``wd validate-fragment``.

Locks validation diagnostics behavior: when a user runs ``wd validate``
against a corrupted graph, each validation error must name the offending
node/edge, describe the violated invariant, and carry a concrete fix
suggestion (``hint: ...``) rather than a bare "validation failed" message.
The CLI also prints a human-readable report block to stderr identifying the
source file, while preserving the existing JSON payload on stdout and
``exit(1)`` when errors are present.
"""
from __future__ import annotations

import io
import json
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
from weld._validate_diagnostics import (  # noqa: E402
    REGEN_HINT,
    dangling_ref_hint,
    format_validation_report,
    missing_edge_field_hint,
    missing_node_field_hint,
    missing_top_level_hint,
    suggest_close_matches,
    vocab_hint,
)
from weld.contract import (  # noqa: E402
    SCHEMA_VERSION,
    VALID_NODE_TYPES,
    ValidationError,
    validate_edge,
    validate_graph,
    validate_meta,
    validate_node,
)


def _run_and_capture(argv):
    """Invoke ``graph_cli_main(argv)`` and return (exit_code, stdout, stderr)."""
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


def _write_graph(root: str, payload: dict) -> Path:
    """Write *payload* to ``<root>/.weld/graph.json`` and return the path."""
    weld_dir = os.path.join(root, ".weld")
    os.makedirs(weld_dir, exist_ok=True)
    path = Path(weld_dir) / "graph.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class ValidationErrorHintTest(unittest.TestCase):
    """``ValidationError.hint`` is additive and backward-compatible."""

    def test_hint_defaults_to_none(self):
        err = ValidationError("p", "f", "m")
        self.assertIsNone(err.hint)

    def test_legacy_str_shape_preserved(self):
        # Pre-existing tests match on ``path.field: message``. That format
        # must survive when no hint is attached.
        self.assertEqual(str(ValidationError("p", "f", "m")), "p.f: m")

    def test_hint_appended_to_str(self):
        rendered = str(ValidationError("p", "f", "m", hint="do X"))
        self.assertIn("p.f: m", rendered)
        self.assertIn("hint: do X", rendered)

    def test_equality_still_works_for_positional_args(self):
        a = ValidationError("x", "y", "z")
        b = ValidationError("x", "y", "z")
        self.assertEqual(a, b)


class DiagnosticHelperTest(unittest.TestCase):
    """Unit coverage for the helpers in ``weld._validate_diagnostics``."""

    def test_suggest_close_matches_returns_nearest(self):
        matches = suggest_close_matches("sevice", VALID_NODE_TYPES)
        self.assertIn("service", matches)

    def test_suggest_close_matches_empty_for_non_string(self):
        self.assertEqual(suggest_close_matches(123, VALID_NODE_TYPES), [])

    def test_vocab_hint_prefers_close_matches(self):
        hint = vocab_hint("sevice", VALID_NODE_TYPES, label="node type")
        self.assertIn("did you mean", hint)
        self.assertIn("service", hint)

    def test_vocab_hint_falls_back_to_full_vocabulary(self):
        # A wildly different value should fall back to the exhaustive list.
        hint = vocab_hint(
            "zzzzz_unrelated", VALID_NODE_TYPES, label="node type",
        )
        self.assertIn("valid node types", hint)
        self.assertIn("service", hint)

    def test_dangling_ref_hint_suggests_close_match(self):
        hint = dangling_ref_hint("entyty:Store", {"entity:Store", "entity:Order"})
        self.assertIn("did you mean", hint)
        self.assertIn("entity:Store", hint)

    def test_dangling_ref_hint_falls_back_when_no_match(self):
        hint = dangling_ref_hint("zzz", {"entity:Store"})
        self.assertIn("no node with id", hint)
        self.assertIn("add a node", hint)

    def test_missing_node_field_hints_are_concrete(self):
        for field in ("type", "label", "props"):
            hint = missing_node_field_hint("entity:Foo", field)
            self.assertIn("entity:Foo", hint)

    def test_missing_edge_field_hints_are_concrete(self):
        for field in ("from", "to", "type", "props"):
            hint = missing_edge_field_hint("a", "b", field)
            self.assertTrue(hint)

    def test_missing_top_level_hint_mentions_regen(self):
        for field in ("meta", "nodes", "edges"):
            hint = missing_top_level_hint(field)
            self.assertIn(REGEN_HINT, hint)

    def test_format_validation_report_shape(self):
        errs = [
            ValidationError("a", "b", "m1", hint="fix 1"),
            ValidationError("c", "d", "m2"),  # no hint
        ]
        report = format_validation_report(errs, source="/tmp/g.json")
        self.assertIn("/tmp/g.json: 2 validation error(s)", report)
        self.assertIn("- a.b: m1", report)
        self.assertIn("hint: fix 1", report)
        self.assertIn("- c.d: m2", report)
        # No-hint errors must not produce an empty "hint:" line.
        self.assertNotIn("hint: \n", report)


class ValidateGraphDiagnosticsTest(unittest.TestCase):
    """Validator-level contract: hints are attached to each error class."""

    def test_invalid_node_type_lists_close_matches(self):
        errs = validate_node(
            "entity:Foo",
            {"type": "sevice", "label": "Foo", "props": {}},
        )
        type_errs = [e for e in errs if e.field == "type"]
        self.assertTrue(type_errs)
        err = type_errs[0]
        self.assertIn("sevice", err.message)
        self.assertIn("entity:Foo", err.message)
        self.assertIsNotNone(err.hint)
        self.assertIn("service", err.hint)

    def test_invalid_edge_type_lists_vocabulary(self):
        errs = validate_edge(
            {"from": "a", "to": "b", "type": "not_a_type", "props": {}},
            {"a", "b"},
        )
        type_errs = [e for e in errs if e.field == "type"]
        self.assertTrue(type_errs)
        self.assertIsNotNone(type_errs[0].hint)
        self.assertIn("valid edge types", type_errs[0].hint)

    def test_dangling_from_reference_hint_suggests_typo_fix(self):
        errs = validate_edge(
            {"from": "entyty:Store", "to": "entity:Order",
             "type": "relates_to", "props": {}},
            {"entity:Store", "entity:Order"},
        )
        from_errs = [e for e in errs if e.field == "from"]
        self.assertTrue(from_errs)
        self.assertIn("entyty:Store", from_errs[0].message)
        self.assertIsNotNone(from_errs[0].hint)
        self.assertIn("did you mean", from_errs[0].hint)
        self.assertIn("entity:Store", from_errs[0].hint)

    def test_dangling_to_reference_hint_without_match(self):
        errs = validate_edge(
            {"from": "entity:Store", "to": "zzz:Unknown",
             "type": "relates_to", "props": {}},
            {"entity:Store"},
        )
        to_errs = [e for e in errs if e.field == "to"]
        self.assertTrue(to_errs)
        self.assertIsNotNone(to_errs[0].hint)
        self.assertIn("add a node", to_errs[0].hint)

    def test_missing_required_field_hint_mentions_node_id(self):
        errs = validate_node(
            "entity:Foo", {"type": "entity", "label": "Foo"},  # no props
        )
        props_errs = [e for e in errs if e.field == "props"]
        self.assertTrue(props_errs)
        self.assertIsNotNone(props_errs[0].hint)
        self.assertIn("entity:Foo", props_errs[0].hint)

    def test_unsupported_schema_version_hint(self):
        errs = validate_meta({"version": 999, "updated_at": "2026-04-24T00:00:00Z"})
        self.assertTrue(errs)
        self.assertIsNotNone(errs[0].hint)
        self.assertIn("wd discover", errs[0].hint)

    def test_top_level_missing_nodes_hint(self):
        errs = validate_graph({
            "meta": {"version": SCHEMA_VERSION, "updated_at": "2026-04-24T00:00:00Z"},
            "edges": [],
        })
        nodes_errs = [e for e in errs if e.field == "nodes"]
        self.assertTrue(nodes_errs)
        self.assertIsNotNone(nodes_errs[0].hint)
        self.assertIn("wd discover", nodes_errs[0].hint)


class ValidateCliStderrReportTest(unittest.TestCase):
    """End-to-end: ``wd validate`` writes an actionable stderr block."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_corrupt_graph_exits_nonzero_and_prints_human_report(self):
        graph_path = _write_graph(self._tmp, {
            "meta": {"version": 999, "updated_at": "now"},
            "nodes": {
                "entity:Foo": {"type": "sevice", "label": "Foo", "props": {}},
            },
            "edges": [
                {"from": "missing:node", "to": "entity:Foo",
                 "type": "contanis", "props": {}},
            ],
        })
        exit_code, stdout, stderr = _run_and_capture(
            ["--root", self._tmp, "validate"],
        )
        self.assertEqual(exit_code, 1, "exit code must signal failure")

        # stderr carries the human-readable block anchored to the graph file.
        self.assertIn(str(graph_path), stderr)
        self.assertIn("validation error(s)", stderr)
        # Each error shows location + hint line.
        self.assertIn("nodes.entity:Foo.type", stderr)
        self.assertIn("hint:", stderr)
        # Typo recovery surfaces for both node and edge vocabularies.
        self.assertIn("service", stderr)
        self.assertIn("contains", stderr)
        # Dangling reference names the missing id.
        self.assertIn("missing:node", stderr)

        # stdout preserves the JSON payload shape so tooling keeps working.
        payload = json.loads(stdout)
        self.assertFalse(payload["valid"])
        self.assertTrue(len(payload["errors"]) >= 3)
        # Each error string carries the enriched hint text.
        self.assertTrue(all("hint:" in e for e in payload["errors"]))

    def test_valid_graph_exits_zero_and_prints_no_report(self):
        _write_graph(self._tmp, {
            "meta": {"version": SCHEMA_VERSION,
                     "updated_at": "2026-04-24T00:00:00Z"},
            "nodes": {},
            "edges": [],
        })
        exit_code, stdout, stderr = _run_and_capture(
            ["--root", self._tmp, "validate"],
        )
        self.assertEqual(exit_code, 0)
        self.assertNotIn("validation error(s)", stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["errors"], [])

    def test_cli_invalid_edge_type_carries_valid_types_list(self):
        _write_graph(self._tmp, {
            "meta": {"version": SCHEMA_VERSION,
                     "updated_at": "2026-04-24T00:00:00Z"},
            "nodes": {
                "a": {"type": "service", "label": "a", "props": {}},
                "b": {"type": "service", "label": "b", "props": {}},
            },
            "edges": [
                {"from": "a", "to": "b", "type": "teleports_to", "props": {}},
            ],
        })
        exit_code, _stdout, stderr = _run_and_capture(
            ["--root", self._tmp, "validate"],
        )
        self.assertEqual(exit_code, 1)
        # Hint enumerates at least one real edge-type so the user can pick.
        self.assertIn("contains", stderr)
        self.assertIn("teleports_to", stderr)


class ValidateFragmentCliDiagnosticsTest(unittest.TestCase):
    """``wd validate-fragment`` emits the same stderr block with the file path."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_corrupt_fragment_stderr_names_file(self):
        # Graph must exist so _graph_cli.py can load a Graph instance before
        # dispatching to the validate-fragment branch.
        _write_graph(self._tmp, {
            "meta": {"version": SCHEMA_VERSION,
                     "updated_at": "2026-04-24T00:00:00Z"},
            "nodes": {}, "edges": [],
        })
        frag_path = Path(self._tmp) / "frag.json"
        frag_path.write_text(json.dumps({
            "nodes": {
                "entity:Foo": {"type": "not_a_type", "label": "Foo",
                               "props": {}},
            },
            "edges": [],
        }), encoding="utf-8")

        exit_code, stdout, stderr = _run_and_capture([
            "--root", self._tmp, "validate-fragment", str(frag_path),
        ])
        self.assertEqual(exit_code, 1)
        self.assertIn(str(frag_path), stderr)
        self.assertIn("validation error(s)", stderr)
        self.assertIn("not_a_type", stderr)

        payload = json.loads(stdout)
        self.assertFalse(payload["valid"])

    def test_trace_inert_fragment_reports_warning(self):
        _write_graph(self._tmp, {
            "meta": {"version": SCHEMA_VERSION,
                     "updated_at": "2026-04-24T00:00:00Z"},
            "nodes": {}, "edges": [],
        })
        frag_path = Path(self._tmp) / "frag.json"
        frag_path.write_text(json.dumps({
            "nodes": {
                "tool:custom-lint": {"type": "tool", "label": "Lint",
                                     "props": {}},
            },
            "edges": [],
        }), encoding="utf-8")

        exit_code, stdout, stderr = _run_and_capture([
            "--root", self._tmp, "validate-fragment", str(frag_path),
        ])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout)
        self.assertTrue(payload["valid"])
        self.assertTrue(payload["warnings"])
        self.assertIn("trace bucket", " ".join(payload["warnings"]))
        self.assertIn("trace bucket", stderr)


if __name__ == "__main__":
    unittest.main()
