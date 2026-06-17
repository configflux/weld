"""Unit tests for the Rust trait-impl edge emitter (ADR 0064 criterion 2).

Covers the pure helpers in :mod:`weld.strategies._rust_inherits`:

* :func:`extract_trait_impls` -- regex recognition of ``impl Trait for
  Type`` blocks, including generic parameter lists, qualified trait
  paths, ``where`` clauses, and the rejection of inherent ``impl Type``
  blocks and commented-out impls.
* :func:`build_caches` / :func:`stage_trait_impls` -- the run-wide
  accumulator seam (Rust-only) and per-file record staging.
* :func:`build_project_symbol_index` -- the project Rust symbol index.
* :func:`finalise` -- resolved vs. unresolved trait resolution, the
  drop-when-no-implementing-symbol rule, and ``(from, to)`` dedup.

Strategy-level integration (the full ``wd discover`` -> ``implements``
edge path on the bundled fixture) is asserted by the determinism gate
``tools/tier_check_determinism_rust_gate_test.py``.
"""

from __future__ import annotations

import unittest

from weld.strategies._rust_inherits import (
    build_caches,
    build_project_symbol_index,
    extract_trait_impls,
    finalise,
    stage_trait_impls,
)


def _symbol(module: str, name: str) -> dict:
    return {
        "type": "symbol",
        "label": name,
        "props": {"language": "rust", "module": module},
    }


class ExtractTraitImplsTest(unittest.TestCase):
    def test_simple_trait_impl(self) -> None:
        impls = extract_trait_impls("impl Shape for Circle { }")
        self.assertEqual(impls, [("Circle", "Shape", "Shape")])

    def test_inherent_impl_not_matched(self) -> None:
        # No ``for`` clause -> not a trait-impl -> no edge.
        self.assertEqual(extract_trait_impls("impl Circle { fn n() {} }"), [])

    def test_generic_impl_captures_base_identifiers(self) -> None:
        src = "impl<T> Shape<T> for Circle<T> where T: Clone { }"
        self.assertEqual(extract_trait_impls(src), [("Circle", "Shape", "Shape")])

    def test_qualified_trait_path_short_name_for_lookup(self) -> None:
        impls = extract_trait_impls("impl std::fmt::Debug for Circle { }")
        # short trait name used for resolution; full path retained.
        self.assertEqual(impls, [("Circle", "Debug", "std::fmt::Debug")])

    def test_unsafe_impl_matched(self) -> None:
        self.assertEqual(
            extract_trait_impls("unsafe impl Send for Foo {}"),
            [("Foo", "Send", "Send")],
        )

    def test_multiple_impls_in_source_order(self) -> None:
        src = (
            "impl A for X {}\n"
            "impl B for Y {}\n"
        )
        self.assertEqual(
            extract_trait_impls(src),
            [("X", "A", "A"), ("Y", "B", "B")],
        )

    def test_commented_impl_ignored(self) -> None:
        src = "// impl Hidden for Circle {}\nimpl Shape for Circle {}"
        self.assertEqual(extract_trait_impls(src), [("Circle", "Shape", "Shape")])

    def test_block_commented_impl_ignored(self) -> None:
        src = "/* impl Hidden for Circle {} */\nimpl Shape for Circle {}"
        self.assertEqual(extract_trait_impls(src), [("Circle", "Shape", "Shape")])


class CachesAndStagingTest(unittest.TestCase):
    def test_build_caches_seeds_only_for_rust(self) -> None:
        self.assertEqual(build_caches("rust"), {"impl_records": []})
        self.assertIsNone(build_caches("go"))
        self.assertIsNone(build_caches("python"))

    def test_stage_appends_records_with_module_path(self) -> None:
        records: list = []
        stage_trait_impls(
            records,
            rel_path="src/shapes.rs",
            source_text="impl Shape for Circle {}",
        )
        self.assertEqual(
            records,
            [{
                "module_path": "src.shapes",
                "type_short": "Circle",
                "trait_short": "Shape",
                "trait_full": "Shape",
            }],
        )

    def test_stage_is_noop_when_accumulator_none(self) -> None:
        # Non-Rust languages pass impl_records=None; must not raise.
        stage_trait_impls(None, rel_path="x.go", source_text="impl X for Y {}")


