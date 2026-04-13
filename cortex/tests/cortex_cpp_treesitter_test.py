"""Tests for C/C++ language support in the tree-sitter strategy.

Tree-sitter is an optional dependency that is not installed in the
Bazel sandbox, so this test exercises the strategy via mocking the
per-file symbol parser. The goal is to assert the contract surface
that layer 1 promises:

  * ``cpp.yaml`` loads with the expected query keys
  * The ``calls`` query exists (so ``_extract_call_edges`` is wired)
  * Queries cover the C++ shapes the task description promises
  * Extraction produces ``file`` nodes for cpp sources
  * ``emit_calls: true`` produces ``symbol`` nodes + ``calls`` edges
    via the existing tree_sitter call graph machinery
  * The cpp grammar entry is documented in ``_INSTALL_MSG``
  * ``init_detect.EXT_TO_LANG`` recognises the C/C++ extensions
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

class CppQueryFileLoadingTest(unittest.TestCase):
    """The bundled cpp.yaml must be loadable and contain expected queries."""

    def test_load_bundled_cpp_queries(self) -> None:
        from cortex.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("cpp")
        self.assertIn("exports", queries)
        self.assertIn("classes", queries)
        self.assertIn("imports", queries)
        self.assertIn("calls", queries)

    def test_cpp_queries_are_nonempty_strings(self) -> None:
        from cortex.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("cpp")
        for name, query_str in queries.items():
            self.assertIsInstance(
                query_str, str, f"Query {name} should be a string"
            )
            self.assertTrue(
                len(query_str.strip()) > 0,
                f"Query {name} should not be empty",
            )

    def test_cpp_exports_query_targets_promised_shapes(self) -> None:
        """The exports query must cover the shapes the task promises."""
        from cortex.strategies.tree_sitter import load_language_queries

        exports = load_language_queries("cpp")["exports"]
        # function_definition (free + in-class definitions)
        self.assertIn("function_definition", exports)
        # in-class declarations via field_declaration
        self.assertIn("field_declaration", exports)
        # free declarations with function_declarator
        self.assertIn("declaration", exports)
        self.assertIn("function_declarator", exports)
        # class/struct/namespace
        self.assertIn("class_specifier", exports)
        self.assertIn("struct_specifier", exports)
        self.assertIn("namespace_definition", exports)
        # qualified_identifier captured whole so Foo::bar stays joined
        self.assertIn("qualified_identifier", exports)

    def test_cpp_classes_query_covers_class_and_struct(self) -> None:
        from cortex.strategies.tree_sitter import load_language_queries

        classes = load_language_queries("cpp")["classes"]
        self.assertIn("class_specifier", classes)
        self.assertIn("struct_specifier", classes)

    def test_cpp_imports_query_only_string_literal(self) -> None:
        """Only ``#include "foo.h"`` form is captured; system includes are
        not part of the query so they cannot match by accident."""
        from cortex.strategies.tree_sitter import load_language_queries

        imports = load_language_queries("cpp")["imports"]
        self.assertIn("preproc_include", imports)
        self.assertIn("string_literal", imports)
        # Must not capture <system> includes via system_lib_string.
        self.assertNotIn("system_lib_string", imports)

    def test_cpp_calls_query_captures_qualified_callees(self) -> None:
        from cortex.strategies.tree_sitter import load_language_queries

        calls = load_language_queries("cpp")["calls"]
        self.assertIn("call_expression", calls)
        # plain identifier callee
        self.assertIn("identifier", calls)
        # field_expression for a.b()
        self.assertIn("field_expression", calls)
        # qualified_identifier whole-capture for Foo::bar()
        self.assertIn("qualified_identifier", calls)

class CppExtractWithMockedTreeSitterTest(unittest.TestCase):
    """Test C++ extraction with mocked tree-sitter internals."""

    def _make_cpp_tree(self, tmp: str) -> Path:
        root = Path(tmp)
        src = root / "src"
        inc = root / "include"
        src.mkdir()
        inc.mkdir()
        (inc / "foo.h").write_text(
            textwrap.dedent("""\
                #pragma once
                namespace app {
                class Foo {
                public:
                    void bar();
                    static int baz(int x);
                };
                int free_add(int a, int b);
                }
            """)
        )
        (src / "foo.cpp").write_text(
            textwrap.dedent("""\
                #include "foo.h"
                namespace app {
                void Foo::bar() {
                    int n = Foo::baz(3);
                    (void)n;
                }
                int Foo::baz(int x) { return x + 1; }
                int free_add(int a, int b) { return Foo::baz(a) + b; }
                }
            """)
        )
        return root

    def test_extract_produces_file_nodes_for_cpp_sources(self) -> None:
        from cortex.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_cpp_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Foo", "Foo::bar", "Foo::baz", "free_add"],
                         "classes": ["Foo"],
                         "imports": ["\"foo.h\""],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={
                        "glob": "**/*.cpp",
                        "language": "cpp",
                    },
                    context={},
                )
            self.assertTrue(
                len(result.nodes) > 0, "Should produce at least one file node"
            )
            file_nodes = [
                n for n in result.nodes.values() if n["type"] == "file"
            ]
            self.assertTrue(file_nodes, "Should emit a file node for cpp")
            node = file_nodes[0]
            self.assertIn("exports", node["props"])
            self.assertIn("Foo::bar", node["props"]["exports"])
            self.assertIn("Foo::baz", node["props"]["exports"])
            self.assertEqual(node["props"]["source_strategy"], "tree_sitter")
            self.assertEqual(node["props"]["confidence"], "definite")
            self.assertIn("implementation", node["props"]["roles"])

    def test_extract_includes_class_types_and_imports(self) -> None:
        from cortex.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_cpp_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Foo"],
                         "classes": ["Foo"],
                         "imports": ["\"foo.h\""],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.cpp", "language": "cpp"},
                    context={},
                )
            node = next(
                n for n in result.nodes.values() if n["type"] == "file"
            )
            self.assertIn("types", node["props"])
            self.assertIn("Foo", node["props"]["types"])
            self.assertIn("imports_from", node["props"])

    def test_emit_calls_produces_symbol_nodes_and_call_edges(self) -> None:
        """When ``emit_calls: true`` is set, the call-graph helper runs."""
        from cortex.strategies import tree_sitter

        def fake_extract(file_path, rel_path, language, queries):
            module = "src.foo"
            return (
                {
                    f"symbol:{language}:{module}:Foo::bar": {
                        "type": "symbol",
                        "label": "Foo::bar",
                        "props": {
                            "qualname": "Foo::bar",
                            "language": language,
                            "source_strategy": "tree_sitter",
                        },
                    },
                    "symbol:unresolved:Foo::baz": {
                        "type": "symbol",
                        "label": "Foo::baz",
                        "props": {
                            "qualname": "Foo::baz",
                            "language": language,
                            "resolved": False,
                            "source_strategy": "tree_sitter",
                        },
                    },
                },
                [
                    {
                        "from": f"symbol:{language}:{module}:<file>",
                        "to": "symbol:unresolved:Foo::baz",
                        "type": "calls",
                        "props": {
                            "source_strategy": "tree_sitter",
                            "resolved": False,
                            "confidence": "speculative",
                        },
                    }
                ],
            )

        with tempfile.TemporaryDirectory() as td:
            root = self._make_cpp_tree(td)

            with mock.patch.object(
                tree_sitter, "TREE_SITTER_AVAILABLE", True
            ), mock.patch.object(
                tree_sitter,
                "_parse_file_symbols",
                return_value={"exports": ["Foo::bar"], "classes": [], "imports": []},
            ), mock.patch.object(
                tree_sitter, "_extract_call_edges", side_effect=fake_extract
            ) as cg_mock:
                result = tree_sitter.extract(
                    root=root,
                    source={
                        "glob": "**/*.cpp",
                        "language": "cpp",
                        "emit_calls": True,
                    },
                    context={},
                )

            # The call-graph helper must have been invoked.
            self.assertTrue(cg_mock.called)
            symbol_nodes = [
                n for n in result.nodes.values() if n["type"] == "symbol"
            ]
            self.assertTrue(symbol_nodes, "expected symbol nodes from cpp")
            self.assertIn("symbol:unresolved:Foo::baz", result.nodes)
            calls = [e for e in result.edges if e["type"] == "calls"]
            self.assertTrue(calls, "expected at least one calls edge")
            self.assertEqual(calls[0]["to"], "symbol:unresolved:Foo::baz")

    def test_no_exports_skips_cpp_file(self) -> None:
        from cortex.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "empty.cpp").write_text("// no top-level definitions\n")

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": [],
                         "classes": [],
                         "imports": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.cpp", "language": "cpp"},
                    context={},
                )
            file_nodes = [
                n for n in result.nodes.values() if n["type"] == "file"
            ]
            self.assertEqual(file_nodes, [])

class CppInstallMessageTest(unittest.TestCase):
    """The install hint must mention the cpp grammar package."""

    def test_install_msg_mentions_tree_sitter_cpp(self) -> None:
        from cortex.strategies import tree_sitter

        self.assertIn("tree-sitter-cpp", tree_sitter._INSTALL_MSG)

class CppExtensionDetectionTest(unittest.TestCase):
    """init_detect must recognise the C/C++ extensions as ``cpp``."""

    def test_ext_to_lang_covers_c_and_cpp_extensions(self) -> None:
        from cortex.init_detect import EXT_TO_LANG

        for ext in (
            ".c", ".cc", ".cpp", ".cxx",
            ".h", ".hpp", ".hh", ".hxx", ".ipp", ".tpp",
        ):
            self.assertEqual(
                EXT_TO_LANG.get(ext),
                "cpp",
                f"{ext} should map to cpp",
            )

class CppFixtureCoverageTest(unittest.TestCase):
    """The cpp_clang fixture must cover all shapes promised by the task."""

    FIXTURE = (
        Path(__file__).resolve().parent / "fixtures" / "cpp_clang"
    )

    def test_fixture_has_namespace_and_class(self) -> None:
        text = (self.FIXTURE / "include" / "app.h").read_text()
        self.assertIn("namespace app", text)
        self.assertIn("class ItemStore", text)

    def test_fixture_has_out_of_class_method_definition(self) -> None:
        text = (self.FIXTURE / "src" / "foo.cpp").read_text()
        self.assertIn("void Foo::bar()", text)

    def test_fixture_has_header_impl_pair(self) -> None:
        self.assertTrue((self.FIXTURE / "include" / "foo.h").exists())
        self.assertTrue((self.FIXTURE / "src" / "foo.cpp").exists())

    def test_fixture_has_inline_header_function(self) -> None:
        text = (self.FIXTURE / "include" / "foo.h").read_text()
        self.assertIn("inline", text)

    def test_fixture_has_template_function(self) -> None:
        text = (self.FIXTURE / "include" / "foo.h").read_text()
        self.assertIn("template", text)

    def test_fixture_has_static_method_qualified_call(self) -> None:
        # Bar::baz() qualified call must appear at a call site.
        main_text = (self.FIXTURE / "src" / "main.cpp").read_text()
        self.assertIn("Bar::baz()", main_text)

    def test_fixture_has_free_function_with_calls(self) -> None:
        text = (self.FIXTURE / "src" / "foo.cpp").read_text()
        self.assertIn("free_add", text)
        # Free function body must contain at least one call.
        self.assertIn("Foo::baz", text)

if __name__ == "__main__":
    unittest.main()
