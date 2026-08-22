"""Unit tests for the TypeScript inheritance edge emitter (ADR 0064 criterion 2).

Covers the pure helpers in :mod:`weld.strategies._typescript_inherits`:

* :func:`extract_inheritance` -- regex recognition of ``class`` /
  ``interface`` declaration headers, the ``inherits`` (``extends``) vs.
  ``implements`` edge-type split, multi-base clauses, generic argument
  lists on the declaration and on each base, qualified base paths, and
  the rejection of commented-out declarations.
* :func:`build_caches` / :func:`stage_inheritance` -- the run-wide
  accumulator seam (TypeScript-only) and per-file record staging.
* :func:`build_project_symbol_index` -- the project TypeScript symbol
  index.
* :func:`finalise` -- resolved vs. unresolved base resolution, the
  drop-when-no-declaring-symbol rule, and ``(from, to, type)`` dedup.

Strategy-level integration (the full ``wd discover`` -> ``inherits`` /
``implements`` edge path on the bundled fixture) is asserted by the
determinism gate ``tools/tier_check_determinism_typescript_gate_test.py``.
"""

from __future__ import annotations

import unittest

from weld.strategies._typescript_inherits import (
    build_caches,
    build_project_symbol_index,
    extract_inheritance,
    finalise,
    stage_inheritance,
)


def _symbol(module: str, name: str) -> dict:
    return {
        "type": "symbol",
        "label": name,
        "props": {"language": "typescript", "module": module},
    }


class ExtractInheritanceTest(unittest.TestCase):
    def test_class_extends_and_implements(self) -> None:
        src = "export class Rectangle extends Base implements Shape { }"
        self.assertEqual(
            extract_inheritance(src),
            [
                ("Rectangle", "Base", "Base", "inherits"),
                ("Rectangle", "Shape", "Shape", "implements"),
            ],
        )

    def test_class_implements_only(self) -> None:
        self.assertEqual(
            extract_inheritance("export class Circle implements Shape { }"),
            [("Circle", "Shape", "Shape", "implements")],
        )

    def test_class_extends_only(self) -> None:
        self.assertEqual(
            extract_inheritance("class Square extends Rectangle { }"),
            [("Square", "Rectangle", "Rectangle", "inherits")],
        )

    def test_plain_class_no_edges(self) -> None:
        self.assertEqual(extract_inheritance("export class Plain { }"), [])

    def test_function_not_matched(self) -> None:
        self.assertEqual(extract_inheritance("export function f() {}"), [])

    def test_multiple_implements_in_source_order(self) -> None:
        self.assertEqual(
            extract_inheritance("class C implements A, B { }"),
            [("C", "A", "A", "implements"), ("C", "B", "B", "implements")],
        )

    def test_interface_extends_multiple(self) -> None:
        self.assertEqual(
            extract_inheritance("export interface I extends J, K { }"),
            [("I", "J", "J", "inherits"), ("I", "K", "K", "inherits")],
        )

    def test_generic_args_on_decl_and_base(self) -> None:
        src = "class Box<T> extends Container<T> implements Iface<T, U> { }"
        self.assertEqual(
            extract_inheritance(src),
            [
                ("Box", "Container", "Container", "inherits"),
                ("Box", "Iface", "Iface", "implements"),
            ],
        )

    def test_qualified_base_short_name_for_lookup(self) -> None:
        # short base name used for resolution; full path retained.
        self.assertEqual(
            extract_inheritance("class C implements ns.Shape { }"),
            [("C", "Shape", "ns.Shape", "implements")],
        )

    def test_line_commented_decl_ignored(self) -> None:
        src = "// class Hidden extends Base {}\nclass C extends Base {}"
        self.assertEqual(
            extract_inheritance(src), [("C", "Base", "Base", "inherits")]
        )

    def test_block_commented_decl_ignored(self) -> None:
        src = "/* class Hidden extends Base {} */\nclass C extends Base {}"
        self.assertEqual(
            extract_inheritance(src), [("C", "Base", "Base", "inherits")]
        )

    def test_abstract_class_matched(self) -> None:
        # ``abstract`` precedes the ``class`` keyword anchor.
        self.assertEqual(
            extract_inheritance("export abstract class A extends B { }"),
            [("A", "B", "B", "inherits")],
        )


