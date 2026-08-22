"""bd gyve: jest.mock / vi.mock module targets become graph edges.

The TS/JS peer of ``weld_mock_patch_python_test.py``. Two layers: the scanner
and resolver in isolation, then ``test_peer.extract`` end to end, because the
value of the feature is the edge landing in the graph -- a resolver that works
while the dispatch hook does not is indistinguishable from the blind spot the
issue reported.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies import _mock_module_ts, test_peer


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class MockTargetScanTest(unittest.TestCase):
    """What the regex recognises, and what it declines."""

    def test_jest_and_vi_spellings_are_both_found(self) -> None:
        source = (
            'jest.mock("./alpha");\n'
            "vi.mock('../beta');\n"
        )
        self.assertEqual(
            _mock_module_ts.mock_targets(source),
            [("./alpha", 1), ("../beta", 2)],
        )

    def test_domock_is_found_and_unmock_is_not(self) -> None:
        """``doMock`` establishes the dependency; ``unmock`` removes it."""
        source = 'jest.doMock("./a");\njest.unmock("./b");\n'
        self.assertEqual(_mock_module_ts.mock_targets(source), [("./a", 1)])

    def test_whitespace_and_multiline_calls_are_found(self) -> None:
        source = 'jest . mock (\n  "./spread"\n);\n'
        self.assertEqual(_mock_module_ts.mock_targets(source), [("./spread", 1)])

    def test_commented_out_calls_are_skipped(self) -> None:
        source = (
            '// jest.mock("./commented");\n'
            ' * jest.mock("./jsdoc");\n'
            'jest.mock("./live");\n'
        )
        self.assertEqual(_mock_module_ts.mock_targets(source), [("./live", 3)])

    def test_multiplication_is_not_mistaken_for_a_block_comment(self) -> None:
        """``*`` only marks a comment when it *opens* the line."""
        source = 'const n = a * b; jest.mock("./live");\n'
        self.assertEqual(_mock_module_ts.mock_targets(source), [("./live", 1)])

    def test_non_literal_target_is_not_guessed(self) -> None:
        """A computed specifier is not statically resolvable, so it is dropped."""
        source = "jest.mock(modulePath);\njest.mock(`./${name}`);\n"
        self.assertEqual(_mock_module_ts.mock_targets(source), [])

    def test_line_numbers_are_reported_for_provenance(self) -> None:
        source = '\n\n\njest.mock("./late");\n'
        self.assertEqual(_mock_module_ts.mock_targets(source), [("./late", 4)])


class ResolveMockTargetTest(unittest.TestCase):
    """The resolution bar: prove the file, or emit nothing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cache: dict = _mock_module_ts.new_cache()

    def _resolve(self, rel: str, specifier: str):
        return _mock_module_ts.resolve_mock_target(
            self.root, Path(rel), specifier, self.cache
        )

    def test_sibling_module_resolves_to_its_file_node(self) -> None:
        _write(self.root, "src/gateway.ts", "export const pay = 1;\n")
        self.assertEqual(
            self._resolve("src/gateway.test.ts", "./gateway"),
            ("file:src/gateway", False),
        )

    def test_parent_relative_specifier_resolves(self) -> None:
        _write(self.root, "src/lib/thing.ts", "export const t = 1;\n")
        self.assertEqual(
            self._resolve("src/__tests__/a.test.ts", "../lib/thing"),
            ("file:src/lib/thing", False),
        )

    def test_explicit_extension_is_reported_as_exact(self) -> None:
        """An explicit, existing extension is proof rather than inference."""
        _write(self.root, "src/mod.ts", "export const m = 1;\n")
        self.assertEqual(
            self._resolve("src/mod.test.ts", "./mod.ts"),
            ("file:src/mod", True),
        )

    def test_esm_js_specifier_resolves_to_its_ts_source(self) -> None:
        """TS emits ESM importing ``./a.js`` from source ``a.ts``."""
        _write(self.root, "src/a.ts", "export const a = 1;\n")
        self.assertEqual(
            self._resolve("src/a.test.ts", "./a.js"),
            ("file:src/a", False),
        )

    def test_directory_specifier_resolves_to_index(self) -> None:
        _write(self.root, "src/pkg/index.ts", "export const p = 1;\n")
        self.assertEqual(
            self._resolve("src/pkg.test.ts", "./pkg"),
            ("file:src/pkg/index", False),
        )

    def test_extension_search_order_is_deterministic(self) -> None:
        """``.ts`` wins over ``.js`` when both exist, per the declared order."""
        _write(self.root, "src/dual.ts", "export const d = 1;\n")
        _write(self.root, "src/dual.js", "module.exports = 1;\n")
        self.assertEqual(
            self._resolve("src/dual.test.ts", "./dual"),
            ("file:src/dual", False),
        )

    def test_bare_package_specifier_is_dropped(self) -> None:
        """node_modules / moduleNameMapper targets are not guessed at."""
        _write(self.root, "src/axios.ts", "export const nope = 1;\n")
        self.assertIsNone(self._resolve("src/a.test.ts", "axios"))
        self.assertIsNone(self._resolve("src/a.test.ts", "@scope/pkg"))

    def test_missing_file_is_dropped(self) -> None:
        self.assertIsNone(self._resolve("src/a.test.ts", "./gone"))

    def test_specifier_escaping_the_root_is_dropped(self) -> None:
        self.assertIsNone(self._resolve("src/a.test.ts", "../../outside/x"))


