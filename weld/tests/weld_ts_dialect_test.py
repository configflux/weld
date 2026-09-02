"""Which tree-sitter grammar parses a TypeScript file (ADR 0142 D4, bd lrnx1.5).

``tree_sitter_typescript`` ships two grammars and weld picked one of them for
every file, so a Next.js page -- JSX in a ``.tsx`` file wired as ``language:
typescript``, which is what ``wd init`` writes -- parsed as broken TypeScript
and contributed no symbol at all. These tests pin the dispatch that fixes it:
the table that chooses the grammar, the alias rows that make ``tsx`` name a
module that exists, the cache keys that keep two grammars apart, and the
extraction itself.

It really parses. The grammars are pinned in ``requirements_lock.txt`` and
declared on this target, so a ``.tsx`` file here is read by the same grammar a
user's is -- which matters more here than anywhere: the gap was invisible to
every mocked test in the tree precisely because a mock never has an opinion
about JSX.

The re-export half of the same task is next door in
``weld_typescript_reexports_test``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies import _ts_dialect, _ts_parse, tree_sitter, typescript_exports

#: A Next.js page, minus Next.js: a default-exported component whose body is
#: JSX. The plain TypeScript grammar reports an error on ``<main>`` and error
#: recovery takes ``Home`` with it.
_PAGE_TSX = """\
import { formatPrice } from "./money";

