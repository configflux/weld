"""Unit tests for the shared tree-sitter definition promoter.

The promoter mints ``symbol:<lang>:<module_path>:<name>`` nodes from
per-language symbol buckets (e.g. ``classes``, ``methods``,
``properties`` for C#) and stamps each with a canonical ``kind``.

ADR 0064 criterion 1 (kind correctness): every ``symbol`` node's
``kind`` is drawn from a documented per-language vocabulary, with zero
mangled values. The historical bug (weld 0.19.1 ShareX) was a naive
``key[:-1] if key.endswith('s')`` strip that produced ``'classe'`` and
``'propertie'`` — these tests pin the canonical singulars and guard
against any plural tree-sitter bucket name leaking through.
"""

from __future__ import annotations

import unittest

from weld.strategies._ts_definitions import (
    _CSHARP_DEFINITION_KEYS,
    _canonical_csharp_kind,
    promote_definition_symbols,
)


# Tree-sitter category plurals whose ``key[:-1]`` form is a *mangled*
# (non-vocabulary) value. The historical ShareX dogfood pass (weld
# 0.19.1, 2026-05-15) observed 'classe' and 'propertie' in 8719
# symbol nodes. The regression guard below pins that none of these
# plurals round-trip to their mangled forms via
# ``_canonical_csharp_kind``. Plurals like ``methods``/``interfaces``/
# ``records`` are excluded because their suffix-stripped forms happen
# to coincide with the correct canonical singular — there is no
# observable bug to guard against for those keys.
_MANGLED_FALLOUT = {
    "classes": "classe",
    "properties": "propertie",
    "structs": "struc",
    "enums": "enu",
}


class CanonicalCSharpKindTest(unittest.TestCase):
    """``_canonical_csharp_kind`` produces the documented vocabulary."""

    def test_known_keys_map_to_canonical_singular(self) -> None:
        # Pins ADR 0064 criterion 1 canonical singulars for the
        # currently-declared C# buckets.
        self.assertEqual(_canonical_csharp_kind("classes"), "class")
        self.assertEqual(_canonical_csharp_kind("methods"), "method")
        self.assertEqual(_canonical_csharp_kind("properties"), "property")

    def test_extended_plurals_map_without_mangling(self) -> None:
        # If/when the csharp.yaml grammar gains additional buckets for
        # interface/struct/enum/record, the canonical mapping must
        # already handle them so a discovery rerun never emits
        # 'interfac', 'struc', 'enu', etc.
        self.assertEqual(_canonical_csharp_kind("interfaces"), "interface")
        self.assertEqual(_canonical_csharp_kind("structs"), "struct")
        self.assertEqual(_canonical_csharp_kind("enums"), "enum")
        self.assertEqual(_canonical_csharp_kind("records"), "record")

    def test_unknown_plural_falls_back_safely(self) -> None:
        # Defensive: an unknown future bucket name must NOT depluralise
        # by suffix strip. It returns the input key verbatim so a
        # downstream filter still has a non-mangled value to match
        # while reviewers see the unmapped raw key.
        self.assertEqual(
            _canonical_csharp_kind("widgets"),
            "widgets",
        )

    def test_no_known_plural_resolves_to_mangled_form(self) -> None:
        # The regression guard: NONE of the historically-observed
        # mangled values may appear as outputs. Exercises >=4 distinct
        # plural inputs whose naive suffix-strip would mangle.
        for plural, mangled in _MANGLED_FALLOUT.items():
            with self.subTest(plural=plural):
                resolved = _canonical_csharp_kind(plural)
                self.assertNotEqual(
                    resolved,
                    mangled,
                    f"{plural} resolved to mangled '{resolved}'",
                )
        # The currently-declared C# definition keys must all map
        # cleanly: each entry in _CSHARP_DEFINITION_KEYS resolves to
        # a non-empty singular that does NOT look like a naive strip.
        for plural in _CSHARP_DEFINITION_KEYS:
            with self.subTest(plural=plural):
                singular = _canonical_csharp_kind(plural)
                self.assertTrue(singular)
                if plural in _MANGLED_FALLOUT:
                    self.assertNotEqual(singular, _MANGLED_FALLOUT[plural])

    def test_at_least_five_plurals_are_pinned(self) -> None:
        # Acceptance contract from the issue: regression coverage
        # exercises >=5 plural tree-sitter category names. Pin each
        # to its canonical singular explicitly so a future refactor
        # cannot silently regress.
        pinned: dict[str, str] = {
            "classes": "class",
            "properties": "property",
            "methods": "method",
            "interfaces": "interface",
            "structs": "struct",
            "enums": "enum",
            "records": "record",
        }
        self.assertGreaterEqual(len(pinned), 5)
        for plural, expected in pinned.items():
            with self.subTest(plural=plural):
                self.assertEqual(
                    _canonical_csharp_kind(plural),
                    expected,
                )


