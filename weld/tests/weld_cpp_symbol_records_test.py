"""Tests for ADR 0057 Wave 2 C++ symbol-record extraction.

Covers :mod:`weld.strategies._cpp_symbol_records` directly (the regex
classifier) plus the wiring through
:mod:`weld.strategies._cpp_tree_sitter.enrich_file_node` that stamps
``symbol_records`` onto the file node.

The records distinguish:

  * forward declarations (``int free_add(int, int);``) from
    definitions (``int free_add(int a, int b) { ... }``).
  * class/struct declarations (``class Bar;``) from definitions
    (``class Bar { ... };``).
  * template definitions: ``template: True`` and ``template_signature``
    is the raw parameter list.

The tree-sitter parse layer remains a name-only black box -- tests
mock ``_parse_file_symbols`` with the same dict shape the YAML query
produces.
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


class ExtractSymbolRecordsTest(unittest.TestCase):
    """Unit tests for ``extract_symbol_records``."""

    def test_function_definition_is_classified(self) -> None:
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = textwrap.dedent("""\
            int free_add(int a, int b) {
                return a + b;
            }
        """)
        records = extract_symbol_records(src, ["free_add"])
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["name"], "free_add")
        self.assertEqual(rec["kind"], "definition")
        self.assertFalse(rec["template"])
        self.assertNotIn("template_signature", rec)

    def test_function_declaration_is_classified(self) -> None:
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = textwrap.dedent("""\
            int free_add(int a, int b);
        """)
        records = extract_symbol_records(src, ["free_add"])
        self.assertEqual(records[0]["kind"], "declaration")
        self.assertFalse(records[0]["template"])

    def test_qualified_definition_classified(self) -> None:
        """``void Foo::bar() {}`` -> kind=definition for ``Foo::bar``."""
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = textwrap.dedent("""\
            void Foo::bar() {
                int n = Foo::baz(3);
                (void)n;
            }
        """)
        records = extract_symbol_records(src, ["Foo::bar"])
        self.assertEqual(records[0]["kind"], "definition")

    def test_class_declaration_vs_definition(self) -> None:
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = textwrap.dedent("""\
            class Foo;
            class Bar {
            public:
                int x;
            };
        """)
        records = extract_symbol_records(
            src, ["Foo", "Bar"], classes=["Foo", "Bar"],
        )
        names_to_kinds = {r["name"]: r["kind"] for r in records}
        self.assertEqual(names_to_kinds["Foo"], "declaration")
        self.assertEqual(names_to_kinds["Bar"], "definition")

    def test_template_function_definition_carries_signature(self) -> None:
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = textwrap.dedent("""\
            template <typename T>
            T identity(T value) {
                return value;
            }
        """)
        records = extract_symbol_records(src, ["identity"])
        rec = records[0]
        self.assertEqual(rec["kind"], "definition")
        self.assertTrue(rec["template"])
        self.assertEqual(rec["template_signature"], "typename T")

    def test_template_signature_multi_param(self) -> None:
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = textwrap.dedent("""\
            template <typename T, int N>
            T sum(T (&values)[N]) {
                T acc = T{};
                for (int i = 0; i < N; ++i) acc += values[i];
                return acc;
            }
        """)
        records = extract_symbol_records(src, ["sum"])
        rec = records[0]
        self.assertTrue(rec["template"])
        self.assertEqual(rec["template_signature"], "typename T, int N")

    def test_template_class_definition_carries_signature(self) -> None:
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = textwrap.dedent("""\
            template <typename T>
            class Holder {
            public:
                T value;
            };
        """)
        records = extract_symbol_records(
            src, ["Holder"], classes=["Holder"],
        )
        rec = records[0]
        self.assertEqual(rec["kind"], "definition")
        self.assertTrue(rec["template"])
        self.assertEqual(rec["template_signature"], "typename T")

    def test_no_match_returns_kind_none(self) -> None:
        """Symbols that the classifier cannot locate get ``kind=None``."""
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = "// just a comment\n"
        records = extract_symbol_records(src, ["mystery_func"])
        self.assertIsNone(records[0]["kind"])
        self.assertFalse(records[0]["template"])

    def test_input_order_preserved(self) -> None:
        from weld.strategies._cpp_symbol_records import extract_symbol_records

        src = textwrap.dedent("""\
            int a();
            int b() { return 0; }
        """)
        records = extract_symbol_records(src, ["b", "a"])
        self.assertEqual([r["name"] for r in records], ["b", "a"])
        self.assertEqual(records[0]["kind"], "definition")
        self.assertEqual(records[1]["kind"], "declaration")


class EnrichFileNodeStampsSymbolRecordsTest(unittest.TestCase):
    """``_cpp_tree_sitter.enrich_file_node`` stamps the records prop."""

    def test_records_appear_on_file_node(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "include").mkdir()
            (root / "include" / "foo.h").write_text(
                textwrap.dedent("""\
                    #pragma once
                    namespace app {
                    int free_add(int a, int b);
                    template <typename T>
                    T identity(T value) { return value; }
                    class Foo;
                    class Bar {
                    public:
                        int x;
                    };
                    }
                """)
            )

            symbols = {
                "exports": ["free_add", "identity", "Foo", "Bar"],
                "classes": ["Foo", "Bar"],
                "imports": [],
            }
            with mock.patch.object(
                tree_sitter, "TREE_SITTER_AVAILABLE", True,
            ), mock.patch.object(
                tree_sitter, "_parse_file_symbols", return_value=symbols,
            ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.h", "language": "cpp"},
                    context={},
                )

            file_nodes = [
                n for n in result.nodes.values() if n["type"] == "file"
            ]
            self.assertEqual(len(file_nodes), 1)
            records = file_nodes[0]["props"].get("symbol_records")
            self.assertIsNotNone(records)
            by_name = {r["name"]: r for r in records}

            self.assertEqual(by_name["free_add"]["kind"], "declaration")
            self.assertFalse(by_name["free_add"]["template"])

            self.assertEqual(by_name["identity"]["kind"], "definition")
            self.assertTrue(by_name["identity"]["template"])
            self.assertEqual(
                by_name["identity"]["template_signature"], "typename T",
            )

            self.assertEqual(by_name["Foo"]["kind"], "declaration")
            self.assertEqual(by_name["Bar"]["kind"], "definition")

    def test_no_records_prop_when_no_exports(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "empty.cpp").write_text("// no symbols\n")

            with mock.patch.object(
                tree_sitter, "TREE_SITTER_AVAILABLE", True,
            ), mock.patch.object(
                tree_sitter, "_parse_file_symbols",
                return_value={"exports": [], "classes": [], "imports": []},
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


if __name__ == "__main__":
    unittest.main()