class ProjectSymbolIndexTest(unittest.TestCase):
    def test_indexes_rust_symbols_by_label(self) -> None:
        nodes = {
            "symbol:rust:src.shapes:Shape": _symbol("src.shapes", "Shape"),
            "symbol:rust:src.shapes:Circle": _symbol("src.shapes", "Circle"),
            "file:src/shapes": {"type": "file", "label": "shapes"},
        }
        index = build_project_symbol_index(nodes)
        self.assertEqual(index["Shape"], "symbol:rust:src.shapes:Shape")
        self.assertEqual(index["Circle"], "symbol:rust:src.shapes:Circle")
        self.assertNotIn("shapes", index)

    def test_non_rust_symbols_excluded(self) -> None:
        nodes = {
            "symbol:java:p:Shape": {
                "type": "symbol", "label": "Shape",
                "props": {"language": "java"},
            },
        }
        self.assertEqual(build_project_symbol_index(nodes), {})

    def test_first_declaration_wins(self) -> None:
        nodes = {
            "symbol:rust:a:Dup": _symbol("a", "Dup"),
            "symbol:rust:b:Dup": _symbol("b", "Dup"),
        }
        # Insertion order is preserved by dict; the first id wins.
        self.assertEqual(build_project_symbol_index(nodes)["Dup"], "symbol:rust:a:Dup")


class FinaliseTest(unittest.TestCase):
    def _base_nodes(self) -> dict:
        return {
            "symbol:rust:src.shapes:Shape": _symbol("src.shapes", "Shape"),
            "symbol:rust:src.shapes:Circle": _symbol("src.shapes", "Circle"),
        }

    def test_resolved_implements_edge(self) -> None:
        nodes = self._base_nodes()
        edges: list = []
        caches = {"impl_records": [{
            "module_path": "src.shapes", "type_short": "Circle",
            "trait_short": "Shape", "trait_full": "Shape",
        }]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "symbol:rust:src.shapes:Circle")
        self.assertEqual(edge["to"], "symbol:rust:src.shapes:Shape")
        self.assertEqual(edge["type"], "implements")
        self.assertTrue(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["confidence"], "definite")

    def test_unresolved_trait_mints_sentinel(self) -> None:
        nodes = {"symbol:rust:src.geometry:Rectangle": _symbol("src.geometry", "Rectangle")}
        edges: list = []
        caches = {"impl_records": [{
            "module_path": "src.geometry", "type_short": "Rectangle",
            "trait_short": "Serialize", "trait_full": "serde::Serialize",
        }]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertIn("symbol:unresolved:Serialize", nodes)
        self.assertEqual(edges[0]["to"], "symbol:unresolved:Serialize")
        self.assertFalse(edges[0]["props"]["resolved"])
        self.assertEqual(edges[0]["props"]["confidence"], "speculative")
        # full trait path retained on the edge for provenance.
        self.assertEqual(edges[0]["props"]["trait_name"], "serde::Serialize")

    def test_record_dropped_when_type_symbol_absent(self) -> None:
        # The implementing type was never promoted to a symbol node -> no
        # source to anchor the edge -> drop rather than dangle.
        nodes = {"symbol:rust:src.shapes:Shape": _symbol("src.shapes", "Shape")}
        edges: list = []
        caches = {"impl_records": [{
            "module_path": "src.shapes", "type_short": "Hidden",
            "trait_short": "Shape", "trait_full": "Shape",
        }]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(edges, [])

    def test_duplicate_records_deduped(self) -> None:
        nodes = self._base_nodes()
        edges: list = []
        rec = {
            "module_path": "src.shapes", "type_short": "Circle",
            "trait_short": "Shape", "trait_full": "Shape",
        }
        caches = {"impl_records": [rec, dict(rec)]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len(edges), 1)

    def test_empty_or_missing_caches_noop(self) -> None:
        edges: list = []
        finalise({}, edges, None, "tree_sitter")
        finalise({}, edges, {}, "tree_sitter")
        finalise({}, edges, {"impl_records": []}, "tree_sitter")
        self.assertEqual(edges, [])


if __name__ == "__main__":
    unittest.main()
