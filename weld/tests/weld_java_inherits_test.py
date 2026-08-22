"""ADR 0064 criterion 2 coverage for the java tree-sitter strategy.

Companion to :mod:`weld.tests.weld_csharp_inheritance_test`. The
treesitter-level Java gate test (``tools/tier_check_java_gate_test``)
verifies end-to-end edge emission against the bundled fixture; this
module pins the lower-level helpers (regex pair extraction,
``extends``/``implements`` split, project-index resolution) in
isolation so a regression there gets a narrow failure trace.
"""

from __future__ import annotations

import unittest


from weld.strategies._java_inherits import (  # noqa: E402
    build_project_class_index,
    emit_inheritance_edges,
    extract_class_inheritance,
    record_inheritance,
)


class ExtractClassInheritanceTest(unittest.TestCase):
    """Regex extractor returns one (derived, base, edge_type) triple per base."""

    def test_extends_clause_emits_inherits(self) -> None:
        source = """
            package com.example.sample;
            public class Circle extends Shape {
                public double area() { return 0; }
            }
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Circle", "Shape", "inherits")])

    def test_implements_clause_emits_implements(self) -> None:
        source = """
            package com.example.sample;
            public class Square implements Drawable {
                public String describe() { return "square"; }
            }
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Square", "Drawable", "implements")])

    def test_extends_and_implements_combined(self) -> None:
        """Generic-arg tails are stripped at extraction time.

        The base name captured is the bare dotted form (without the
        ``<...>`` tail) so project-index lookup uses the short name.
        """
        source = """
            package com.example.sample;
            public class Circle extends Shape implements Drawable, Comparable<Circle> {
                public double area() { return 0; }
            }
        """
        records = extract_class_inheritance(source)
        self.assertEqual(
            records,
            [
                ("Circle", "Shape", "inherits"),
                ("Circle", "Drawable", "implements"),
                ("Circle", "Comparable", "implements"),
            ],
        )

    def test_interface_extends_other_interfaces(self) -> None:
        """``interface extends`` lists ARE captured as ``inherits``.

        Java interfaces extend interfaces (multiple); the ADR 0064
        criterion-2 contract treats interface-to-interface edges as
        ``inherits`` (the symbol-vocabulary distinction between
        ``inherits`` and ``implements`` is class-to-class vs
        class-to-interface, not interface-to-interface).
        """
        source = """
            package com.example.sample;
            public interface Drawable extends Renderable, Sized {
                String describe();
            }
        """
        records = extract_class_inheritance(source)
        self.assertEqual(
            records,
            [
                ("Drawable", "Renderable", "inherits"),
                ("Drawable", "Sized", "inherits"),
            ],
        )

    def test_record_implements_clause(self) -> None:
        """Record declarations with implements clauses are captured.

        The generic-arg tail is stripped by the base-entry parser so
        ``Comparable<Point>`` extracts as ``Comparable``.
        """
        source = """
            package com.example.sample;
            public record Point(int x, int y) implements Comparable<Point> {
                public int compareTo(Point other) { return 0; }
            }
        """
        records = extract_class_inheritance(source)
        self.assertEqual(
            records,
            [("Point", "Comparable", "implements")],
        )

    def test_generic_bound_extends_is_not_class_extends(self) -> None:
        """``class Foo<T extends Number>`` -- inner ``extends`` is a bound."""
        source = """
            package com.example.sample;
            public class Container<T extends Number> {
                private T value;
            }
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [])

    def test_generic_class_with_real_extends(self) -> None:
        """Generic class with both a type parameter AND a real extends."""
        source = """
            package com.example.sample;
            public class TypedShape<T> extends Shape {
                private T payload;
            }
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("TypedShape", "Shape", "inherits")])

    def test_comments_do_not_produce_phantom_records(self) -> None:
        """``class Foo extends Bar`` inside a comment must be stripped."""
        source = """
            // public class Phantom extends NotABase {}
            /* public class Ghost extends NotEitherBase {} */
            package com.example.sample;
            public class Real extends ActualBase {}
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Real", "ActualBase", "inherits")])

    def test_qualified_base_keeps_dotted_form(self) -> None:
        """``extends java.util.HashMap`` keeps the full qualified form."""
        source = """
            package com.example.sample;
            public class MyMap extends java.util.HashMap {}
        """
        records = extract_class_inheritance(source)
        self.assertEqual(
            records, [("MyMap", "java.util.HashMap", "inherits")],
        )


class RecordInheritanceTest(unittest.TestCase):
    """``record_inheritance`` stages module-aware records for finalise."""

    def test_records_carry_module_path_and_short_name(self) -> None:
        records: list = []
        record_inheritance(
            records,
            rel_path="src/main/java/com/example/sample/Circle.java",
            source_text="""
                package com.example.sample;
                public class Circle extends Shape {}
            """,
        )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["derived"], "Circle")
        self.assertEqual(rec["base"], "Shape")
        self.assertEqual(rec["base_short"], "Shape")
        self.assertEqual(rec["edge_type"], "inherits")
        self.assertIn("src.main.java.com.example.sample", rec["module_path"])

    def test_qualified_base_strips_to_short_name(self) -> None:
        records: list = []
        record_inheritance(
            records,
            rel_path="src/main/java/com/example/MyMap.java",
            source_text="""
                package com.example;
                public class MyMap extends java.util.HashMap {}
            """,
        )
        self.assertEqual(records[0]["base"], "java.util.HashMap")
        self.assertEqual(records[0]["base_short"], "HashMap")

    def test_self_reference_skipped(self) -> None:
        """A nonsensical ``class Foo extends Foo`` self-reference is skipped."""
        records: list = []
        record_inheritance(
            records,
            rel_path="src/main/java/com/example/Foo.java",
            source_text="""
                package com.example;
                public class Foo extends Foo {}
            """,
        )
        self.assertEqual(records, [])


class BuildProjectClassIndexTest(unittest.TestCase):
    """``build_project_class_index`` maps short class name -> symbol id."""

    def test_indexes_java_symbol_nodes_by_label(self) -> None:
        nodes = {
            "symbol:java:src.shape:Shape": {
                "type": "symbol",
                "label": "Shape",
                "props": {"language": "java"},
            },
            "symbol:java:src.circle:Circle": {
                "type": "symbol",
                "label": "Circle",
                "props": {"language": "java"},
            },
            "symbol:python:foo:Bar": {
                "type": "symbol",
                "label": "Bar",
                "props": {"language": "python"},
            },
            "file:src/shape": {"type": "file", "label": "shape"},
        }
        index = build_project_class_index(nodes)
        self.assertEqual(
            index,
            {
                "Shape": "symbol:java:src.shape:Shape",
                "Circle": "symbol:java:src.circle:Circle",
            },
        )

    def test_duplicate_short_name_first_wins(self) -> None:
        """When two java classes share a short name, the first indexed wins."""
        nodes = {
            "symbol:java:pkg.a:Util": {
                "type": "symbol",
                "label": "Util",
                "props": {"language": "java"},
            },
            "symbol:java:pkg.b:Util": {
                "type": "symbol",
                "label": "Util",
                "props": {"language": "java"},
            },
        }
        index = build_project_class_index(nodes)
        # ``dict.setdefault`` semantics: first key wins.
        self.assertEqual(len(index), 1)
        self.assertIn(index["Util"], {
            "symbol:java:pkg.a:Util", "symbol:java:pkg.b:Util",
        })


class EmitInheritanceEdgesTest(unittest.TestCase):
    """End-to-end record -> edge emission with project / unresolved targets."""

    def _make_nodes(self) -> dict[str, dict]:
        return {
            "symbol:java:src.shape:Shape": {
                "type": "symbol",
                "label": "Shape",
                "props": {"language": "java"},
            },
            "symbol:java:src.circle:Circle": {
                "type": "symbol",
                "label": "Circle",
                "props": {"language": "java"},
            },
        }

    def test_resolved_edge_originates_at_symbol(self) -> None:
        """Edge from symbol:java:...:Circle to symbol:java:...:Shape."""
        nodes = self._make_nodes()
        edges: list = []
        records = [{
            "rel_path": "src/circle.java",
            "module_path": "src.circle",
            "derived": "Circle",
            "base": "Shape",
            "base_short": "Shape",
            "edge_type": "inherits",
        }]
        emit_inheritance_edges(nodes, edges, records, "tree_sitter")
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "symbol:java:src.circle:Circle")
        self.assertEqual(edge["to"], "symbol:java:src.shape:Shape")
        self.assertEqual(edge["type"], "inherits")
        self.assertTrue(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["confidence"], "definite")
        # ADR 0074 / bd rifzk: the edge is attributed to the file whose
        # extends/implements clause produced it, so an incremental purge
        # can tell "this file is stale" apart from "this edge's endpoint
        # node happens to be gone".
        self.assertEqual(edge["props"]["provenance"], {"file": "src/circle.java"})

    def test_unresolved_base_mints_sentinel(self) -> None:
        """Edge targets symbol:unresolved:<base_short> when base not in project."""
        nodes = self._make_nodes()
        edges: list = []
        records = [{
            "rel_path": "src/circle.java",
            "module_path": "src.circle",
            "derived": "Circle",
            "base": "external.Renderable",
            "base_short": "Renderable",
            "edge_type": "implements",
        }]
        emit_inheritance_edges(nodes, edges, records, "tree_sitter")
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["to"], "symbol:unresolved:Renderable")
        self.assertEqual(edge["type"], "implements")
        self.assertFalse(edge["props"]["resolved"])
        self.assertIn("symbol:unresolved:Renderable", nodes)
        self.assertEqual(
            nodes["symbol:unresolved:Renderable"]["type"], "symbol",
        )
        self.assertEqual(
            nodes["symbol:unresolved:Renderable"]["props"]["origin"],
            "unresolved",
        )

    def test_missing_derived_symbol_skips_record(self) -> None:
        """No derived symbol -> no edge (avoids dangling from-id)."""
        nodes = self._make_nodes()
        edges: list = []
        records = [{
            "rel_path": "src/orphan.java",
            "module_path": "src.orphan",
            "derived": "Orphan",  # not in nodes
            "base": "Shape",
            "base_short": "Shape",
            "edge_type": "inherits",
        }]
        emit_inheritance_edges(nodes, edges, records, "tree_sitter")
        self.assertEqual(edges, [])

    def test_duplicate_record_emits_one_edge(self) -> None:
        """Two identical records produce a single deduped edge."""
        nodes = self._make_nodes()
        edges: list = []
        rec = {
            "rel_path": "src/circle.java",
            "module_path": "src.circle",
            "derived": "Circle",
            "base": "Shape",
            "base_short": "Shape",
            "edge_type": "inherits",
        }
        emit_inheritance_edges(nodes, edges, [rec, dict(rec)], "tree_sitter")
        self.assertEqual(len(edges), 1)

    def test_edges_originate_at_symbol_not_file(self) -> None:
        """Criterion 2 contract: from-id MUST start with ``symbol:``."""
        nodes = self._make_nodes()
        edges: list = []
        records = [{
            "rel_path": "src/circle.java",
            "module_path": "src.circle",
            "derived": "Circle",
            "base": "Shape",
            "base_short": "Shape",
            "edge_type": "inherits",
        }]
        emit_inheritance_edges(nodes, edges, records, "tree_sitter")
        self.assertTrue(
            edges[0]["from"].startswith("symbol:"),
            f"edge must originate at symbol, got {edges[0]['from']}",
        )
        self.assertFalse(
            edges[0]["from"].startswith("file:"),
            "edge must NOT originate at file (criterion 2 contract)",
        )


if __name__ == "__main__":
    unittest.main()
