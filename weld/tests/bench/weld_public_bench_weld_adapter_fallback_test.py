"""File-index fallback tests for the ``weld`` public-bench adapter.

Regression coverage: the adapter only consulted ``file_index`` for the
``callgraph`` family, so dependency / impact / cross_repo / navigation
tasks scored F1=0.00 against the public C++ corpus whenever the graph
was empty (no ``.weld/discover.yaml``) or BM25 buried the answer file.

The contract these tests pin:

  1. Empty / sparse graph + populated file_index ->
     every family augments ``files`` with file-index hits.
  2. Graph already returns >= the threshold of file hits -> NO
     augmentation (precision-preservation).
  3. file-index.json missing -> no crash, unaugmented result.
  4. Existing ``callgraph`` merge still works (regression guard).

Split out of ``weld_public_bench_weld_adapter_test.py`` to keep both
files under the 400-line cap (CLAUDE.md "Line-Count Policy").
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from weld.bench._public_runner import PublicTask  # noqa: E402
from weld.bench.adapters import weld as weld_adapter  # noqa: E402


def _task(**overrides) -> PublicTask:
    """Build a task with tree-sitter-friendly defaults; overrides win."""
    defaults = {
        "repo_id": "njson",
        "id": "njson-fallback-01",
        "family": "dependency",
        "prompt": "x",
        "term": "parser",
        "symbol": None,
        "answer_files": ("include/nlohmann/parser.hpp",),
    }
    defaults.update(overrides)
    return PublicTask(**defaults)


def _write_empty_graph(repo_root: Path) -> None:
    """Write a minimal ``.weld/graph.json`` with zero nodes/edges.

    ``_ensure_graph`` short-circuits when ``graph.json`` already exists,
    so the tests stay hermetic (no tree-sitter, no discover.yaml).
    """
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": "1", "schema_version": 1},
        "nodes": {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8",
    )


def _write_file_index(
    repo_root: Path, files: dict[str, list[str]],
) -> None:
    """Write a ``.weld/file-index.json`` with ``path -> tokens`` map.

    Mirrors the on-disk envelope produced by
    :func:`weld.file_index.save_file_index`.
    """
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    envelope = {"meta": {"version": 1}, "files": files}
    (weld_dir / "file-index.json").write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class FileIndexFallbackTest(unittest.TestCase):
    """Non-callgraph families must consult ``file_index`` when graph is sparse."""

    def test_dependency_family_augments_with_file_index(self) -> None:
        # The exact bug class: dependency task, empty graph, populated
        # file-index -> the answer file MUST appear in result.files.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_empty_graph(root)
            _write_file_index(root, {
                "include/nlohmann/detail/input/parser.hpp": [
                    "parser", "Parser", "input",
                ],
                "docs/unrelated.md": ["other", "doc"],
            })
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=True,
            ):
                result = weld_adapter.run(
                    _task(
                        family="dependency",
                        term="parser",
                        symbol=None,
                        answer_files=(
                            "include/nlohmann/detail/input/parser.hpp",
                        ),
                    ),
                    root,
                )
            self.assertEqual(result.status, "ok")
            self.assertIn(
                "include/nlohmann/detail/input/parser.hpp",
                result.files,
            )

    def test_impact_family_augments_with_file_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_empty_graph(root)
            _write_file_index(root, {
                "tests/unit-deserialization.cpp": [
                    "unit-deserialization", "test",
                ],
            })
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=True,
            ):
                result = weld_adapter.run(
                    _task(
                        family="impact",
                        term="unit-deserialization",
                        symbol=None,
                        answer_files=(
                            "tests/unit-deserialization.cpp",
                        ),
                    ),
                    root,
                )
            self.assertEqual(result.status, "ok")
            self.assertIn(
                "tests/unit-deserialization.cpp", result.files,
            )

    def test_cross_repo_family_augments_with_file_index(self) -> None:
        # ``cross_repo`` was the second 0.00-F1 family in the original
        # bench run -- CMakeLists.txt is in INDEXED_FILENAMES but no
        # graph node exists for it without explicit configuration.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_empty_graph(root)
            _write_file_index(root, {
                "CMakeLists.txt": ["CMakeLists", "cmake", "project"],
            })
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=True,
            ):
                result = weld_adapter.run(
                    _task(
                        family="cross_repo",
                        term="CMakeLists",
                        symbol=None,
                        # No tree-sitter language -> no gate trip.
                        answer_files=("CMakeLists.txt",),
                    ),
                    root,
                )
            self.assertEqual(result.status, "ok")
            self.assertIn("CMakeLists.txt", result.files)

    def test_navigation_family_augments_with_file_index(self) -> None:
        # The ``navigation`` branch goes through ``brief()``; the brief
        # envelope from an empty graph carries no ``primary`` matches
        # at all, so the answer file must come from the file_index.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_empty_graph(root)
            _write_file_index(root, {
                "single_include/nlohmann/json.hpp": [
                    "basic_json", "json",
                ],
            })
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=True,
            ):
                result = weld_adapter.run(
                    _task(
                        family="navigation",
                        term="basic_json",
                        symbol=None,
                        answer_files=(
                            "single_include/nlohmann/json.hpp",
                        ),
                    ),
                    root,
                )
            self.assertEqual(result.status, "ok")
            self.assertIn(
                "single_include/nlohmann/json.hpp", result.files,
            )

    def test_callgraph_family_still_uses_file_index(self) -> None:
        # Regression guard: the existing callgraph fallback must keep
        # working after the refactor -- the original branch already
        # merged file_index hits and the rewrite must not drop them.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_empty_graph(root)
            _write_file_index(root, {
                "include/nlohmann/json_pointer.hpp": [
                    "json_pointer", "Pointer",
                ],
            })
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=True,
            ):
                result = weld_adapter.run(
                    _task(
                        family="callgraph",
                        term="json_pointer",
                        symbol="json_pointer",
                        answer_files=(
                            "include/nlohmann/json_pointer.hpp",
                        ),
                    ),
                    root,
                )
            self.assertEqual(result.status, "ok")
            self.assertIn(
                "include/nlohmann/json_pointer.hpp", result.files,
            )

    def test_threshold_guard_skips_augmentation_when_graph_is_rich(
        self,
    ) -> None:
        # When the graph already supplies >= the fallback threshold of
        # file hits, the file_index MUST NOT be merged in -- doing so
        # would dilute precision without improving recall. We construct
        # a graph with 3 file nodes whose label/file matches the term,
        # then put a DIFFERENT path in file_index and assert it is NOT
        # in the result.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir(parents=True, exist_ok=True)
            graph_payload = {
                "meta": {"version": "1", "schema_version": 1},
                "nodes": {
                    "file:src/parser_a": {
                        "type": "file",
                        "label": "parser_a",
                        "props": {"file": "src/parser_a.py"},
                    },
                    "file:src/parser_b": {
                        "type": "file",
                        "label": "parser_b",
                        "props": {"file": "src/parser_b.py"},
                    },
                    "file:src/parser_c": {
                        "type": "file",
                        "label": "parser_c",
                        "props": {"file": "src/parser_c.py"},
                    },
                },
                "edges": [],
            }
            (weld_dir / "graph.json").write_text(
                json.dumps(graph_payload, indent=2) + "\n",
                encoding="utf-8",
            )
            _write_file_index(root, {
                "include/should_NOT_be_added.hpp": ["parser"],
            })
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=True,
            ):
                result = weld_adapter.run(
                    _task(
                        family="dependency",
                        term="parser",
                        symbol=None,
                        # Python answer-files don't trip the
                        # tree-sitter gate.
                        answer_files=("src/parser_a.py",),
                    ),
                    root,
                )
            self.assertEqual(result.status, "ok")
            # Graph supplied >= 3 file hits -> file_index hit must NOT
            # have been merged.
            self.assertNotIn(
                "include/should_NOT_be_added.hpp", result.files,
            )

    def test_missing_file_index_does_not_crash(self) -> None:
        # ``.weld/file-index.json`` absent -> the adapter must NOT raise
        # FileNotFoundError; it should fall through to the empty-graph
        # path with no augmentation.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_empty_graph(root)  # graph yes, file-index no
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=True,
            ):
                result = weld_adapter.run(
                    _task(
                        family="dependency",
                        term="parser",
                        symbol=None,
                        answer_files=("include/parser.hpp",),
                    ),
                    root,
                )
            # Status is "ok" because g.query returned an envelope
            # (just no matches). The point is we did not crash.
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.files, [])


if __name__ == "__main__":
    unittest.main()
