"""Tests for tree-sitter call-graph extraction.

``cortex/docs/adr/0004-call-graph-schema-extension.md``.

Tree-sitter is an optional dependency that is not installed in the
Bazel sandbox, so this test exercises the strategy via mocking. The
goal is to assert the contract: when ``emit_calls: true`` is set on a
source entry, ``_extract_call_edges`` is invoked and emits ``symbol``
nodes plus ``calls`` edges (with the unresolved sentinel form).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.strategies import tree_sitter as ts_strategy  # noqa: E402

class TreeSitterCallgraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        # A trivial TypeScript file -- contents only matter for the
        # mocked extractor.
        (self.tmp / "index.ts").write_text(
            "export function main() { helper(); other(); }\n"
            "export function helper() {}\n",
            encoding="utf-8",
        )

    def test_emit_calls_invokes_callgraph_extractor(self) -> None:
        """When ``emit_calls: true`` is set, the helper must run."""

        def fake_extract(file_path, rel_path, language, queries):
            module = "index"
            return (
                {
                    f"symbol:{language}:{module}:main": {
                        "type": "symbol",
                        "label": "main",
                        "props": {
                            "qualname": "main",
                            "language": language,
                            "source_strategy": "tree_sitter",
                        },
                    },
                    f"symbol:{language}:{module}:helper": {
                        "type": "symbol",
                        "label": "helper",
                        "props": {
                            "qualname": "helper",
                            "language": language,
                            "source_strategy": "tree_sitter",
                        },
                    },
                    "symbol:unresolved:other": {
                        "type": "symbol",
                        "label": "other",
                        "props": {
                            "qualname": "other",
                            "language": language,
                            "resolved": False,
                            "source_strategy": "tree_sitter",
                        },
                    },
                },
                [
                    {
                        "from": f"symbol:{language}:{module}:main",
                        "to": f"symbol:{language}:{module}:helper",
                        "type": "calls",
                        "props": {
                            "source_strategy": "tree_sitter",
                            "resolved": False,
                            "confidence": "speculative",
                        },
                    },
                    {
                        "from": f"symbol:{language}:{module}:main",
                        "to": "symbol:unresolved:other",
                        "type": "calls",
                        "props": {
                            "source_strategy": "tree_sitter",
                            "resolved": False,
                            "confidence": "speculative",
                        },
                    },
                ],
            )

        # Force TREE_SITTER_AVAILABLE so the strategy enters its
        # primary loop, then mock the per-file callgraph helper plus
        # the (irrelevant for this test) symbol parser to a benign no-op.
        with mock.patch.object(ts_strategy, "TREE_SITTER_AVAILABLE", True), \
             mock.patch.object(
                 ts_strategy,
                 "load_language_queries",
                 return_value={"exports": "(_) @name", "calls": "(_) @name"},
             ), \
             mock.patch.object(
                 ts_strategy,
                 "_parse_file_symbols",
                 return_value={"exports": ["main", "helper"]},
             ), \
             mock.patch.object(
                 ts_strategy, "_extract_call_edges", side_effect=fake_extract
             ):
            result = ts_strategy.extract(
                root=self.tmp,
                source={
                    "glob": "*.ts",
                    "language": "typescript",
                    "emit_calls": True,
                },
                context={},
            )

        # Symbol nodes present
        self.assertIn("symbol:typescript:index:main", result.nodes)
        self.assertIn("symbol:typescript:index:helper", result.nodes)
        self.assertIn("symbol:unresolved:other", result.nodes)
        # Calls edges present
        calls_edges = [e for e in result.edges if e["type"] == "calls"]
        self.assertEqual(len(calls_edges), 2)
        froms = {e["from"] for e in calls_edges}
        self.assertEqual(froms, {"symbol:typescript:index:main"})

    def test_emit_calls_default_off(self) -> None:
        """Existing tree_sitter sources without ``emit_calls`` see no edges."""
        with mock.patch.object(ts_strategy, "TREE_SITTER_AVAILABLE", True), \
             mock.patch.object(
                 ts_strategy,
                 "load_language_queries",
                 return_value={"exports": "(_) @name", "calls": "(_) @name"},
             ), \
             mock.patch.object(
                 ts_strategy,
                 "_parse_file_symbols",
                 return_value={"exports": ["main"]},
             ), \
             mock.patch.object(
                 ts_strategy, "_extract_call_edges"
             ) as cg_mock:
            ts_strategy.extract(
                root=self.tmp,
                source={"glob": "*.ts", "language": "typescript"},
                context={},
            )
            cg_mock.assert_not_called()

    def test_calls_query_present_in_language_files(self) -> None:
        """Every loaded language YAML must declare a ``calls`` query."""
        for lang in ("python", "typescript", "go", "rust"):
            queries = ts_strategy.load_language_queries(lang)
            self.assertIn(
                "calls", queries, f"language {lang} missing 'calls' query"
            )

if __name__ == "__main__":
    unittest.main()