export default function Home() {
  return <main>{formatPrice(1299)}</main>;
}
"""

_MONEY_TS = """\
export function formatPrice(cents: number): string {
  return `${cents}`;
}
"""


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _extract(root: Path, **overrides) -> tuple[dict, list]:
    source = {
        "glob": "**/*.{ts,tsx}",
        "type": "file",
        "strategy": "tree_sitter",
        "language": "typescript",
    }
    source.update(overrides)
    result = tree_sitter.extract(root, source, {})
    return result.nodes, result.edges


def _labels(nodes: dict, node_type: str) -> set[str]:
    return {
        str(node.get("label"))
        for node in nodes.values()
        if node.get("type") == node_type
    }


class GrammarVariantTableTest(unittest.TestCase):
    """:func:`_ts_dialect.grammar_variant` picks the grammar, per file."""

    def test_tsx_file_under_the_typescript_entry_takes_the_tsx_grammar(self) -> None:
        self.assertEqual(
            _ts_dialect.grammar_variant("typescript", Path("app/page.tsx")), "tsx"
        )

    def test_ts_file_takes_the_plain_grammar(self) -> None:
        self.assertEqual(
            _ts_dialect.grammar_variant("typescript", Path("lib/money.ts")),
            "typescript",
        )

    def test_a_ts_file_under_a_tsx_entry_is_still_plain_typescript(self) -> None:
        """The suffix decides, not the entry.

        The TSX grammar rejects the ``<T>value`` type assertion the plain one
        accepts, so honouring a blanket ``language: tsx`` for a ``.ts`` file
        would trade one dialect gap for another.
        """
        self.assertEqual(
            _ts_dialect.grammar_variant("tsx", Path("lib/money.ts")), "typescript"
        )

    def test_case_is_normalised(self) -> None:
        self.assertEqual(
            _ts_dialect.grammar_variant("typescript", Path("Widget.TSX")), "tsx"
        )

    def test_every_other_language_is_the_identity(self) -> None:
        """So callers can apply it unconditionally, whatever the language."""
        for language, path in (
            ("python", "pkg/mod.py"),
            ("go", "cmd/main.go"),
            ("javascript", "src/legacy.jsx"),
        ):
            self.assertEqual(
                _ts_dialect.grammar_variant(language, Path(path)), language, path
            )

    def test_canonical_language_folds_tsx_and_leaves_the_rest(self) -> None:
        self.assertEqual(_ts_dialect.canonical_language("tsx"), "typescript")
        self.assertEqual(_ts_dialect.canonical_language("typescript"), "typescript")
        for language in ("python", "go", "rust", "javascript"):
            self.assertEqual(_ts_dialect.canonical_language(language), language)

    def test_both_typescript_strategies_share_one_table(self) -> None:
        """The two TS strategies disagreed once; that disagreement was gap G4."""
        for name in ("Button.tsx", "utils.ts", "Widget.TSX"):
            self.assertEqual(
                typescript_exports._ts_variant_for(Path(name)),
                _ts_dialect.grammar_variant("typescript", Path(name)),
                name,
            )


class TsxGrammarModuleTest(unittest.TestCase):
    """``tsx`` names a module and a package that exist."""

    def test_tsx_resolves_to_the_typescript_grammar_module(self) -> None:
        self.assertEqual(
            _ts_parse.grammar_module_name("tsx"), "tree_sitter_typescript"
        )

    def test_the_install_hint_names_an_installable_package(self) -> None:
        """It named ``tree-sitter-tsx``, which has never existed on PyPI."""
        self.assertEqual(
            _ts_parse.grammar_package_name("tsx"), "tree-sitter-typescript"
        )

    def test_loading_tsx_returns_a_different_grammar_than_typescript(self) -> None:
        plain = _ts_parse.load_ts_language("typescript")
        tsx = _ts_parse.load_ts_language("tsx")
        self.assertIsNotNone(tsx)
        self.assertIsNot(plain, tsx)


class ParseCacheGrammarKeyTest(unittest.TestCase):
    """One language, two grammars, and no cache entry shared between them."""

    def test_a_tsx_file_and_a_ts_file_get_their_own_parse_entries(self) -> None:
        cache = _ts_parse.ParseCache()
        queries = tree_sitter.load_language_queries("typescript")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            page = _write(root, "page.tsx", _PAGE_TSX)
            money = _write(root, "money.ts", _MONEY_TS)
            _ts_parse.parse_file_symbols(page, "typescript", queries, cache=cache)
            _ts_parse.parse_file_symbols(money, "typescript", queries, cache=cache)

            tsx_entry = cache.get_parse(page, "typescript")
            ts_entry = cache.get_parse(money, "typescript")
            self.assertIsNotNone(tsx_entry)
            self.assertIsNotNone(ts_entry)
            self.assertIsNot(tsx_entry.language_obj, ts_entry.language_obj)

    def test_the_call_graph_reader_finds_the_tsx_tree_it_did_not_ask_for(self) -> None:
        """``_ts_call_graph`` asks by weld language and must still get TSX.

        It hands the cache ``"typescript"`` for a ``.tsx`` file, so the key
        derivation has to live in the cache: were it in the caller, the call
        graph would miss, re-parse ``page.tsx`` with the plain grammar, and
        lose every call the JSX body makes.
        """
        cache = _ts_parse.ParseCache()
        queries = tree_sitter.load_language_queries("typescript")
        with tempfile.TemporaryDirectory() as td:
            page = _write(Path(td), "page.tsx", _PAGE_TSX)
            _ts_parse.parse_file_symbols(page, "typescript", queries, cache=cache)
            entry = cache.get_parse(page, "typescript")
            self.assertIsNotNone(entry)
            self.assertEqual(cache.grammar_key_of(entry.language_obj), "tsx")

    def test_a_compiled_query_is_never_reused_across_grammars(self) -> None:
        """A ``Query`` is bound to one ``Language`` and fails silently on another.

        Measured: the plain-TypeScript ``exports`` query run over a TSX tree
        returns no matches and raises nothing -- so a shared query-cache key
        would have cost every ``.tsx`` file its symbols while looking healthy.
        """
        import tree_sitter as ts_mod

        cache = _ts_parse.ParseCache()
        exports = tree_sitter.load_language_queries("typescript")["exports"]
        plain, _ = cache.get_or_load_language(
            "typescript", _ts_parse.load_ts_language, ts_mod
        )
        tsx, _ = cache.get_or_load_language(
            "tsx", _ts_parse.load_ts_language, ts_mod
        )
        for language_arg in ("typescript", "tsx"):
            # Whatever the caller *calls* the language, the query it gets back
            # is the one compiled against the grammar object it passed.
            self.assertIsNot(
                cache.get_or_compile_query(
                    language_arg, "exports", exports, plain, ts_mod
                ),
                cache.get_or_compile_query(
                    language_arg, "exports", exports, tsx, ts_mod
                ),
                language_arg,
            )


class TsxExtractionTest(unittest.TestCase):
    """The gap itself: a JSX component reaching the graph as a symbol."""

    def test_a_default_exported_component_becomes_a_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "app/page.tsx", _PAGE_TSX)
            nodes, _edges = _extract(root)
        self.assertIn("Home", _labels(nodes, "symbol"))
        self.assertEqual(
            [
                node["props"]["exports"]
                for node in nodes.values()
                if node.get("type") == "file"
            ],
            [["Home"]],
        )

    def test_the_jsx_body_no_longer_hides_the_import(self) -> None:
        """Error recovery took the import table down with the declaration."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "app/page.tsx", _PAGE_TSX)
            nodes, _edges = _extract(root)
        imports = [
            node["props"].get("imports_from")
            for node in nodes.values()
            if node.get("type") == "file"
        ]
        self.assertEqual(imports, [['"./money"']])

    def test_a_ts_sibling_in_the_same_run_still_extracts(self) -> None:
        """Two grammars in one pass, both memoised, neither shadowing the other."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "app/page.tsx", _PAGE_TSX)
            _write(root, "lib/money.ts", _MONEY_TS)
            nodes, _edges = _extract(root)
        self.assertEqual(
            _labels(nodes, "symbol") & {"Home", "formatPrice"},
            {"Home", "formatPrice"},
        )

    def test_language_tsx_is_a_working_spelling_of_the_same_thing(self) -> None:
        """It resolved to no grammar module and no query file, and so to nothing."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "app/page.tsx", _PAGE_TSX)
            nodes, _edges = _extract(root, glob="**/*.tsx", language="tsx")
        self.assertIn("Home", _labels(nodes, "symbol"))

    def test_a_tsx_entry_keeps_the_one_typescript_symbol_namespace(self) -> None:
        """``symbol:tsx:...`` beside ``symbol:typescript:...`` would strand
        every cross-dialect call and import resolution on the seam."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "app/page.tsx", _PAGE_TSX)
            nodes, _edges = _extract(root, glob="**/*.tsx", language="tsx")
        symbols = {
            node_id: node
            for node_id, node in nodes.items()
            if node.get("type") == "symbol"
        }
        self.assertTrue(symbols)
        for node_id, node in sorted(symbols.items()):
            self.assertTrue(node_id.startswith("symbol:typescript:"), node_id)
            self.assertEqual(node["props"].get("language"), "typescript", node_id)


if __name__ == "__main__":
    unittest.main()
