"""Unit tests for the Go inheritance *fact-extraction* half.

ADR 0064 criterion 2 for Go. Go has no inheritance keyword: its "is-a"
analog is struct *embedding* (an embedded field promotes the embedded
type's fields and methods) and its interface analog is *structural
satisfaction* (a type whose method set includes every method an
interface declares satisfies that interface implicitly -- no
``implements`` keyword). This module is the Go counterpart to
:mod:`weld.strategies._rust_inherits` / :mod:`weld.strategies._typescript_inherits`.

Covered (pure helpers in :mod:`weld.strategies._go_inherits`):

* :func:`extract_file_facts` -- regex recognition of struct embeddings,
  method receivers, and interface method sets from one source file,
  including pointer receivers, qualified embedded types, embedded
  interfaces, commented-out declarations, and the rejection of named
  fields.
* :func:`build_caches` / :func:`stage_file` -- the run-wide accumulator
  seam (Go-only) and per-file record staging.
* :func:`build_project_symbol_index` -- the project Go symbol index.

The edge-*emission* half (:func:`finalise`: embedding -> ``inherits``,
method-set closure -> ``implements``, resolved/unresolved, dedup, and the
ADR 0074 provenance stamp) is covered by the sibling module
:mod:`weld.tests.weld_go_inherits_finalise_test` (split out at bd rifzk to
stay under the line-count cap -- mirrors the production module boundary
between :mod:`weld.strategies._go_inherits_extract` and
:mod:`weld.strategies._go_inherits` itself). The end-to-end ``wd discover``
path (Circle/Rectangle embed shapes.Base -> ``inherits``; satisfy
shapes.Shape structurally -> ``implements``) is asserted on a real graph by
:mod:`weld.tests.weld_go_inherits_discovery_test`.
"""

from __future__ import annotations

import unittest

from weld.strategies._go_inherits import (
    build_caches,
    build_project_symbol_index,
    extract_file_facts,
    stage_file,
)


def _symbol(module: str, name: str) -> dict:
    return {
        "type": "symbol",
        "label": name,
        "props": {"language": "go", "module": module},
    }


class ExtractFileFactsTest(unittest.TestCase):
    def test_struct_embedding_unqualified(self) -> None:
        facts = extract_file_facts("type Circle struct {\n\tBase\n\tR float64\n}")
        # embedded Base, named field R ignored.
        self.assertEqual(facts.embeddings, [("Circle", "Base", "Base")])

    def test_struct_embedding_qualified_short_name(self) -> None:
        src = "type Circle struct {\n\tshapes.Base\n\tR float64\n}"
        # qualified embedded type collapses to its short name for lookup;
        # full form retained for provenance.
        self.assertEqual(
            extract_file_facts(src).embeddings,
            [("Circle", "Base", "shapes.Base")],
        )

    def test_pointer_embedding(self) -> None:
        facts = extract_file_facts("type Node struct {\n\t*Base\n}")
        self.assertEqual(facts.embeddings, [("Node", "Base", "*Base")])

    def test_named_field_not_embedding(self) -> None:
        facts = extract_file_facts("type S struct {\n\tName string\n\tAge int\n}")
        self.assertEqual(facts.embeddings, [])

    def test_method_receiver_value_and_pointer(self) -> None:
        src = (
            "func (c Circle) Area() float64 { return 0 }\n"
            "func (r *Rectangle) Area() float64 { return 0 }\n"
        )
        facts = extract_file_facts(src)
        self.assertEqual(
            sorted(facts.methods),
            [("Circle", "Area"), ("Rectangle", "Area")],
        )

    def test_free_function_not_a_method(self) -> None:
        # A free function has no receiver clause -> contributes no method.
        facts = extract_file_facts("func FormatLabel(name string) string { return name }")
        self.assertEqual(facts.methods, [])

    def test_interface_method_set(self) -> None:
        src = (
            "type Shape interface {\n"
            "\tArea() float64\n"
            "\tDescribe() string\n"
            "}"
        )
        facts = extract_file_facts(src)
        self.assertEqual(facts.interfaces, {"Shape": ({"Area", "Describe"}, set())})

    def test_embedded_interface_in_interface(self) -> None:
        # An interface that embeds another interface lists the embedded
        # name as a required-set contributor, not a method.
        src = "type RW interface {\n\tReader\n\tWrite() int\n}"
        facts = extract_file_facts(src)
        methods, embeds = facts.interfaces["RW"]
        self.assertEqual(methods, {"Write"})
        self.assertEqual(embeds, {"Reader"})

    def test_commented_struct_ignored(self) -> None:
        src = "// type Hidden struct {\n//\tBase\n// }\ntype C struct {\n\tBase\n}"
        self.assertEqual(extract_file_facts(src).embeddings, [("C", "Base", "Base")])

    def test_block_commented_struct_ignored(self) -> None:
        src = "/* type Hidden struct { Base } */\ntype C struct {\n\tBase\n}"
        self.assertEqual(extract_file_facts(src).embeddings, [("C", "Base", "Base")])


class CachesAndStagingTest(unittest.TestCase):
    def test_build_caches_seeds_only_for_go(self) -> None:
        cache = build_caches("go")
        self.assertIsNotNone(cache)
        self.assertEqual(cache, {"go_inherit_records": []})
        self.assertIsNone(build_caches("rust"))
        self.assertIsNone(build_caches("python"))

    def test_stage_appends_record_with_module_path(self) -> None:
        records: list = []
        stage_file(
            records,
            rel_path="geometry/geometry.go",
            source_text="type Circle struct {\n\tshapes.Base\n}\n"
            "func (c Circle) Area() float64 { return 0 }",
        )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["module_path"], "geometry.geometry")
        self.assertEqual(rec["rel_path"], "geometry/geometry.go")
        self.assertEqual(rec["embeddings"], [("Circle", "Base", "shapes.Base")])
        self.assertEqual(rec["methods"], [("Circle", "Area")])

    def test_stage_is_noop_when_accumulator_none(self) -> None:
        # Non-Go languages pass go_inherit_records=None; must not raise.
        stage_file(None, rel_path="x.rs", source_text="struct X {}")


class ProjectSymbolIndexTest(unittest.TestCase):
    def test_indexes_go_symbols_by_label(self) -> None:
        nodes = {
            "symbol:go:shapes.shapes:Shape": _symbol("shapes.shapes", "Shape"),
            "symbol:go:shapes.shapes:Base": _symbol("shapes.shapes", "Base"),
            "file:shapes/shapes": {"type": "file", "label": "shapes"},
        }
        index = build_project_symbol_index(nodes)
        self.assertEqual(index["Shape"], "symbol:go:shapes.shapes:Shape")
        self.assertEqual(index["Base"], "symbol:go:shapes.shapes:Base")
        self.assertNotIn("shapes", index)

    def test_non_go_symbols_excluded(self) -> None:
        nodes = {
            "symbol:rust:p:Shape": {
                "type": "symbol", "label": "Shape",
                "props": {"language": "rust"},
            },
        }
        self.assertEqual(build_project_symbol_index(nodes), {})


if __name__ == "__main__":
    unittest.main()
