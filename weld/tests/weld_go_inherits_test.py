"""Unit + integration tests for the Go inheritance edge emitter.

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
* :func:`finalise` -- embedding -> ``inherits`` edges, method-set
  closure -> ``implements`` edges, resolved vs. unresolved targets, the
  drop-when-no-declaring-symbol rule, and dedup.

The end-to-end ``wd discover`` path (Circle/Rectangle embed shapes.Base ->
``inherits``; satisfy shapes.Shape structurally -> ``implements``) is
asserted on a real graph by the sibling module
:mod:`weld.tests.weld_go_inherits_discovery_test`.
"""

from __future__ import annotations

import unittest

from weld.strategies._go_inherits import (
    build_caches,
    build_project_symbol_index,
    extract_file_facts,
    finalise,
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


class FinaliseInheritsTest(unittest.TestCase):
    def _embedding_record(self, module: str, struct: str, base_short: str,
                          base_full: str) -> dict:
        return {
            "module_path": module,
            "embeddings": [(struct, base_short, base_full)],
            "methods": [],
            "interfaces": {},
        }

    def test_resolved_inherits_edge_cross_module(self) -> None:
        # Circle (geometry) embeds Base (shapes) -> cross-package inherits.
        nodes = {
            "symbol:go:geometry.geometry:Circle": _symbol("geometry.geometry", "Circle"),
            "symbol:go:shapes.shapes:Base": _symbol("shapes.shapes", "Base"),
        }
        edges: list = []
        caches = {"go_inherit_records": [
            self._embedding_record("geometry.geometry", "Circle", "Base", "shapes.Base"),
        ]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "symbol:go:geometry.geometry:Circle")
        self.assertEqual(edge["to"], "symbol:go:shapes.shapes:Base")
        self.assertEqual(edge["type"], "inherits")
        self.assertTrue(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["confidence"], "definite")
        self.assertEqual(edge["props"]["base_name"], "shapes.Base")

    def test_unresolved_embedded_base_mints_sentinel(self) -> None:
        nodes = {"symbol:go:p.p:T": _symbol("p.p", "T")}
        edges: list = []
        caches = {"go_inherit_records": [
            self._embedding_record("p.p", "T", "Mutex", "sync.Mutex"),
        ]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertIn("symbol:unresolved:Mutex", nodes)
        self.assertEqual(edges[0]["to"], "symbol:unresolved:Mutex")
        self.assertFalse(edges[0]["props"]["resolved"])
        self.assertEqual(edges[0]["props"]["confidence"], "speculative")

    def test_record_dropped_when_struct_symbol_absent(self) -> None:
        nodes = {"symbol:go:shapes.shapes:Base": _symbol("shapes.shapes", "Base")}
        edges: list = []
        caches = {"go_inherit_records": [
            self._embedding_record("p.p", "Hidden", "Base", "Base"),
        ]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(edges, [])

    def test_duplicate_embeddings_deduped(self) -> None:
        nodes = {
            "symbol:go:p.p:T": _symbol("p.p", "T"),
            "symbol:go:p.p:Base": _symbol("p.p", "Base"),
        }
        edges: list = []
        rec = self._embedding_record("p.p", "T", "Base", "Base")
        caches = {"go_inherit_records": [rec, dict(rec)]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len([e for e in edges if e["type"] == "inherits"]), 1)


class FinaliseImplementsTest(unittest.TestCase):
    def _nodes(self) -> dict:
        return {
            "symbol:go:geometry.geometry:Circle": _symbol("geometry.geometry", "Circle"),
            "symbol:go:geometry.geometry:Rectangle": _symbol("geometry.geometry", "Rectangle"),
            "symbol:go:shapes.shapes:Base": _symbol("shapes.shapes", "Base"),
            "symbol:go:shapes.shapes:Shape": _symbol("shapes.shapes", "Shape"),
        }

    def _fixture_records(self) -> list[dict]:
        # geometry.go: Circle/Rectangle embed shapes.Base, define Area.
        # shapes.go: Base defines Describe; Shape requires Area+Describe.
        return [
            {
                "module_path": "geometry.geometry",
                "embeddings": [("Circle", "Base", "shapes.Base"),
                               ("Rectangle", "Base", "shapes.Base")],
                "methods": [("Circle", "Area"), ("Rectangle", "Area")],
                "interfaces": {},
            },
            {
                "module_path": "shapes.shapes",
                "embeddings": [],
                "methods": [("Base", "Describe")],
                "interfaces": {"Shape": ({"Area", "Describe"}, set())},
            },
        ]

    def test_implements_via_promoted_embedded_method(self) -> None:
        nodes = self._nodes()
        edges: list = []
        finalise(nodes, edges, {"go_inherit_records": self._fixture_records()},
                 "tree_sitter")
        implements = {
            (e["from"], e["to"]) for e in edges if e["type"] == "implements"
        }
        # Circle has own Area + promoted Describe (from embedded Base) ->
        # satisfies Shape{Area,Describe}. Same for Rectangle.
        self.assertIn(
            ("symbol:go:geometry.geometry:Circle", "symbol:go:shapes.shapes:Shape"),
            implements,
        )
        self.assertIn(
            ("symbol:go:geometry.geometry:Rectangle", "symbol:go:shapes.shapes:Shape"),
            implements,
        )
        for edge in edges:
            if edge["type"] == "implements":
                self.assertTrue(edge["props"]["resolved"])
                self.assertEqual(edge["props"]["confidence"], "definite")

    def test_implements_origin_resolved_via_index_not_record_module(self) -> None:
        # Go packages span multiple files, each with its own per-file
        # module (``geometry.geometry`` vs. ``geometry.helper``). A type
        # declared in one file may have a method in a sibling file. The
        # implementing-type origin must resolve to the *declared* symbol
        # (via the project index), not be reconstructed from the method
        # record's module -- otherwise the implements edge is dropped.
        nodes = {
            # Circle is declared in geometry.go -> this symbol id.
            "symbol:go:geometry.geometry:Circle": _symbol("geometry.geometry", "Circle"),
            "symbol:go:shapes.shapes:Shape": _symbol("shapes.shapes", "Shape"),
        }
        edges: list = []
        records = [
            # Methods of Circle live in a sibling file (different module).
            {
                "module_path": "geometry.helper",
                "embeddings": [],
                "methods": [("Circle", "Area"), ("Circle", "Describe")],
                "interfaces": {},
            },
            {
                "module_path": "shapes.shapes",
                "embeddings": [],
                "methods": [],
                "interfaces": {"Shape": ({"Area", "Describe"}, set())},
            },
        ]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        implements = {(e["from"], e["to"]) for e in edges if e["type"] == "implements"}
        self.assertIn(
            ("symbol:go:geometry.geometry:Circle", "symbol:go:shapes.shapes:Shape"),
            implements,
        )

    def test_no_implements_when_method_set_incomplete(self) -> None:
        # A struct missing one interface method does not implement it.
        nodes = {
            "symbol:go:p.p:Partial": _symbol("p.p", "Partial"),
            "symbol:go:p.p:Iface": _symbol("p.p", "Iface"),
        }
        edges: list = []
        records = [{
            "module_path": "p.p",
            "embeddings": [],
            "methods": [("Partial", "Area")],  # missing Describe
            "interfaces": {"Iface": ({"Area", "Describe"}, set())},
        }]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        self.assertEqual([e for e in edges if e["type"] == "implements"], [])

    def test_interface_does_not_implement_itself(self) -> None:
        # The interface's own symbol must not get an implements self-edge,
        # nor should an empty interface match every struct.
        nodes = {
            "symbol:go:p.p:T": _symbol("p.p", "T"),
            "symbol:go:p.p:Shape": _symbol("p.p", "Shape"),
        }
        edges: list = []
        records = [{
            "module_path": "p.p",
            "embeddings": [],
            "methods": [("T", "Area"), ("T", "Describe")],
            "interfaces": {"Shape": ({"Area", "Describe"}, set())},
        }]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        implements = {(e["from"], e["to"]) for e in edges if e["type"] == "implements"}
        self.assertNotIn(
            ("symbol:go:p.p:Shape", "symbol:go:p.p:Shape"), implements
        )
        self.assertIn(("symbol:go:p.p:T", "symbol:go:p.p:Shape"), implements)

    def test_empty_interface_does_not_match_all(self) -> None:
        # An interface with no required methods must not implements-edge
        # every struct (that would be noise, not signal).
        nodes = {
            "symbol:go:p.p:T": _symbol("p.p", "T"),
            "symbol:go:p.p:Empty": _symbol("p.p", "Empty"),
        }
        edges: list = []
        records = [{
            "module_path": "p.p",
            "embeddings": [],
            "methods": [("T", "Area")],
            "interfaces": {"Empty": (set(), set())},
        }]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        self.assertEqual([e for e in edges if e["type"] == "implements"], [])

    def test_empty_or_missing_caches_noop(self) -> None:
        edges: list = []
        finalise({}, edges, None, "tree_sitter")
        finalise({}, edges, {}, "tree_sitter")
        finalise({}, edges, {"go_inherit_records": []}, "tree_sitter")
        self.assertEqual(edges, [])


if __name__ == "__main__":
    unittest.main()