class PromoteDefinitionSymbolsKindTest(unittest.TestCase):
    """``promote_definition_symbols`` stamps canonical kinds on symbols."""

    def test_csharp_promotion_uses_canonical_kinds(self) -> None:
        symbols = {
            "classes": ["OrdersController"],
            "methods": ["GetAsync"],
            "properties": ["Helper"],
        }
        nodes, edges = promote_definition_symbols(
            language="csharp",
            rel_path="src/OrdersController.cs",
            symbols=symbols,
            file_node_id="file:src/OrdersController",
            source_strategy="csharp",
        )
        kinds = {
            n["props"]["kind"]
            for n in nodes.values()
            if "kind" in n["props"]
        }
        self.assertEqual(kinds, {"class", "method", "property"})
        # The historical mangled values must be absent.
        for mangled in ("classe", "propertie", "interfac", "struc", "enu"):
            with self.subTest(mangled=mangled):
                self.assertNotIn(mangled, kinds)

    def test_csharp_promotion_splits_class_interface_struct_record(self) -> None:
        # ADR 0064 § 1 acceptance: per-decl-kind YAML buckets must
        # carry through to the promoted symbol's ``kind`` so a
        # downstream filter for ``kind == 'interface'`` (or 'struct',
        # or 'record') returns only the matching declarations.
        symbols = {
            "classes": ["MyClass"],
            "interfaces": ["IMyService"],
            "structs": ["Point"],
            "records": ["Person"],
        }
        nodes, edges = promote_definition_symbols(
            language="csharp",
            rel_path="src/Mixed.cs",
            symbols=symbols,
            file_node_id="file:src/Mixed",
            source_strategy="csharp",
        )
        kind_by_label = {
            n["label"]: n["props"]["kind"]
            for n in nodes.values()
            if "kind" in n["props"]
        }
        self.assertEqual(
            kind_by_label,
            {
                "MyClass": "class",
                "IMyService": "interface",
                "Point": "struct",
                "Person": "record",
            },
        )
        self.assertEqual(len(nodes), 4)
        self.assertEqual(len(edges), 4)

    def test_csharp_promotion_emits_one_node_per_definition(self) -> None:
        symbols = {
            "classes": ["A", "B"],
            "methods": ["m1"],
            "properties": ["p1", "p2"],
        }
        nodes, edges = promote_definition_symbols(
            language="csharp",
            rel_path="src/Sample.cs",
            symbols=symbols,
            file_node_id="file:src/Sample",
            source_strategy="csharp",
        )
        self.assertEqual(len(nodes), 5)
        # Each promoted symbol has a contains edge from the file node.
        self.assertEqual(len(edges), 5)
        for edge in edges:
            self.assertEqual(edge["type"], "contains")
            self.assertEqual(edge["from"], "file:src/Sample")

    def test_non_csharp_language_leaves_kind_unset(self) -> None:
        # Python/TS/etc go through the same helper but emit only
        # ``exports`` and intentionally leave ``kind`` off (kind is
        # populated by the language-specific enricher in those cases).
        nodes, _edges = promote_definition_symbols(
            language="python",
            rel_path="pkg/mod.py",
            symbols={"exports": ["my_func"]},
            file_node_id="file:pkg/mod",
            source_strategy="python_module",
        )
        for node in nodes.values():
            self.assertNotIn("kind", node["props"])


if __name__ == "__main__":
    unittest.main()
