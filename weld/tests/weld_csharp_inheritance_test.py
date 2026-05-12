"""Tests for the C# base-list inheritance extractor.

Companion to :mod:`weld.tests.weld_csharp_treesitter_test`. The
treesitter-level test verifies end-to-end edge emission inside the
shared tree-sitter dispatcher; this module pins down the lower-level
helpers (regex pair extraction, interface-vs-class heuristic,
project-file resolution) in isolation so a regression there gets a
narrow failure trace.
"""

from __future__ import annotations

import unittest

from weld.strategies._csharp_inheritance import (
    build_project_file_index,
    emit_base_edges,
    extract_base_pairs,
    finalise,
    split_inherits_implements,
)


class ExtractBasePairsTest(unittest.TestCase):
    """The regex pair extractor returns one triple per base entry."""

    def test_single_class_with_class_and_interface_bases(self) -> None:
        source = """
            namespace Sample;
            public class Foo : Base, IFoo {}
        """
        pairs = extract_base_pairs(source)
        self.assertEqual(
            pairs,
            [("Sample", "Foo", "Base"), ("Sample", "Foo", "IFoo")],
        )

    def test_qualified_and_generic_bases(self) -> None:
        """Qualified and generic bases are split per entry; generics
        keep their bare dotted form (without the type-arg tail) so the
        heuristic can run on the short identifier."""
        source = """
            namespace Sample;
            public class Foo : MyApp.OuterBase, System.IDisposable, IList<int> {}
        """
        pairs = extract_base_pairs(source)
        bases = [base for _ns, _derived, base in pairs]
        self.assertIn("MyApp.OuterBase", bases)
        self.assertIn("System.IDisposable", bases)
        # The generic-arg tail is stripped; the bare identifier ``IList``
        # is what the heuristic sees.
        self.assertIn("IList", bases)

    def test_interface_record_struct_declarations(self) -> None:
        source = """
            namespace Sample;
            public interface IDerived : IBase {}
            public record OrderRecord : ValueObject;
            public struct PointXY : IComparable {}
        """
        pairs = extract_base_pairs(source)
        derived = {derived: base for _ns, derived, base in pairs}
        self.assertEqual(derived["IDerived"], "IBase")
        self.assertEqual(derived["OrderRecord"], "ValueObject")
        self.assertEqual(derived["PointXY"], "IComparable")

    def test_comments_do_not_produce_phantom_pairs(self) -> None:
        """A ``class Foo : Base`` declaration inside a comment is a
        false-positive trap. The extractor must strip comments before
        scanning."""
        source = """
            // class Foo : NotABase {}
            /* class Bar : NotEitherBase {} */
            namespace Sample;
            public class Real : ActualBase {}
        """
        pairs = extract_base_pairs(source)
        self.assertEqual(pairs, [("Sample", "Real", "ActualBase")])

    def test_class_with_where_clause(self) -> None:
        """Generic constraint clauses (``where T : ...``) must not be
        captured as base entries."""
        source = """
            namespace Sample;
            public class Foo<T> : BaseClass where T : class {}
        """
        pairs = extract_base_pairs(source)
        bases = [base for _ns, _derived, base in pairs]
        self.assertIn("BaseClass", bases)
        # ``class`` from the constraint should not surface as a base.
        self.assertNotIn("class", bases)

    def test_class_without_bases_is_skipped(self) -> None:
        source = """
            namespace Sample;
            public class Alone {}
            public class Empty {
                public void Method() {}
            }
        """
        pairs = extract_base_pairs(source)
        self.assertEqual(pairs, [])


class SplitInheritsImplementsTest(unittest.TestCase):
    """Bases matching ``^I[A-Z]`` map to ``implements`` per ADR 0050."""

    def test_interface_naming_maps_to_implements(self) -> None:
        self.assertEqual(split_inherits_implements("IFoo"), "implements")
        self.assertEqual(split_inherits_implements("IDisposable"), "implements")
        self.assertEqual(
            split_inherits_implements("System.IList"), "implements",
        )

    def test_class_naming_defaults_to_inherits(self) -> None:
        self.assertEqual(split_inherits_implements("Base"), "inherits")
        self.assertEqual(split_inherits_implements("URLSharer"), "inherits")
        self.assertEqual(
            split_inherits_implements("MyApp.OuterBase"), "inherits",
        )
        # Lowercase ``i`` -- not the interface convention.
        self.assertEqual(split_inherits_implements("iconBase"), "inherits")
        # ``I`` followed by a lowercase letter (rare but real, e.g.
        # ``Identity``) is treated as a class.
        self.assertEqual(split_inherits_implements("Identity"), "inherits")