class CachesAndStagingTest(unittest.TestCase):
    def test_build_caches_seeds_only_for_typescript(self) -> None:
        self.assertEqual(build_caches("typescript"), {"inherit_records": []})
        self.assertIsNone(build_caches("javascript"))
        self.assertIsNone(build_caches("tsx"))
        self.assertIsNone(build_caches("go"))

    def test_stage_appends_records_with_module_path(self) -> None:
        records: list = []
        stage_inheritance(
            records,
            rel_path="src/geometry.ts",
            source_text="class Rectangle extends Base implements Shape {}",
        )
        self.assertEqual(
            records,
            [
                {
                    "module_path": "src.geometry",
                    "rel_path": "src/geometry.ts",
                    "decl_short": "Rectangle",
                    "base_short": "Base",
                    "base_full": "Base",
                    "edge_type": "inherits",
                },
                {
                    "module_path": "src.geometry",
                    "rel_path": "src/geometry.ts",
                    "decl_short": "Rectangle",
                    "base_short": "Shape",
                    "base_full": "Shape",
                    "edge_type": "implements",
                },
            ],
        )

    def test_stage_is_noop_when_accumulator_none(self) -> None:
        # Non-TS languages pass inherit_records=None; must not raise.
        stage_inheritance(None, rel_path="x.go", source_text="class X {}")


class ProjectSymbolIndexTest(unittest.TestCase):
    def test_indexes_typescript_symbols_by_label(self) -> None:
        nodes = {
            "symbol:typescript:src.shapes:Shape": _symbol("src.shapes", "Shape"),
            "symbol:typescript:src.shapes:Circle": _symbol("src.shapes", "Circle"),
            "file:src/shapes": {"type": "file", "label": "shapes"},
        }
        index = build_project_symbol_index(nodes)
        self.assertEqual(index["Shape"], "symbol:typescript:src.shapes:Shape")
        self.assertEqual(index["Circle"], "symbol:typescript:src.shapes:Circle")
        self.assertNotIn("shapes", index)

    def test_non_typescript_symbols_excluded(self) -> None:
        nodes = {
            "symbol:java:p:Shape": {
                "type": "symbol",
                "label": "Shape",
                "props": {"language": "java"},
            },
        }
        self.assertEqual(build_project_symbol_index(nodes), {})

    def test_first_declaration_wins(self) -> None:
        nodes = {
            "symbol:typescript:a:Dup": _symbol("a", "Dup"),
            "symbol:typescript:b:Dup": _symbol("b", "Dup"),
        }
        self.assertEqual(
            build_project_symbol_index(nodes)["Dup"], "symbol:typescript:a:Dup"
        )


