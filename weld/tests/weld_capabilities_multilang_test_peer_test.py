"""Cross-strategy capabilities assertion for ADR 0046 multi-language tests.

Layer C3 widens ``test_peer`` to claim ``tests`` evidence across Python,
Go, TS/JS, Java, C#, and Rust. The runtime capability matrix
(:func:`weld.capabilities.compute_capabilities`) must reflect the
upgrade: when the loaded graph contains a file in one of those
languages and ``test_peer`` is wired in ``discover.yaml``, the matching
language row's ``tests`` flag is True. Without those files (or the
strategy not wired) the flag stays False.

This test runs outside the strategy itself -- it exercises the full
registry+graph pipeline that ``wd capabilities`` consumes -- so it
validates the registry update from the consumer side rather than from
the strategy side.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld.capabilities import compute_capabilities  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402


def _make_repo(
    nodes: dict[str, dict],
    yaml_strategies: list[str],
) -> Path:
    """Build a tmp repo with a graph + ``.weld/discover.yaml`` shim.

    Mirrors the helper used in :mod:`weld_capabilities_test`; copied
    here to keep the multi-language suite self-contained.
    """
    root = Path(tempfile.mkdtemp())
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": "2026-05-03T00:00:00+00:00",
                },
                "nodes": nodes,
                "edges": [],
            },
        ),
        encoding="utf-8",
    )
    sources = "\n".join(
        f"  - glob: '*'\n    type: file\n    strategy: {s}"
        for s in yaml_strategies
    )
    (root / ".weld" / "discover.yaml").write_text(
        f"sources:\n{sources}\n", encoding="utf-8",
    )
    return root


_LANG_FILES: dict[str, tuple[str, str]] = {
    # language -> (file_id, file_path)
    "python": ("file:weld/foo_test", "weld/foo_test.py"),
    "go": ("file:pkg/foo_test", "pkg/foo_test.go"),
    "typescript": ("file:src/foo.test", "src/foo.test.ts"),
    "javascript": ("file:src/foo.test", "src/foo.test.js"),
    "java": ("file:pkg/FooTest", "pkg/FooTest.java"),
    "csharp": ("file:pkg/FooTests", "pkg/FooTests.cs"),
    "rust": ("file:tests/integration", "tests/integration.rs"),
}


class TestPeerLightsUpEachLanguage(unittest.TestCase):
    """For each supported language: a matching file flips ``tests`` True."""

    def _run_for_language(self, language: str) -> dict:
        nid, path = _LANG_FILES[language]
        nodes = {nid: {"type": "file", "props": {"file": path}}}
        root = _make_repo(nodes, yaml_strategies=["test_peer"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        return compute_capabilities(graph_data, root)

    def test_python_tests_flag_true(self) -> None:
        result = self._run_for_language("python")
        self.assertTrue(result["languages"]["python"]["tests"])

    def test_go_tests_flag_true(self) -> None:
        result = self._run_for_language("go")
        self.assertTrue(result["languages"]["go"]["tests"])

    def test_typescript_tests_flag_true(self) -> None:
        result = self._run_for_language("typescript")
        self.assertTrue(result["languages"]["typescript"]["tests"])

    def test_javascript_tests_flag_true(self) -> None:
        result = self._run_for_language("javascript")
        self.assertTrue(result["languages"]["javascript"]["tests"])

    def test_java_tests_flag_true(self) -> None:
        result = self._run_for_language("java")
        self.assertTrue(result["languages"]["java"]["tests"])

    def test_csharp_tests_flag_true(self) -> None:
        result = self._run_for_language("csharp")
        self.assertTrue(result["languages"]["csharp"]["tests"])

    def test_rust_tests_flag_true(self) -> None:
        result = self._run_for_language("rust")
        self.assertTrue(result["languages"]["rust"]["tests"])


class TestPeerWithoutMatchingFilesFalse(unittest.TestCase):
    """Without any matching file in the graph, every ``tests`` flag is False.

    Wires ``test_peer`` but seeds the graph with a non-test file shape
    -- the registry-completeness path still surfaces the language rows
    but every flag stays False.
    """

    def test_all_languages_false_when_no_matching_files(self) -> None:
        nodes = {
            "file:other/blob.txt": {
                "type": "file",
                "props": {"file": "other/blob.txt"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["test_peer"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        # Every language test_peer claims should appear with all
        # flags False because no graph file matches the strategy's
        # file-extension signature.
        for language in (
            "python", "go", "typescript", "javascript",
            "java", "csharp", "rust",
        ):
            row = result["languages"].get(language, {})
            self.assertFalse(
                row.get("tests", False),
                f"{language}.tests must be False without matching files",
            )


class TestPeerNotWiredFalse(unittest.TestCase):
    """When ``test_peer`` is not wired, no ``tests`` flag flips to True."""

    def test_strategy_unwired_keeps_flags_false(self) -> None:
        nodes = {
            "file:pkg/foo_test": {
                "type": "file",
                "props": {"file": "pkg/foo_test.go"},
            },
        }
        # Wire something else -- without ``test_peer`` in the active set
        # the multi-language attribution must not light up.
        root = _make_repo(nodes, yaml_strategies=["python_module"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        go_row = result["languages"].get("go", {})
        self.assertFalse(go_row.get("tests", False))


if __name__ == "__main__":
    unittest.main()
