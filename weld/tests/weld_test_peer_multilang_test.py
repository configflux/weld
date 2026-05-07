"""Tests for the multi-language test_peer dispatcher (ADR 0046).

The original Python heuristic remains exercised in
``weld_test_peer_strategy_test.py``. This file covers the per-language
resolvers introduced for Layer C3:

- Go ``*_test.go``
- TS/JS ``*.test|spec.{ts,tsx,js,jsx}`` and ``__tests__/`` dir
- Java ``*Test.java`` / ``*Tests.java``
- C# ``*Test.cs`` / ``*Tests.cs``
- Rust integration tests (``tests/<name>.rs`` -> ``src/<name>.rs``)
- Determinism + unsupported-extension dispatch behavior

Each test case mirrors the shape of the existing Python tests so a
reader can compare per-language behavior side-by-side.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.test_peer import extract


def _touch(path: Path, content: str = "") -> None:
    """Create *path* with *content*, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _MultiLangFixture:
    """Helper mixin: build a temp tree and run extract() with a glob.

    Centralizes the tempdir + ``_touch`` + ``extract`` plumbing so the
    per-language test classes stay focused on the heuristic they
    exercise.
    """

    def _make_tree(self, files: list[str]) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for rel in files:
            _touch(root / rel, "x\n")
        return root

    def _run(self, root: Path, glob: str) -> StrategyResult:
        return extract(
            root,
            {"glob": glob, "type": "file", "strategy": "test_peer"},
            {},
        )


class TestGoTestPeer(unittest.TestCase, _MultiLangFixture):
    """Go convention: ``foo_test.go`` -> ``foo.go`` peer (same dir)."""

    def tearDown(self) -> None:
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def test_emits_edge_when_peer_exists(self) -> None:
        root = self._make_tree(["pkg/foo.go", "pkg/foo_test.go"])
        result = self._run(root, "**/*_test.go")
        self.assertIn("file:pkg/foo_test", result.nodes)
        self.assertEqual(
            result.nodes["file:pkg/foo_test"]["props"]["kind"], "test",
        )
        edges = [
            e for e in result.edges if e["from"] == "file:pkg/foo_test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:pkg/foo")
        self.assertEqual(edges[0]["type"], "tests")
        self.assertEqual(edges[0]["props"]["confidence"], "inferred")
        self.assertEqual(
            edges[0]["props"]["source_strategy"], "test_peer",
        )

    def test_no_edge_when_peer_missing(self) -> None:
        root = self._make_tree(["pkg/foo_test.go"])
        result = self._run(root, "**/*_test.go")
        self.assertIn("file:pkg/foo_test", result.nodes)
        self.assertEqual(result.edges, [])

    def test_legacy_python_alias_not_emitted_for_go(self) -> None:
        # The ``file:tests/<stem>`` alias is Python-only (ADR 0041
        # migration). Go nodes must not carry it.
        root = self._make_tree(["pkg/foo.go", "pkg/foo_test.go"])
        result = self._run(root, "**/*_test.go")
        node = result.nodes["file:pkg/foo_test"]
        self.assertNotIn("aliases", node["props"])


