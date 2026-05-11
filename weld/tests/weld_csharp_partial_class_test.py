"""Tests for partial-class merging in ``_csharp_tree_sitter`` (Wave 3).

ADR 0056 Wave 3 extends the C# tree-sitter enricher to track ``partial
class`` declarations across files and merge them into one
``symbol:csharp:<namespace>.<TypeName>`` node listing every contributing
file.

The merge runs as a *finalise* pass after the per-file enrichment loop
so all partial pieces are visible. The pass emits a single symbol node
plus one ``contains`` edge per contributing file
(``symbol -[contains]-> file:``) carrying ``confidence="definite"`` per
ADR 0050.

Generic-parameter preservation and modifier-order tolerance live in a
sibling test module (:mod:`weld.tests.weld_csharp_partial_generics_test`)
to keep both files under the 400-line line-count cap.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weld.tests._csharp_partial_class_lib import (
    make_partial_tree,
    make_single_file_tree,
    stub_symbol_payload,
)


class PartialClassMergingTest(unittest.TestCase):
    """Two files declaring ``partial class Foo`` merge into one symbol."""

    def test_two_partial_files_merge_into_one_symbol(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_partial_tree(td)
            payloads = {
                "Foo.Part1.cs": stub_symbol_payload(
                    classes=["Foo"], methods=["GetA"],
                ),
                "Foo.Part2.cs": stub_symbol_payload(
                    classes=["Foo"], methods=["GetB"],
                ),
            }

            def _fake_parse(file_path: Path, language: str, queries) -> dict:
                return payloads.get(file_path.name, {"exports": []})

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     side_effect=_fake_parse,
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        symbol_id = "symbol:csharp:Sample.Api.Foo"
        self.assertIn(symbol_id, result.nodes)
        node = result.nodes[symbol_id]
        self.assertEqual(node["type"], "symbol")
        self.assertEqual(node["props"]["kind"], "partial_class")
        # Both files contribute; the list is sorted for determinism.
        files = node["props"]["files"]
        self.assertEqual(
            files,
            ["src/Foo.Part1.cs", "src/Foo.Part2.cs"],
        )
        self.assertEqual(node["props"]["partial_count"], 2)
        self.assertEqual(node["props"]["language"], "csharp")
        self.assertEqual(node["props"]["confidence"], "definite")

    def test_merge_emits_contains_edges_to_each_partial_file(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_partial_tree(td)

            def _fake_parse(file_path: Path, language: str, queries) -> dict:
                return stub_symbol_payload(classes=["Foo"], methods=["X"])

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     side_effect=_fake_parse,
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        symbol_id = "symbol:csharp:Sample.Api.Foo"
        contains = [
            edge for edge in result.edges
            if edge.get("type") == "contains"
            and edge.get("from") == symbol_id
        ]
        self.assertEqual(len(contains), 2)
        for edge in contains:
            self.assertEqual(edge["props"]["confidence"], "definite")
            # ADR 0056 Wave 3: edge ships with source strategy
            # provenance so consumers can attribute the merge.
            self.assertEqual(
                edge["props"]["source_strategy"], "tree_sitter",
            )
            self.assertEqual(edge["props"]["kind"], "partial_class")

    def test_single_partial_declaration_still_emits_symbol(self) -> None:
        # A class declared ``partial`` in a single file still gets a
        # symbol node so the modifier is queryable. ``partial_count``
        # is 1 in that case.
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_single_file_tree(
                td,
                """\
                namespace Sample.Api;

                public partial class Foo {
                    public int GetA() => 1;
                }
                """,
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value=stub_symbol_payload(
                         classes=["Foo"], methods=["GetA"],
                     ),
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        symbol_id = "symbol:csharp:Sample.Api.Foo"
        self.assertIn(symbol_id, result.nodes)
        self.assertEqual(
            result.nodes[symbol_id]["props"]["partial_count"], 1,
        )

    def test_non_partial_class_does_not_emit_symbol(self) -> None:
        # A regular (non-``partial``) class declaration is not promoted
        # to a symbol node: that promotion is the responsibility of the
        # Wave 2 framework-aware strategies (EF Core, controllers).
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_single_file_tree(
                td,
                """\
                namespace Sample.Api;

                public class Plain {
                    public int GetA() => 1;
                }
                """,
                filename="Plain.cs",
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value=stub_symbol_payload(
                         classes=["Plain"], methods=["GetA"],
                     ),
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        # No partial declaration -> no symbol node from this pass.
        symbol_ids = [
            nid for nid in result.nodes if nid.startswith("symbol:csharp:")
        ]
        self.assertEqual(symbol_ids, [])

    def test_comment_text_does_not_synthesise_phantom_partial_symbol(self) -> None:
        # Defensive: a doc comment containing the phrase
        # "partial class XYZ" must not produce a phantom symbol node.
        # Comments are stripped before the regex scan.
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = make_single_file_tree(
                td,
                """\
                // The matching half of a partial class lives elsewhere;
                /* Also: partial class GhostShouldNotEmit lives in a doc. */
                namespace Sample.Api;

                public partial class Real {
                    public int Bar() => 1;
                }
                """,
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value=stub_symbol_payload(classes=["Real"]),
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        symbol_ids = sorted(
            nid for nid in result.nodes if nid.startswith("symbol:csharp:")
        )
        # Only the real partial class -- the commented one was a
        # phantom that the comment stripper kills before the regex
        # gets a chance to see it.
        self.assertEqual(symbol_ids, ["symbol:csharp:Sample.Api.Real"])

    def test_partial_classes_in_different_namespaces_do_not_merge(self) -> None:
        from weld.strategies import tree_sitter
        import textwrap

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Sample.Api.csproj").write_text(
                textwrap.dedent("""\
                    <Project Sdk="Microsoft.NET.Sdk">
                      <PropertyGroup>
                        <RootNamespace>Sample.Api</RootNamespace>
                      </PropertyGroup>
                    </Project>
                """),
                encoding="utf-8",
            )
            (root / "A.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample.Api.A;

                    public partial class Foo {
                        public int X() => 1;
                    }
                """),
                encoding="utf-8",
            )
            (root / "B.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample.Api.B;

                    public partial class Foo {
                        public int Y() => 2;
                    }
                """),
                encoding="utf-8",
            )

            def _fake_parse(file_path: Path, language: str, queries) -> dict:
                return stub_symbol_payload(classes=["Foo"], methods=["X"])

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     side_effect=_fake_parse,
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        # Two distinct symbol nodes -- one per namespace.
        self.assertIn("symbol:csharp:Sample.Api.A.Foo", result.nodes)
        self.assertIn("symbol:csharp:Sample.Api.B.Foo", result.nodes)


if __name__ == "__main__":
    unittest.main()