class FinaliseTest(unittest.TestCase):
    def _base_nodes(self) -> dict:
        return {
            "symbol:typescript:src.shapes:Shape": _symbol("src.shapes", "Shape"),
            "symbol:typescript:src.shapes:Circle": _symbol("src.shapes", "Circle"),
            "symbol:typescript:src.geometry:Base": _symbol("src.geometry", "Base"),
            "symbol:typescript:src.geometry:Rectangle": _symbol(
                "src.geometry", "Rectangle"
            ),
        }

    def test_resolved_implements_edge(self) -> None:
        nodes = self._base_nodes()
        edges: list = []
        caches = {
            "inherit_records": [
                {
                    "module_path": "src.shapes",
                    "decl_short": "Circle",
                    "base_short": "Shape",
                    "base_full": "Shape",
                    "edge_type": "implements",
                }
            ]
        }
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "symbol:typescript:src.shapes:Circle")
        self.assertEqual(edge["to"], "symbol:typescript:src.shapes:Shape")
        self.assertEqual(edge["type"], "implements")
        self.assertTrue(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["confidence"], "definite")

    def test_resolved_inherits_edge(self) -> None:
        nodes = self._base_nodes()
        edges: list = []
        caches = {
            "inherit_records": [
                {
                    "module_path": "src.geometry",
                    "rel_path": "src/geometry.ts",
                    "decl_short": "Rectangle",
                    "base_short": "Base",
                    "base_full": "Base",
                    "edge_type": "inherits",
                }
            ]
        }
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["type"], "inherits")
        self.assertEqual(edges[0]["to"], "symbol:typescript:src.geometry:Base")
        self.assertTrue(edges[0]["props"]["resolved"])
        # ADR 0074 / bd rifzk: attributed to the file whose extends/
        # implements clause produced the edge.
        self.assertEqual(
            edges[0]["props"]["provenance"], {"file": "src/geometry.ts"},
        )

    def test_unresolved_base_mints_sentinel(self) -> None:
        nodes = {
            "symbol:typescript:src.geometry:Rectangle": _symbol(
                "src.geometry", "Rectangle"
            )
        }
        edges: list = []
        caches = {
            "inherit_records": [
                {
                    "module_path": "src.geometry",
                    "decl_short": "Rectangle",
                    "base_short": "External",
                    "base_full": "pkg.External",
                    "edge_type": "implements",
                }
            ]
        }
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertIn("symbol:unresolved:External", nodes)
        self.assertEqual(edges[0]["to"], "symbol:unresolved:External")
        self.assertFalse(edges[0]["props"]["resolved"])
        self.assertEqual(edges[0]["props"]["confidence"], "speculative")
        # full base path retained on the edge for provenance.
        self.assertEqual(edges[0]["props"]["base_name"], "pkg.External")

    def test_record_dropped_when_decl_symbol_absent(self) -> None:
        # The declaring type was never promoted to a symbol node -> no
        # source to anchor the edge -> drop rather than dangle.
        nodes = {
            "symbol:typescript:src.shapes:Shape": _symbol("src.shapes", "Shape")
        }
        edges: list = []
        caches = {
            "inherit_records": [
                {
                    "module_path": "src.shapes",
                    "decl_short": "Hidden",
                    "base_short": "Shape",
                    "base_full": "Shape",
                    "edge_type": "implements",
                }
            ]
        }
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(edges, [])

    def test_duplicate_records_deduped(self) -> None:
        nodes = self._base_nodes()
        edges: list = []
        rec = {
            "module_path": "src.shapes",
            "decl_short": "Circle",
            "base_short": "Shape",
            "base_full": "Shape",
            "edge_type": "implements",
        }
        caches = {"inherit_records": [rec, dict(rec)]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len(edges), 1)

    def test_same_pair_different_edge_type_both_kept(self) -> None:
        # A class that both extends and implements the *same* short name
        # (pathological but possible) keeps both edges -- the dedup key
        # includes the edge type.
        nodes = self._base_nodes()
        edges: list = []
        caches = {
            "inherit_records": [
                {
                    "module_path": "src.shapes",
                    "decl_short": "Circle",
                    "base_short": "Shape",
                    "base_full": "Shape",
                    "edge_type": "inherits",
                },
                {
                    "module_path": "src.shapes",
                    "decl_short": "Circle",
                    "base_short": "Shape",
                    "base_full": "Shape",
                    "edge_type": "implements",
                },
            ]
        }
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(
            sorted(e["type"] for e in edges), ["implements", "inherits"]
        )

    def test_empty_or_missing_caches_noop(self) -> None:
        edges: list = []
        finalise({}, edges, None, "tree_sitter")
        finalise({}, edges, {}, "tree_sitter")
        finalise({}, edges, {"inherit_records": []}, "tree_sitter")
        self.assertEqual(edges, [])


if __name__ == "__main__":
    unittest.main()