class TestTypeScriptTestPeer(unittest.TestCase, _MultiLangFixture):
    """TS/JS conventions: ``.test``/``.spec`` infix and ``__tests__/`` dir."""

    def tearDown(self) -> None:
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def test_test_ts_resolves_to_ts_peer(self) -> None:
        root = self._make_tree(["src/foo.ts", "src/foo.test.ts"])
        result = self._run(root, "src/*.test.ts")
        edges = [
            e for e in result.edges
            if e["from"] == "file:src/foo.test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:src/foo")

    def test_spec_ts_resolves_to_ts_peer(self) -> None:
        root = self._make_tree(["src/foo.ts", "src/foo.spec.ts"])
        result = self._run(root, "src/*.spec.ts")
        edges = [
            e for e in result.edges
            if e["from"] == "file:src/foo.spec"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:src/foo")

    def test_test_tsx_resolves_to_tsx_peer(self) -> None:
        root = self._make_tree(["src/foo.tsx", "src/foo.test.tsx"])
        result = self._run(root, "src/*.test.tsx")
        edges = [
            e for e in result.edges
            if e["from"] == "file:src/foo.test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:src/foo")

    def test_test_js_resolves_to_js_peer(self) -> None:
        root = self._make_tree(["src/bar.js", "src/bar.test.js"])
        result = self._run(root, "src/*.test.js")
        edges = [
            e for e in result.edges
            if e["from"] == "file:src/bar.test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:src/bar")

    def test_underscore_tests_dir_pairs_with_parent_source(self) -> None:
        # __tests__/foo.ts (no infix) -> ../foo.ts
        root = self._make_tree([
            "src/foo.ts",
            "src/__tests__/foo.ts",
        ])
        result = self._run(root, "src/__tests__/*.ts")
        edges = [
            e for e in result.edges
            if e["from"] == "file:src/__tests__/foo"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:src/foo")

    def test_underscore_tests_dir_with_infix_pairs_with_parent_source(
        self,
    ) -> None:
        root = self._make_tree([
            "src/foo.ts",
            "src/__tests__/foo.test.ts",
        ])
        result = self._run(root, "src/__tests__/*.test.ts")
        edges = [
            e for e in result.edges
            if e["from"] == "file:src/__tests__/foo.test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:src/foo")

    def test_no_edge_when_peer_missing(self) -> None:
        root = self._make_tree(["src/orphan.test.ts"])
        result = self._run(root, "src/*.test.ts")
        self.assertEqual(result.edges, [])

    def test_extension_search_order_prefers_ts_over_tsx(self) -> None:
        # Both peers exist; the deterministic search order picks .ts.
        root = self._make_tree([
            "src/foo.ts", "src/foo.tsx", "src/foo.test.ts",
        ])
        result = self._run(root, "src/*.test.ts")
        edges = [
            e for e in result.edges
            if e["from"] == "file:src/foo.test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:src/foo")


class TestJavaTestPeer(unittest.TestCase, _MultiLangFixture):
    """JUnit conventions: ``FooTest.java`` and ``FooTests.java``."""

    def tearDown(self) -> None:
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def test_singular_test_suffix(self) -> None:
        # Note: ``file_id`` canonicalises to lowercase per ADR 0041's
        # slug rule, so the JVM-style PascalCase stems end up lower in
        # the graph IDs. The on-disk filenames stay PascalCase.
        root = self._make_tree(["pkg/Foo.java", "pkg/FooTest.java"])
        result = self._run(root, "pkg/*Test.java")
        edges = [
            e for e in result.edges if e["from"] == "file:pkg/footest"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:pkg/foo")

    def test_plural_tests_suffix(self) -> None:
        root = self._make_tree(["pkg/Bar.java", "pkg/BarTests.java"])
        result = self._run(root, "pkg/*Tests.java")
        edges = [
            e for e in result.edges if e["from"] == "file:pkg/bartests"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:pkg/bar")

    def test_no_edge_when_peer_missing(self) -> None:
        root = self._make_tree(["pkg/OrphanTest.java"])
        result = self._run(root, "pkg/*Test.java")
        self.assertEqual(result.edges, [])

    def test_lowercase_suffix_not_recognized(self) -> None:
        # ``footest.java`` is not a JUnit test by the case-sensitive rule.
        root = self._make_tree(["pkg/foo.java", "pkg/footest.java"])
        result = self._run(root, "pkg/*test.java")
        # File is matched by glob but should not produce a tests edge.
        edges = [e for e in result.edges if e["type"] == "tests"]
        self.assertEqual(edges, [])


class TestCsharpTestPeer(unittest.TestCase, _MultiLangFixture):
    """xUnit/NUnit/MSTest conventions: ``FooTests.cs`` and ``FooTest.cs``."""

    def tearDown(self) -> None:
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def test_plural_tests_suffix(self) -> None:
        # See note in TestJavaTestPeer: file IDs are lowercased.
        root = self._make_tree(["pkg/Foo.cs", "pkg/FooTests.cs"])
        result = self._run(root, "pkg/*Tests.cs")
        edges = [
            e for e in result.edges if e["from"] == "file:pkg/footests"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:pkg/foo")

    def test_singular_test_suffix(self) -> None:
        root = self._make_tree(["pkg/Bar.cs", "pkg/BarTest.cs"])
        result = self._run(root, "pkg/*Test.cs")
        edges = [
            e for e in result.edges if e["from"] == "file:pkg/bartest"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:pkg/bar")

    def test_no_edge_when_peer_missing(self) -> None:
        root = self._make_tree(["pkg/OrphanTests.cs"])
        result = self._run(root, "pkg/*Tests.cs")
        self.assertEqual(result.edges, [])


class TestRustTestPeer(unittest.TestCase, _MultiLangFixture):
    """Cargo convention: ``tests/<name>.rs`` -> ``src/<name>.rs``."""

    def tearDown(self) -> None:
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def test_integration_test_pairs_with_src_module(self) -> None:
        root = self._make_tree([
            "src/integration.rs",
            "tests/integration.rs",
        ])
        result = self._run(root, "tests/*.rs")
        edges = [
            e for e in result.edges
            if e["from"] == "file:tests/integration"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:src/integration")

    def test_no_edge_when_src_module_missing(self) -> None:
        root = self._make_tree(["tests/orphan.rs"])
        result = self._run(root, "tests/*.rs")
        self.assertEqual(result.edges, [])


class TestDispatcherDeterminism(unittest.TestCase, _MultiLangFixture):
    """Two consecutive runs produce byte-identical edge lists."""

    def tearDown(self) -> None:
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def test_repeated_runs_are_byte_identical(self) -> None:
        root = self._make_tree([
            "pkg/a.go", "pkg/a_test.go",
            "pkg/b.go", "pkg/b_test.go",
            "pkg/c.go", "pkg/c_test.go",
        ])
        first = self._run(root, "**/*_test.go")
        second = self._run(root, "**/*_test.go")
        self.assertEqual(first.edges, second.edges)
        self.assertEqual(list(first.nodes), list(second.nodes))


class TestDispatcherSkipsUnsupported(unittest.TestCase, _MultiLangFixture):
    """Files matching the glob but not any per-language predicate are skipped."""

    def tearDown(self) -> None:
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def test_random_extension_yields_no_node(self) -> None:
        # ``foo.txt`` is not a test in any supported language.
        root = self._make_tree(["pkg/foo.txt"])
        result = self._run(root, "**/*.txt")
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])


if __name__ == "__main__":
    unittest.main()
