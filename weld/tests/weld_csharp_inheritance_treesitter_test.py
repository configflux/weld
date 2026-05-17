"""Dispatcher-level integration tests for C# base-list edge emission.

These tests exercise the full path through
:mod:`weld.strategies.tree_sitter.extract` -- discovery loop, C#
enricher, post-pass -- and assert that the documented ``inherits`` /
``implements`` edges land in the result. Lower-level helper unit tests
live in :mod:`weld.tests.weld_csharp_inheritance_test`; the
``weld_csharp_treesitter_test`` file pins the unrelated startup /
visibility / metadata behaviour.

Split out of ``weld_csharp_treesitter_test.py`` to keep each test
module under the 400-line line-count cap.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


class CSharpInheritanceTreesitterTest(unittest.TestCase):
    """End-to-end assertions on edges emitted from ``base_list`` extraction."""

    def test_csharp_emits_inherits_and_implements_edges(self) -> None:
        """``class Foo : Base, IFoo`` emits one inherits + one implements edge.

        Heuristic per ADR 0050: bases matching ``^I[A-Z]`` are
        interfaces (``implements``); other bases default to
        ``inherits``. Both carry ``confidence: inferred`` because the
        name-only heuristic cannot resolve a class-named-with-I-prefix
        edge case.
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "Foo.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample;

                    public class Foo : Base, IFoo {
                        public void Bar() {}
                    }
                """),
                encoding="utf-8",
            )
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Foo", "Bar"],
                         "classes": ["Foo"],
                         "imports": [],
                         "methods": ["Bar"],
                         "namespaces": ["Sample"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        inherits = [e for e in result.edges if e["type"] == "inherits"]
        implements = [e for e in result.edges if e["type"] == "implements"]
        self.assertEqual(len(inherits), 1, result.edges)
        self.assertEqual(len(implements), 1, result.edges)
        # ADR 0064 criterion 2: the edge ``from`` is the class-level
        # promoted symbol id, not the file node. ``ts_module_from_path``
        # maps ``src/Foo.cs`` -> ``src.Foo`` and the C# definition
        # promoter mints ``symbol:csharp:<module_path>:<class>`` (see
        # :mod:`weld.strategies._ts_definitions`).
        derived_symbol_id = "symbol:csharp:src.Foo:Foo"
        self.assertEqual(inherits[0]["from"], derived_symbol_id)
        self.assertEqual(implements[0]["from"], derived_symbol_id)
        # The derived symbol node must exist (promoted by the dispatcher
        # in the same loop iteration that recorded the base pair).
        self.assertIn(derived_symbol_id, result.nodes)
        # Regression guard for the file-node-from bug: the source must
        # not be the file node (multi-class files would otherwise
        # silently merge unrelated inheritance edges).
        self.assertFalse(inherits[0]["from"].startswith("file:"))
        self.assertFalse(implements[0]["from"].startswith("file:"))
        # Heuristic split: Base -> inherits, IFoo -> implements.
        self.assertEqual(inherits[0]["to"], "symbol:csharp:Sample.Base")
        self.assertEqual(implements[0]["to"], "symbol:csharp:Sample.IFoo")
        # Confidence is inferred per ADR 0050 (name-only heuristic).
        self.assertEqual(inherits[0]["props"]["confidence"], "inferred")
        self.assertEqual(implements[0]["props"]["confidence"], "inferred")
        # Both edges declare their source strategy so downstream linters
        # can attribute provenance.
        self.assertEqual(
            inherits[0]["props"]["source_strategy"], "tree_sitter",
        )
        self.assertEqual(
            implements[0]["props"]["source_strategy"], "tree_sitter",
        )
        # The base symbol nodes are minted as ``symbol`` placeholders
        # with origin/external classification (no resolution in v1).
        for base_id in (
            "symbol:csharp:Sample.Base",
            "symbol:csharp:Sample.IFoo",
        ):
            self.assertIn(base_id, result.nodes)
            self.assertEqual(result.nodes[base_id]["type"], "symbol")

    def test_csharp_inherits_handles_qualified_and_generic_bases(self) -> None:
        """Qualified and generic bases retain their declared prefix."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "Foo.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample;

                    public class Foo : MyApp.OuterBase, System.IDisposable, IList<int> {
                    }
                """),
                encoding="utf-8",
            )
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Foo"],
                         "classes": ["Foo"],
                         "imports": [],
                         "namespaces": ["Sample"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        inherits = {e["to"] for e in result.edges if e["type"] == "inherits"}
        implements = {e["to"] for e in result.edges if e["type"] == "implements"}
        # Qualified non-interface name: inherits (last identifier
        # ``OuterBase`` does not match ``^I[A-Z]``).
        self.assertIn("symbol:csharp:MyApp.OuterBase", inherits)
        # Qualified interface name: implements (last identifier matches
        # the ``I[A-Z]`` convention).
        self.assertIn("symbol:csharp:System.IDisposable", implements)
        # Generic base: strip type-argument list, then heuristic on
        # remaining short name ``IList`` -> implements.
        self.assertIn("symbol:csharp:Sample.IList", implements)

    def test_csharp_inherits_routes_to_project_file_when_resolved(
        self,
    ) -> None:
        """Resolution prefers a project file id when the base name matches.

        When the base resolves to a class declared in another project
        file, the edge targets that file node instead of an external
        ``symbol:csharp:...`` placeholder.
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "Base.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample;
                    public class Base {}
                """),
                encoding="utf-8",
            )
            (src / "Foo.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample;
                    public class Foo : Base {}
                """),
                encoding="utf-8",
            )

            symbols_by_file = {
                "Base.cs": {
                    "exports": ["Base"],
                    "classes": ["Base"],
                    "imports": [],
                    "namespaces": ["Sample"],
                },
                "Foo.cs": {
                    "exports": ["Foo"],
                    "classes": ["Foo"],
                    "imports": [],
                    "namespaces": ["Sample"],
                },
            }

            def parse_side_effect(fpath, language, queries, **_kw):
                return symbols_by_file[Path(fpath).name]

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     side_effect=parse_side_effect,
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        inherits = [e for e in result.edges if e["type"] == "inherits"]
        # One edge: Foo -> Base (resolved to the project file).
        self.assertEqual(len(inherits), 1)
        base_id = next(
            nid for nid, n in result.nodes.items()
            if n["type"] == "file" and n["props"].get("file") == "src/Base.cs"
        )
        self.assertEqual(inherits[0]["to"], base_id)

    def test_csharp_inherits_runs_under_real_grammar(self) -> None:
        """End-to-end: real tree-sitter c-sharp grammar emits the edges.

        Skipped when the tree-sitter c-sharp grammar is not importable
        so the test stays optional in CI environments without the
        extra installed.
        """
        try:
            import tree_sitter  # noqa: F401
            import tree_sitter_c_sharp  # noqa: F401
        except ImportError:
            self.skipTest("tree-sitter or tree_sitter_c_sharp not installed")

        from weld.strategies import tree_sitter as ts

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "Foo.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample;

                    public class Foo : Base, IFoo {
                        public void Bar() {}
                    }
                """),
                encoding="utf-8",
            )
            result = ts.extract(
                root,
                {"glob": "**/*.cs", "language": "csharp"},
                {},
            )
        inherits = [e for e in result.edges if e["type"] == "inherits"]
        implements = [e for e in result.edges if e["type"] == "implements"]
        self.assertGreaterEqual(len(inherits), 1)
        self.assertGreaterEqual(len(implements), 1)
        inherits_targets = {e["to"] for e in inherits}
        implements_targets = {e["to"] for e in implements}
        self.assertIn("symbol:csharp:Sample.Base", inherits_targets)
        self.assertIn("symbol:csharp:Sample.IFoo", implements_targets)

    def test_csharp_inherits_works_for_interfaces_and_records(self) -> None:
        """Interface and record declarations also emit base-list edges."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "Mixed.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample;
                    public interface IDerived : IBase {}
                    public record OrderRecord : ValueObject;
                """),
                encoding="utf-8",
            )
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["IDerived", "OrderRecord"],
                         "classes": ["IDerived", "OrderRecord"],
                         "imports": [],
                         "namespaces": ["Sample"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        implements = {e["to"] for e in result.edges if e["type"] == "implements"}
        inherits = {e["to"] for e in result.edges if e["type"] == "inherits"}
        # Interface inheritance (``IDerived : IBase``) -> implements.
        self.assertIn("symbol:csharp:Sample.IBase", implements)
        # Record inheriting a non-interface positional base -> inherits.
        self.assertIn("symbol:csharp:Sample.ValueObject", inherits)

    def test_inheritance_edges_originate_at_class_symbol_not_file(
        self,
    ) -> None:
        """Regression: edge ``from`` must be ``symbol:csharp:*``, not ``file:*``.

        Pins ADR 0064 criterion 2. In a multi-class file the file-level
        edge cannot distinguish which class inherits from which; the
        bug surfaced on the ShareX corpus (786 inherits + 90 implements
        edges, 100% sourced from file nodes -- ``wd context`` returned
        no inheritance neighbours from the class symbol). The fix
        wires each emitted edge to the class-level promoted symbol id
        (``symbol:csharp:<module_path>:<class>``).
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            # Multi-class file: two unrelated inheritance chains share
            # the same file node. The file-node-from bug would make
            # both chains indistinguishable; symbol-from edges
            # disambiguate.
            (src / "Multi.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample;

                    public class Alpha : AlphaBase, IAlpha {}
                    public class Beta : BetaBase, IBeta {}
                """),
                encoding="utf-8",
            )
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Alpha", "Beta"],
                         "classes": ["Alpha", "Beta"],
                         "imports": [],
                         "namespaces": ["Sample"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        inherits = [e for e in result.edges if e["type"] == "inherits"]
        implements = [e for e in result.edges if e["type"] == "implements"]

        # Every inheritance/implementation edge originates at a class
        # symbol node (regression guard).
        for edge in inherits + implements:
            self.assertTrue(
                edge["from"].startswith("symbol:csharp:"),
                f"edge from is not a class symbol: {edge!r}",
            )
            self.assertFalse(
                edge["from"].startswith("file:"),
                f"edge from is a file node, regression of class-edge bug: {edge!r}",
            )

        # Disambiguation: the two inheritance chains are visibly
        # separate in the edge graph, keyed on the class symbol id.
        alpha_id = "symbol:csharp:src.Multi:Alpha"
        beta_id = "symbol:csharp:src.Multi:Beta"
        alpha_inherits = {e["to"] for e in inherits if e["from"] == alpha_id}
        alpha_implements = {e["to"] for e in implements if e["from"] == alpha_id}
        beta_inherits = {e["to"] for e in inherits if e["from"] == beta_id}
        beta_implements = {e["to"] for e in implements if e["from"] == beta_id}
        self.assertEqual(alpha_inherits, {"symbol:csharp:Sample.AlphaBase"})
        self.assertEqual(alpha_implements, {"symbol:csharp:Sample.IAlpha"})
        self.assertEqual(beta_inherits, {"symbol:csharp:Sample.BetaBase"})
        self.assertEqual(beta_implements, {"symbol:csharp:Sample.IBeta"})

        # The class-level symbol nodes exist in the graph so
        # ``wd context`` returns the neighbours.
        self.assertIn(alpha_id, result.nodes)
        self.assertIn(beta_id, result.nodes)


if __name__ == "__main__":
    unittest.main()
