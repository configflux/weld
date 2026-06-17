"""Integration tests for ADR 0042 origin tagging in language strategies.

Companion to ``weld_language_origin_test`` (the pure-helper unit
tests). This module exercises the strategy entry points -- the
tree-sitter call-graph extractor, the universal ``tree_sitter``
strategy, the regex-fallback ``typescript_exports`` strategy, and the
C#/Java package-emission helpers -- to confirm that every node minted
through these paths carries ``props.origin``.

The tests do not require a real tree-sitter install; they patch
``tree_sitter`` with a thin mock and feed the extractor synthetic
captures, mirroring the contract verified by
``weld_callgraph_treesitter_test``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock



# ---------------------------------------------------------------------------
# Tree-sitter mock helpers
# ---------------------------------------------------------------------------


class _FakeNode:
    """Minimal stand-in for a tree-sitter capture node."""

    def __init__(self, text: bytes, *, line: int = 1) -> None:
        self.text = text
        self.start_point = (line - 1, 0)


class _FakeQueryCursor:
    """Drives ``matches`` with the captures the test wants to feed back."""

    def __init__(self, captures_per_call: list[dict[str, list[_FakeNode]]]) -> None:
        self._captures_per_call = captures_per_call

    def matches(self, _root):
        for caps in self._captures_per_call:
            yield (0, caps)


def _patched_tree_sitter(
    def_captures: list[_FakeNode], call_captures: list[_FakeNode],
):
    """Build a mock ``tree_sitter`` module that returns the given captures.

    The mock is structured so the first ``QueryCursor`` returns the
    definition captures and the second returns the call captures (the
    ``calls`` query). This mirrors the real call site in
    ``_ts_call_graph.extract_call_edges``.
    """
    module = mock.MagicMock()
    module.Language.return_value = object()
    fake_tree = mock.MagicMock()
    fake_tree.root_node = mock.MagicMock()
    module.Parser.return_value.parse.return_value = fake_tree

    cursors = [
        _FakeQueryCursor([{"name": def_captures}]),
        _FakeQueryCursor([{"name": call_captures}]),
    ]
    cursor_iter = iter(cursors)
    module.QueryCursor.side_effect = lambda _q: next(cursor_iter)
    module.Query.return_value = object()
    return module


# ---------------------------------------------------------------------------
# Call-graph integration: definitions / file-caller / sentinels carry origin
# ---------------------------------------------------------------------------


class CallGraphLanguageOriginTest(unittest.TestCase):
    """``extract_call_edges`` must stamp origin for every node it emits."""

    def _run(
        self,
        language: str,
        def_names: list[str],
        call_names: list[str],
    ) -> tuple[dict[str, dict], list[dict]]:
        from weld.strategies import _ts_call_graph

        ts_mock = _patched_tree_sitter(
            [_FakeNode(n.encode()) for n in def_names],
            [_FakeNode(n.encode()) for n in call_names],
        )
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "example"
            src.write_text("// fixture\n", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"tree_sitter": ts_mock}), \
                 mock.patch.object(
                     _ts_call_graph, "load_ts_language", return_value=object(),
                 ):
                definition_key = "methods" if language == "csharp" else "exports"
                queries = {definition_key: "(_ ) @name", "calls": "(_ ) @name"}
                return _ts_call_graph.extract_call_edges(
                    src, "example", language, queries,
                )

    def _origin_of(self, nodes: dict[str, dict], node_id: str) -> str:
        return nodes[node_id]["props"].get("origin", "<missing>")

    # ---- TypeScript ----

    def test_typescript_definition_is_project(self) -> None:
        nodes, _ = self._run("typescript", ["render"], [])
        self.assertEqual(
            self._origin_of(nodes, "symbol:typescript:example:render"),
            "project",
        )

    def test_typescript_file_caller_is_project(self) -> None:
        nodes, _ = self._run("typescript", [], ["doSomething"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:typescript:example:<file>"),
            "project",
        )

    def test_typescript_builtin_sentinel_is_stdlib(self) -> None:
        nodes, _ = self._run("typescript", [], ["Array"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:unresolved:Array"),
            "stdlib",
        )

    def test_typescript_unknown_sentinel_is_unresolved(self) -> None:
        nodes, _ = self._run("typescript", [], ["myHelper"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:unresolved:myHelper"),
            "unresolved",
        )

    # ---- Rust ----

    def test_rust_qualified_sentinel_is_stdlib(self) -> None:
        nodes, _ = self._run("rust", [], ["std::println"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:unresolved:std::println"),
            "stdlib",
        )

    def test_rust_bare_sentinel_is_unresolved(self) -> None:
        nodes, _ = self._run("rust", [], ["println"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:unresolved:println"),
            "unresolved",
        )

    def test_rust_definition_is_project(self) -> None:
        nodes, _ = self._run("rust", ["compute"], [])
        self.assertEqual(
            self._origin_of(nodes, "symbol:rust:example:compute"),
            "project",
        )

    # ---- Go / Java / C# ----

    def test_go_sentinel_is_unresolved(self) -> None:
        nodes, _ = self._run("go", [], ["Println"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:unresolved:Println"),
            "unresolved",
        )

    def test_go_definition_is_project(self) -> None:
        nodes, _ = self._run("go", ["Handle"], [])
        self.assertEqual(
            self._origin_of(nodes, "symbol:go:example:Handle"),
            "project",
        )

    def test_java_definition_and_sentinel(self) -> None:
        nodes, _ = self._run("java", ["doWork"], ["println"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:java:example:doWork"),
            "project",
        )
        self.assertEqual(
            self._origin_of(nodes, "symbol:unresolved:println"),
            "unresolved",
        )

    def test_csharp_definition_and_sentinel(self) -> None:
        nodes, _ = self._run("csharp", ["Run"], ["WriteLine"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:csharp:example:Run"),
            "project",
        )
        self.assertEqual(
            self._origin_of(nodes, "symbol:unresolved:WriteLine"),
            "unresolved",
        )

    # ---- C++ unchanged: the cpp-specific contract is still intact ----

    def test_cpp_definition_still_project(self) -> None:
        nodes, _ = self._run("cpp", ["solve"], [])
        self.assertEqual(
            self._origin_of(nodes, "symbol:cpp:example:solve"),
            "project",
        )

    def test_cpp_sentinel_still_unresolved(self) -> None:
        nodes, _ = self._run("cpp", [], ["something"])
        self.assertEqual(
            self._origin_of(nodes, "symbol:unresolved:something"),
            "unresolved",
        )


# ---------------------------------------------------------------------------
# tree_sitter.extract: file-type nodes carry origin="project" for every lang
# ---------------------------------------------------------------------------


class TreeSitterFileNodeOriginTest(unittest.TestCase):
    """Project-glob file nodes minted by ``tree_sitter.extract`` are project."""

    def _file_node_for(self, language: str, suffix: str) -> dict:
        from weld.strategies import tree_sitter as ts_strategy

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / f"example.{suffix}"
            src.write_text("// fixture\n", encoding="utf-8")

            with mock.patch.object(ts_strategy, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     ts_strategy,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["foo"],
                         "classes": [],
                         "imports": [],
                     },
                 ):
                result = ts_strategy.extract(
                    root=root,
                    source={
                        "glob": f"*.{suffix}",
                        "language": language,
                    },
                    context={},
                )
        # Find the file-type node (the helper enrichers may also emit
        # synthetic nodes for csharp/java; we want the ``file`` one).
        for node in result.nodes.values():
            if node.get("type") == "file":
                return node
        self.fail(f"no file-type node emitted for {language}")

    def test_typescript_file_node(self) -> None:
        node = self._file_node_for("typescript", "ts")
        self.assertEqual(node["props"].get("origin"), "project")

    def test_go_file_node(self) -> None:
        node = self._file_node_for("go", "go")
        self.assertEqual(node["props"].get("origin"), "project")

    def test_rust_file_node(self) -> None:
        node = self._file_node_for("rust", "rs")
        self.assertEqual(node["props"].get("origin"), "project")

    def test_java_file_node(self) -> None:
        node = self._file_node_for("java", "java")
        self.assertEqual(node["props"].get("origin"), "project")

    def test_csharp_file_node(self) -> None:
        node = self._file_node_for("csharp", "cs")
        self.assertEqual(node["props"].get("origin"), "project")


# ---------------------------------------------------------------------------
# typescript_exports strategy: file nodes carry origin="project"
# ---------------------------------------------------------------------------


class TypescriptExportsOriginTest(unittest.TestCase):
    """File nodes from the regex-fallback path also carry origin."""

    def test_regex_fallback_file_node_is_project(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "module.ts"
            src.write_text(
                "export function helper() {}\n", encoding="utf-8",
            )
            # Force the regex-fallback path so the test does not need
            # the tree-sitter binding.
            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", False,
            ):
                result = typescript_exports.extract(
                    root=root,
                    source={"glob": "*.ts"},
                    context={},
                )
        files = [n for n in result.nodes.values() if n.get("type") == "file"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["props"].get("origin"), "project")


# ---------------------------------------------------------------------------
# Java package nodes default to unresolved; C# stdlib imports classify directly.
# ---------------------------------------------------------------------------


class CsharpJavaPackageOriginTest(unittest.TestCase):
    """Package nodes emitted by the language enrichers carry an origin."""

    def test_csharp_system_package_is_stdlib(self) -> None:
        from weld.strategies import _csharp_tree_sitter

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        node_props: dict = {"file": "Program.cs"}
        symbols: dict[str, list[str]] = {
            "imports": ["System.Linq"],
            "exports": [],
            "classes": [],
        }
        _csharp_tree_sitter.enrich_file_node(
            nodes, edges, "file:program", node_props, symbols, "", "csharp",
        )
        package_nodes = [
            n for n in nodes.values() if n.get("type") == "package"
        ]
        self.assertTrue(package_nodes)
        for n in package_nodes:
            self.assertEqual(n["props"].get("origin"), "stdlib")

    def test_java_jdk_package_is_stdlib(self) -> None:
        from weld.strategies import _java_tree_sitter

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        node_props: dict = {"file": "Foo.java"}
        symbols: dict[str, list[str]] = {
            "imports": ["java.util.List"],
            "exports": [],
            "classes": [],
        }
        _java_tree_sitter.enrich_file_node(
            nodes, edges, "file:foo", node_props, symbols, "", "java",
        )
        package_nodes = [
            n for n in nodes.values() if n.get("type") == "package"
        ]
        self.assertTrue(package_nodes)
        # ADR 0042: ``java.*`` is a JDK stdlib root and is classified
        # without consulting any pom.xml metadata.
        for n in package_nodes:
            self.assertEqual(n["props"].get("origin"), "stdlib")


if __name__ == "__main__":
    unittest.main()
