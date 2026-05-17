"""Dispatcher-level tests for partial-class inheritance edges (ADR 0064 § 2).

Companion to :mod:`weld.tests.weld_csharp_inheritance_treesitter_test`.
That module pins ADR 0064 criterion 2 for *non-partial* classes (edges
originate at the per-file class symbol
``symbol:csharp:<module_path>:<class>``). The follow-up bug surfaced
when the derived class is *partial*: the per-file class symbol still
exists, but the canonical
merged partial-class symbol
``symbol:csharp:<namespace>.<class>`` minted by
:func:`weld.strategies._csharp_partial_classes.finalise` (ADR 0056 Wave
3) is a different node id. Consumers traversing inheritance from the
canonical id (the natural query target) saw nothing because every edge
pointed at the per-file alias.

The fix retargets the edge ``from`` to the canonical merged symbol
whenever the partial-class merger produced one for the same
``(namespace, class)`` key. Non-partial classes keep the per-file
``symbol:csharp:<module_path>:<class>`` source (no regression on the
existing ADR 0064 criterion 2 fix).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


def _write(root: Path, rel: str, body: str) -> None:
    """Write *body* (dedented) to ``root/rel`` creating dirs as needed."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


class PartialClassInheritanceEdgeTest(unittest.TestCase):
    """Inheritance edges from partial classes originate at the canonical symbol."""

    def test_partial_class_base_list_edge_from_canonical_symbol(self) -> None:
        """Two-file partial class with base list on one file.

        ``partial class Foo : Bar`` in Foo.Part1.cs plus
        ``partial class Foo {}`` (no base list) in Foo.Part2.cs. The
        partial-class merger mints ``symbol:csharp:Sample.Api.Foo`` and
        the inheritance post-pass must wire the inherits edge from that
        canonical id, NOT from ``symbol:csharp:src.Foo.Part1:Foo``.
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "src/Foo.Part1.cs",
                """\
                    namespace Sample.Api;

                    public partial class Foo : Bar {
                        public int GetA() => 1;
                    }
                """,
            )
            _write(
                root,
                "src/Foo.Part2.cs",
                """\
                    namespace Sample.Api;

                    public partial class Foo {
                        public int GetB() => 2;
                    }
                """,
            )

            def _fake_parse(file_path, language, queries, **_kw) -> dict:
                return {
                    "exports": ["Foo"],
                    "classes": ["Foo"],
                    "imports": [],
                    "namespaces": ["Sample.Api"],
                }

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

        canonical = "symbol:csharp:Sample.Api.Foo"
        per_file = "symbol:csharp:src.Foo.Part1:Foo"

        # Sanity: both ids exist as nodes (the per-file alias is the
        # promoted class symbol; the canonical is the partial-class
        # merger output).
        self.assertIn(canonical, result.nodes)
        self.assertIn(per_file, result.nodes)

        inherits = [e for e in result.edges if e["type"] == "inherits"]
        # One inherits edge: Foo -> Bar.
        self.assertEqual(len(inherits), 1, inherits)
        edge = inherits[0]
        # The fix: edge originates at the canonical partial-class
        # symbol, not the per-file alias.
        self.assertEqual(edge["from"], canonical)
        self.assertNotEqual(edge["from"], per_file)
        # Target resolution and edge body unchanged.
        self.assertEqual(edge["to"], "symbol:csharp:Sample.Api.Bar")
        self.assertEqual(edge["props"]["derived_class"], "Foo")

    def test_partial_class_base_list_on_both_files_emits_one_canonical_edge(
        self,
    ) -> None:
        """Both partial files declare the same base list.

        Edges per ``(derived, base)`` are not deduplicated across files
        (each declaration produces its own record), but every edge must
        originate at the canonical merged symbol id. The downstream
        consumer queries ``wd context symbol:csharp:Sample.Api.Foo``
        and sees the inheritance neighbours; whether there are one or
        two edges is a graph density detail.
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "src/Foo.Part1.cs",
                """\
                    namespace Sample.Api;
                    public partial class Foo : Bar, IThing {
                        public int X() => 1;
                    }
                """,
            )
            _write(
                root,
                "src/Foo.Part2.cs",
                """\
                    namespace Sample.Api;
                    public partial class Foo : Bar, IThing {
                        public int Y() => 2;
                    }
                """,
            )

            def _fake_parse(file_path, language, queries, **_kw) -> dict:
                return {
                    "exports": ["Foo"],
                    "classes": ["Foo"],
                    "imports": [],
                    "namespaces": ["Sample.Api"],
                }

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

        canonical = "symbol:csharp:Sample.Api.Foo"
        inherits = [e for e in result.edges if e["type"] == "inherits"]
        implements = [e for e in result.edges if e["type"] == "implements"]

        # Every base-list edge originates at the canonical symbol.
        for edge in inherits + implements:
            self.assertEqual(
                edge["from"], canonical,
                f"edge from is not the canonical partial-class id: {edge!r}",
            )

        # Both bases land in the expected buckets.
        self.assertEqual(
            {e["to"] for e in inherits},
            {"symbol:csharp:Sample.Api.Bar"},
        )
        self.assertEqual(
            {e["to"] for e in implements},
            {"symbol:csharp:Sample.Api.IThing"},
        )

    def test_non_partial_class_inheritance_still_uses_per_file_symbol(
        self,
    ) -> None:
        """Regression guard: ADR 0064 criterion 2 behaviour is preserved.

        A non-partial class must keep emitting the inherits edge from
        the per-file class symbol id
        (``symbol:csharp:<module_path>:<class>``). The partial-class
        retarget logic only kicks in when the partial-class merger
        produced a canonical symbol for the ``(namespace, class)`` key.
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "src/Plain.cs",
                """\
                    namespace Sample.Api;

                    public class Plain : Base, IPlain {
                        public void Bar() {}
                    }
                """,
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Plain"],
                         "classes": ["Plain"],
                         "imports": [],
                         "namespaces": ["Sample.Api"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        per_file = "symbol:csharp:src.Plain:Plain"
        canonical = "symbol:csharp:Sample.Api.Plain"
        # Non-partial class: no canonical partial-class merger output.
        self.assertNotIn(canonical, result.nodes)
        # Per-file class symbol exists (ADR 0064 criterion 2 promotion).
        self.assertIn(per_file, result.nodes)

        inherits = [e for e in result.edges if e["type"] == "inherits"]
        implements = [e for e in result.edges if e["type"] == "implements"]
        self.assertEqual(len(inherits), 1)
        self.assertEqual(len(implements), 1)
        # Edge ``from`` is the per-file alias, NOT the bare namespace
        # id (the partial-class shape).
        self.assertEqual(inherits[0]["from"], per_file)
        self.assertEqual(implements[0]["from"], per_file)


class PartialClassInheritanceRecordOnlyOnDeclaringFileTest(unittest.TestCase):
    """Only the partial file with the base list contributes a record.

    Pins the contract that the inheritance post-pass walks records
    produced by :func:`record_base_pairs`, not the partial-class state
    itself. The retarget logic must locate the canonical id by
    ``(namespace, derived)`` and use it as ``from``, regardless of which
    contributing file declared the base list.
    """

    def test_only_declaring_file_has_record_but_edge_still_canonical(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "src/Foo.Part1.cs",
                """\
                    namespace Sample.Api;
                    public partial class Foo { public int A() => 1; }
                """,
            )
            _write(
                root,
                "src/Foo.Part2.cs",
                """\
                    namespace Sample.Api;
                    public partial class Foo : BaseOnPart2 {
                        public int B() => 2;
                    }
                """,
            )

            def _fake_parse(file_path, language, queries, **_kw) -> dict:
                return {
                    "exports": ["Foo"],
                    "classes": ["Foo"],
                    "imports": [],
                    "namespaces": ["Sample.Api"],
                }

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

        canonical = "symbol:csharp:Sample.Api.Foo"
        inherits = [e for e in result.edges if e["type"] == "inherits"]
        self.assertEqual(len(inherits), 1)
        # Edge from the canonical symbol even though the base list was
        # declared on the Part2 file.
        self.assertEqual(inherits[0]["from"], canonical)
        self.assertEqual(inherits[0]["to"], "symbol:csharp:Sample.Api.BaseOnPart2")


if __name__ == "__main__":
    unittest.main()
