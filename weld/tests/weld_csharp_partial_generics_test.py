"""Generic-parameter + modifier-order tests for the partial-class merger.

Companion to :mod:`weld.tests.weld_csharp_partial_class_test`. The
split exists so each test file stays under the 400-line line-count
cap. Both files exercise the same Wave 3 surface
(``_csharp_partial_classes`` + ``_csharp_tree_sitter.finalise``).

Two surfaces covered here:

1. Generic-parameter preservation -- ``partial class Box<T> { ... }``
   must yield a symbol node whose ``label`` and
   ``props.generic_parameters`` carry the full ``<T>`` shape.
2. Modifier-ordering tolerance -- C# allows ``sealed partial class``,
   ``partial sealed class``, etc. The regex inside
   :mod:`weld.strategies._csharp_partial_classes` accepts every
   valid order.
"""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from weld.tests._csharp_partial_class_lib import (
    make_single_file_tree,
    stub_symbol_payload,
)


class ModifierOrderingToleranceTest(unittest.TestCase):
    """``partial`` may appear before or after other class modifiers."""

    def test_sealed_partial_class_recognised(self) -> None:
        # C# allows the order ``sealed partial class Foo``. The merger
        # must accept this declaration form -- a single declaration
        # still emits a partial-class symbol node.
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_single_file_tree(
                td,
                """\
                namespace Sample.Api;

                public sealed partial class Foo {
                    public int Bar() => 1;
                }
                """,
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value=stub_symbol_payload(classes=["Foo"]),
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        self.assertIn("symbol:csharp:Sample.Api.Foo", result.nodes)

    def test_partial_before_other_modifier_recognised(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_single_file_tree(
                td,
                """\
                namespace Sample.Api;

                public partial sealed class Foo {
                    public int Bar() => 1;
                }
                """,
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value=stub_symbol_payload(classes=["Foo"]),
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        self.assertIn("symbol:csharp:Sample.Api.Foo", result.nodes)


class GenericParameterPreservationTest(unittest.TestCase):
    """Generic parameters from ``Foo<T>`` survive into the symbol label."""

    def test_generic_parameter_preserved_in_label(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_single_file_tree(
                td,
                """\
                namespace Sample.Api;

                public partial class Box<T> {
                    public T Value { get; set; }
                }
                """,
                filename="BoxT.cs",
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value=stub_symbol_payload(classes=["Box"]),
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        symbol_id = "symbol:csharp:Sample.Api.Box"
        self.assertIn(symbol_id, result.nodes)
        node = result.nodes[symbol_id]
        # Label preserves the generic parameter syntax; the node id
        # strips it because the id has to be stable across declarations
        # with different generic-arg names (T vs U).
        self.assertEqual(node["label"], "Box<T>")
        self.assertEqual(
            node["props"]["generic_parameters"], "<T>",
        )

    def test_multi_arg_generic_preserved(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_single_file_tree(
                td,
                """\
                namespace Sample.Api;

                public partial class Pair<TFirst, TSecond> {
                    public TFirst First { get; set; }
                    public TSecond Second { get; set; }
                }
                """,
                filename="Pair.cs",
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value=stub_symbol_payload(classes=["Pair"]),
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        symbol_id = "symbol:csharp:Sample.Api.Pair"
        self.assertIn(symbol_id, result.nodes)
        self.assertEqual(
            result.nodes[symbol_id]["label"], "Pair<TFirst, TSecond>",
        )
        self.assertEqual(
            result.nodes[symbol_id]["props"]["generic_parameters"],
            "<TFirst, TSecond>",
        )


if __name__ == "__main__":
    unittest.main()