class EmitBaseEdgesTest(unittest.TestCase):
    """``emit_base_edges`` writes the edge body and resolves the target."""

    def test_emits_external_symbol_when_unresolved(self) -> None:
        nodes: dict = {}
        edges: list = []
        emit_base_edges(
            nodes,
            edges,
            file_node_id="file:src/Foo",
            namespace="Sample",
            derived_class="Foo",
            base_name="Base",
            source_strategy="tree_sitter",
            project_file_index={},
        )
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "file:src/Foo")
        self.assertEqual(edge["to"], "symbol:csharp:Sample.Base")
        self.assertEqual(edge["type"], "inherits")
        self.assertEqual(edge["props"]["confidence"], "inferred")
        self.assertEqual(edge["props"]["source_strategy"], "tree_sitter")
        self.assertEqual(edge["props"]["base_name"], "Base")
        self.assertEqual(edge["props"]["derived_class"], "Foo")
        # The placeholder symbol node is minted with origin: external.
        target = nodes["symbol:csharp:Sample.Base"]
        self.assertEqual(target["type"], "symbol")
        self.assertEqual(target["props"]["origin"], "external")
        self.assertEqual(target["props"]["kind"], "base_reference")

    def test_resolves_target_to_project_file_when_indexed(self) -> None:
        nodes: dict = {}
        edges: list = []
        emit_base_edges(
            nodes,
            edges,
            file_node_id="file:src/Foo",
            namespace="Sample",
            derived_class="Foo",
            base_name="Base",
            source_strategy="tree_sitter",
            project_file_index={"Base": "file:src/Base"},
        )
        self.assertEqual(edges[0]["to"], "file:src/Base")
        # No symbol placeholder when the base resolves to a project file.
        self.assertNotIn("symbol:csharp:Sample.Base", nodes)


class BuildProjectFileIndexTest(unittest.TestCase):
    """The file index keys on declared class names of C# file nodes."""

    def test_index_collects_types_from_csharp_file_nodes(self) -> None:
        nodes = {
            "file:src/Base": {
                "type": "file",
                "props": {
                    "language": "csharp",
                    "types": ["Base"],
                },
            },
            "file:src/IFoo": {
                "type": "file",
                "props": {
                    "language": "csharp",
                    "types": ["IFoo"],
                },
            },
            "file:py/other": {
                "type": "file",
                "props": {
                    "language": "python",
                    "types": ["NotIndexed"],
                },
            },
            "symbol:csharp:Sample.Foo": {
                "type": "symbol",
                "props": {"language": "csharp"},
            },
        }
        index = build_project_file_index(nodes)
        self.assertEqual(index["Base"], "file:src/Base")
        self.assertEqual(index["IFoo"], "file:src/IFoo")
        # Non-C# file nodes are ignored.
        self.assertNotIn("NotIndexed", index)


class FinaliseEntryPointTest(unittest.TestCase):
    """The post-pass walks the records list and emits one edge per pair."""

    def test_none_caches_is_noop(self) -> None:
        nodes: dict = {}
        edges: list = []
        finalise(nodes, edges, None, "tree_sitter")
        self.assertEqual(nodes, {})
        self.assertEqual(edges, [])

    def test_empty_records_is_noop(self) -> None:
        nodes: dict = {}
        edges: list = []
        finalise(
            nodes,
            edges,
            {"inheritance_records": []},
            "tree_sitter",
        )
        self.assertEqual(nodes, {})
        self.assertEqual(edges, [])

    def test_records_emit_inherits_and_implements_edges(self) -> None:
        nodes: dict = {
            "file:src/Foo": {
                "type": "file",
                "props": {"language": "csharp", "types": ["Foo"]},
            },
        }
        edges: list = []
        caches = {
            "inheritance_records": [
                ("file:src/Foo", "Sample", "Foo", "Base"),
                ("file:src/Foo", "Sample", "Foo", "IFoo"),
            ],
        }
        finalise(nodes, edges, caches, "tree_sitter")
        edge_types = sorted(e["type"] for e in edges)
        self.assertEqual(edge_types, ["implements", "inherits"])


if __name__ == "__main__":
    unittest.main()