class TestPeerTsMockEdgeTest(unittest.TestCase):
    """End to end through the strategy that owns the dispatch hook."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _extract(self, glob: str = "src/**/*.test.ts"):
        return test_peer.extract(self.root, {"glob": glob}, {})

    def test_mock_edge_is_emitted_for_a_ts_test_file(self) -> None:
        _write(self.root, "src/gateway.ts", "export const pay = 1;\n")
        _write(self.root, "src/other.ts", "export const o = 1;\n")
        _write(
            self.root,
            "src/gateway.test.ts",
            'jest.mock("./other");\nimport { pay } from "./gateway";\n',
        )
        result = self._extract()
        mock_edges = [
            e for e in result.edges
            if (e["props"] or {}).get("resolution") == "mock_patch"
        ]
        self.assertEqual(len(mock_edges), 1, result.edges)
        edge = mock_edges[0]
        self.assertEqual(edge["from"], "file:src/gateway.test")
        self.assertEqual(edge["to"], "file:src/other")
        self.assertEqual(edge["type"], "depends_on")
        self.assertEqual(edge["props"]["raw"], "./other")

    def test_edge_carries_test_file_provenance_not_the_mocked_module(self) -> None:
        """ADR 0074: the stamp is the file whose scan produced the edge."""
        _write(self.root, "src/dep.ts", "export const d = 1;\n")
        _write(self.root, "src/a.test.ts", '\njest.mock("./dep");\n')
        edge = [
            e for e in self._extract().edges
            if (e["props"] or {}).get("resolution") == "mock_patch"
        ][0]
        self.assertEqual(
            edge["props"]["provenance"],
            {"file": "src/a.test.ts", "line": 2},
        )

    def test_confidence_splits_on_whether_the_extension_was_explicit(self) -> None:
        _write(self.root, "src/exact.ts", "export const e = 1;\n")
        _write(self.root, "src/guessed.ts", "export const g = 1;\n")
        _write(
            self.root,
            "src/a.test.ts",
            'jest.mock("./exact.ts");\njest.mock("./guessed");\n',
        )
        by_target = {
            e["to"]: e["props"]["confidence"]
            for e in self._extract().edges
            if (e["props"] or {}).get("resolution") == "mock_patch"
        }
        self.assertEqual(by_target["file:src/exact"], "definite")
        self.assertEqual(by_target["file:src/guessed"], "inferred")

    def test_unresolvable_targets_emit_no_edge(self) -> None:
        """The ymso bar: drop, rather than mint a dangling or guessed edge."""
        _write(
            self.root,
            "src/a.test.ts",
            'jest.mock("axios");\njest.mock("./missing");\n',
        )
        mock_edges = [
            e for e in self._extract().edges
            if (e["props"] or {}).get("resolution") == "mock_patch"
        ]
        self.assertEqual(mock_edges, [])

    def test_repeated_mock_of_one_module_emits_one_edge(self) -> None:
        _write(self.root, "src/dep.ts", "export const d = 1;\n")
        _write(
            self.root,
            "src/a.test.ts",
            'jest.mock("./dep");\nvi.mock("./dep.ts");\n',
        )
        mock_edges = [
            e for e in self._extract().edges
            if (e["props"] or {}).get("resolution") == "mock_patch"
        ]
        self.assertEqual(len(mock_edges), 1)

    def test_python_mock_harvest_still_works_through_the_hook(self) -> None:
        """The table replaced an explicit branch; the filled slot must survive."""
        _write(self.root, "pkg/__init__.py", "")
        _write(self.root, "pkg/target.py", "def thing():\n    return 1\n")
        _write(
            self.root,
            "pkg/target_test.py",
            'from unittest.mock import patch\n'
            'def test_it():\n'
            '    with patch("pkg.target.thing"):\n'
            '        pass\n',
        )
        result = test_peer.extract(self.root, {"glob": "pkg/*_test.py"}, {})
        mock_edges = [
            e for e in result.edges
            if (e["props"] or {}).get("resolution") == "mock_patch"
        ]
        self.assertEqual(len(mock_edges), 1, result.edges)
        self.assertEqual(mock_edges[0]["to"], "symbol:py:pkg.target:thing")


if __name__ == "__main__":
    unittest.main()
