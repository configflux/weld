"""ADR 0064 criterion 2 coverage for the cpp tree-sitter strategy (bd bou8).

Companion to :mod:`weld.tests.weld_java_inherits_test` and
:mod:`weld.tests.weld_csharp_inheritance_test`. The treesitter-level
cpp gate test (``tools/tier_check_cpp_gate_test``) verifies end-to-end
edge emission against the bundled fixture; this module pins the
lower-level helpers (regex pair extraction, access-specifier /
``virtual`` stripping, project-index resolution) in isolation so a
regression there gets a narrow failure trace.

C++ inheritance differs from Java / C# in three structural ways that
this test class enumerates explicitly:

1. Every base is captured as ``inherits`` (no ``implements`` -- C++
   has no separate interface concept at the language level).
2. The access specifier (``public`` / ``protected`` / ``private``) and
   the optional ``virtual`` keyword are stripped before lookup.
3. Multiple inheritance (``: public A, public B``) splits into one
   edge per base, deterministically in source order.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies._cpp_inherits import (  # noqa: E402
    build_project_class_index,
    emit_inheritance_edges,
    extract_class_inheritance,
    record_inheritance,
)


class ExtractClassInheritanceTest(unittest.TestCase):
    """Regex extractor returns one (derived, base) pair per base entry."""

    def test_single_public_base_emits_one_inherits(self) -> None:
        source = """
            namespace shapes {
            class Circle : public Shape {
                public:
                    double area() const override;
            };
            }
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Circle", "Shape")])

    def test_multiple_inheritance_emits_one_per_base(self) -> None:
        """``: public Shape, public Drawable`` -> two records.

        C++ multiple inheritance is the criterion-2 sample-size driver
        on the bundled fixture: a single class declaration yields
        multiple ``inherits`` edges, each originating at the same
        derived-class symbol.
        """
        source = """
            class Rectangle : public Shape, public Drawable {
                public:
                    double area() const override;
            };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(
            records,
            [("Rectangle", "Shape"), ("Rectangle", "Drawable")],
        )

    def test_protected_and_private_access_specifiers_stripped(self) -> None:
        """Access specifier other than ``public`` is still inheritance."""
        source = """
            class A : protected B { };
            class C : private D { };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("A", "B"), ("C", "D")])

    def test_virtual_keyword_is_stripped(self) -> None:
        """``: virtual public Base`` and ``: public virtual Base`` both work."""
        source = """
            class A : virtual public Diamond { };
            class B : public virtual Diamond { };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("A", "Diamond"), ("B", "Diamond")])

    def test_struct_inherits_treated_same_as_class(self) -> None:
        """``struct Derived : public Base`` matches the same regex."""
        source = """
            struct Derived : public Base { int x; };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Derived", "Base")])

    def test_template_class_with_base_strips_prefix(self) -> None:
        """``template<typename T> class Foo : public Bar`` captures Foo."""
        source = """
            template<typename T>
            class Foo : public Bar { };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Foo", "Bar")])

    def test_template_class_with_no_base_emits_nothing(self) -> None:
        """A template with no base-list generates zero inheritance edges.

        This is the C++ analogue of the Java ``class Container<T>``
        boundary the java sibling pins (bd 3kej): a generic class with
        no real bases must produce zero inheritance records even
        though the source contains ``<T>``-style tokens.
        """
        source = """
            template<typename T>
            class Container {
                public:
                    void add(const T& item);
            };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [])

    def test_qualified_base_keeps_dotted_form(self) -> None:
        """``: public foo::Bar`` keeps the qualified name."""
        source = """
            class Derived : public foo::Bar { };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Derived", "foo::Bar")])

    def test_generic_arg_tail_stripped(self) -> None:
        """``: public Comparable<Circle>`` -> base ``Comparable``."""
        source = """
            class Circle : public Comparable<Circle> { };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Circle", "Comparable")])

    def test_comments_do_not_produce_phantom_records(self) -> None:
        """``class Foo : public Bar`` inside a comment must be stripped."""
        source = """
            // class Phantom : public NotABase { };
            /* class Ghost : public NotEitherBase { }; */
            class Real : public ActualBase { };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Real", "ActualBase")])

    def test_template_parameter_list_with_class_keyword_does_not_match(
        self,
    ) -> None:
        """``template<class T> class Foo : public Bar`` captures Foo, not T.

        Template parameter lists frequently use the ``class`` keyword
        as a type-parameter-kind marker (``template<class T>``). The
        extractor must strip that prefix before scanning, or the
        ``class`` token inside the parameter list would match the
        declaration regex and emit a spurious record.
        """
        source = """
            template<class T> class Foo : public Bar { };
        """
        records = extract_class_inheritance(source)
        self.assertEqual(records, [("Foo", "Bar")])


class RecordInheritanceTest(unittest.TestCase):
    """``record_inheritance`` stages module-aware records for finalise."""

    def test_records_carry_module_path_and_short_name(self) -> None:
        records: list = []
        record_inheritance(
            records,
            rel_path="include/shapes/circle.hpp",
            source_text="""
                class Circle : public Shape { };
            """,
        )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["derived"], "Circle")
        self.assertEqual(rec["base"], "Shape")
        self.assertEqual(rec["base_short"], "Shape")
        self.assertIn("include.shapes.circle", rec["module_path"])

    def test_qualified_base_strips_to_short_name(self) -> None:
        records: list = []
        record_inheritance(
            records,
            rel_path="include/derived.hpp",
            source_text="""
                class Derived : public foo::Bar { };
            """,
        )
        self.assertEqual(records[0]["base"], "foo::Bar")
        self.assertEqual(records[0]["base_short"], "Bar")

    def test_self_reference_skipped(self) -> None:
        """A nonsensical ``class Foo : public Foo`` self-reference is skipped."""
        records: list = []
        record_inheritance(
            records,
            rel_path="include/foo.hpp",
            source_text="""
                class Foo : public Foo { };
            """,
        )
        self.assertEqual(records, [])


class BuildProjectClassIndexTest(unittest.TestCase):
    """``build_project_class_index`` maps short class name -> symbol id."""

    def test_indexes_cpp_symbol_nodes_by_label(self) -> None:
        nodes = {
            "symbol:cpp:include.shape:Shape": {
                "type": "symbol",
                "label": "Shape",
                "props": {"language": "cpp"},
            },
            "symbol:cpp:include.circle:Circle": {
                "type": "symbol",
                "label": "Circle",
                "props": {"language": "cpp"},
            },
            "symbol:java:src.shape:Shape": {
                "type": "symbol",
                "label": "Shape",
                "props": {"language": "java"},
            },
            "file:include/shape": {"type": "file", "label": "shape"},
        }
        index = build_project_class_index(nodes)
        self.assertEqual(
            index,
            {
                "Shape": "symbol:cpp:include.shape:Shape",
                "Circle": "symbol:cpp:include.circle:Circle",
            },
        )

    def test_duplicate_short_name_first_wins(self) -> None:
        """When two cpp classes share a short name, the first indexed wins."""
        nodes = {
            "symbol:cpp:ns.a:Util": {
                "type": "symbol",
                "label": "Util",
                "props": {"language": "cpp"},
            },
            "symbol:cpp:ns.b:Util": {
                "type": "symbol",
                "label": "Util",
                "props": {"language": "cpp"},
            },
        }
        index = build_project_class_index(nodes)
        # ``dict.setdefault`` semantics: first key wins.
        self.assertEqual(len(index), 1)
        self.assertIn(
            index["Util"],
            {"symbol:cpp:ns.a:Util", "symbol:cpp:ns.b:Util"},
        )


class EmitInheritanceEdgesTest(unittest.TestCase):
    """End-to-end record -> edge emission with project / unresolved targets."""

    def _make_nodes(self) -> dict[str, dict]:
        return {
            "symbol:cpp:include.shape:Shape": {
                "type": "symbol",
                "label": "Shape",
                "props": {"language": "cpp"},
            },
            "symbol:cpp:include.circle:Circle": {
                "type": "symbol",
                "label": "Circle",
                "props": {"language": "cpp"},
            },
        }

    def test_resolved_edge_originates_at_symbol(self) -> None:
        """Edge from symbol:cpp:...:Circle to symbol:cpp:...:Shape."""
        nodes = self._make_nodes()
        edges: list = []
        records = [{
            "rel_path": "include/circle.hpp",
            "module_path": "include.circle",
            "derived": "Circle",
            "base": "Shape",
            "base_short": "Shape",
        }]
        emit_inheritance_edges(nodes, edges, records, "tree_sitter")
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "symbol:cpp:include.circle:Circle")
        self.assertEqual(edge["to"], "symbol:cpp:include.shape:Shape")
        self.assertEqual(edge["type"], "inherits")
        self.assertTrue(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["confidence"], "definite")

    def test_unresolved_base_mints_sentinel(self) -> None:
        """Edge targets symbol:unresolved:<short> when base not in project."""
        nodes = self._make_nodes()
        edges: list = []
        records = [{
            "rel_path": "include/circle.hpp",
            "module_path": "include.circle",
            "derived": "Circle",
            "base": "std::vector",
            "base_short": "vector",
        }]
        emit_inheritance_edges(nodes, edges, records, "tree_sitter")
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["to"], "symbol:unresolved:vector")
        self.assertEqual(edge["type"], "inherits")
        self.assertFalse(edge["props"]["resolved"])
        self.assertIn("symbol:unresolved:vector", nodes)
        self.assertEqual(
            nodes["symbol:unresolved:vector"]["type"], "symbol",
        )
        self.assertEqual(
            nodes["symbol:unresolved:vector"]["props"]["origin"],
            "unresolved",
        )

    def test_missing_derived_symbol_skips_record(self) -> None:
        """No derived symbol -> no edge (avoids dangling from-id)."""
        nodes = self._make_nodes()
        edges: list = []
        records = [{
            "rel_path": "include/orphan.hpp",
            "module_path": "include.orphan",
            "derived": "Orphan",  # not in nodes
            "base": "Shape",
            "base_short": "Shape",
        }]
        emit_inheritance_edges(nodes, edges, records, "tree_sitter")
        self.assertEqual(edges, [])

    def test_duplicate_record_emits_one_edge(self) -> None:
        """Two identical records produce a single deduped edge."""
        nodes = self._make_nodes()
        edges: list = []
        rec = {
            "rel_path": "include/circle.hpp",
            "module_path": "include.circle",
            "derived": "Circle",
            "base": "Shape",
            "base_short": "Shape",
        }
        emit_inheritance_edges(nodes, edges, [rec, dict(rec)], "tree_sitter")
        self.assertEqual(len(edges), 1)

    def test_edges_originate_at_symbol_not_file(self) -> None:
        """Criterion 2 contract: from-id MUST start with ``symbol:``."""
        nodes = self._make_nodes()
        edges: list = []
        records = [{
            "rel_path": "include/circle.hpp",
            "module_path": "include.circle",
            "derived": "Circle",
            "base": "Shape",
            "base_short": "Shape",
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
