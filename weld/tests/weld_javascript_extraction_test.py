"""What a JavaScript file contributes to the graph (ADR 0142 D6, bd lrnx1.6).

Before this, ``weld/languages/javascript.yaml`` did not exist. ``extract()``
loads a language's queries before it resolves a single file, so a ``language:
javascript`` source entry raised ``FileNotFoundError``, was caught, and
returned an empty result plus a warning -- while the README's platform table
claimed JavaScript delivered "exports, classes, imports". Every ``.js`` file in
every graph reached it as nothing.

It really parses, and here that is the point rather than a detail. The whole
gap lived on a route no in-process test entered, and the two facts this file
pins hardest are facts only a real grammar has an opinion about:

* **Text predicates are applied.** ``imports`` captures ``require("express")``
  through a ``(#eq? @_require "require")`` predicate -- the first predicate in
  any weld language file. If a py-tree-sitter release stopped evaluating them
  inside ``QueryCursor.matches``, every one-string call in the file
  (``describe("some suite", ...)``) would silently become an import specifier.
  ``test_a_string_argument_to_any_other_call_is_not_an_import`` is that alarm.
* **JSX parses natively.** ``.jsx`` needs no dialect dispatch the way ``.tsx``
  does, which is a claim about the grammar and not about weld.

The system-level half is ``weld_node_eval_symbols_e2e_test``'s G6 probe, which
runs the real CLI over a Node workspace.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tree_sitter as ts_bindings

from weld.strategies import _ts_call_sites, _ts_file_props, _ts_parse, tree_sitter

#: CommonJS from top to bottom, plus the shapes that must NOT be mistaken for
#: definitions: two ``require`` bindings and a non-``require`` call carrying a
#: string argument.
_LEGACY_JS = """\
const express = require("express");
const { CURRENCY } = require("@acme/shared");

describe("some suite", () => {});

const router = express.Router();

function renderOrder(order) {
  function innerHelper() {}
  return { id: order.id, currency: CURRENCY, innerHelper };
}

class OrderView {}

module.exports = { router, renderOrder, view: OrderView };
exports.CURRENCY = CURRENCY;
module.exports.late = 1;
"""

#: The ESM half of the language, which a modern ``.js`` file is just as likely
#: to be written in as the CommonJS one above.
_MODERN_JS = """\
import express from "express";
import { formatPrice as fmt } from "@acme/shared";
export { helper } from "./helper";
export * from "./star";

export function namedExport() {}
export default function Home() {}
export class Widget {}
export const VALUE = 1;
export function* gen() {}
"""

#: A CommonJS re-export facade: declares nothing, and is still the file
#: ``main`` points at.
_FACADE_JS = """\
module.exports = require("./impl");
"""

#: JSX in a ``.jsx`` file. The JavaScript grammar reads it natively.
_COMPONENT_JSX = """\
export default function Card() {
  return <article className="card">hi</article>;
}
"""

_PACKAGE_JSON = """\
{"name": "@acme/api", "dependencies": {"express": "4.21.1"}}
"""


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _symbols(body: str, rel: str = "src/legacy.js") -> dict[str, list[str]]:
    """Parse *body* with the shipped JavaScript queries."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), rel, body)
        return tree_sitter._parse_file_symbols(
            path, "javascript", tree_sitter.load_language_queries("javascript"),
        )


def _extract(root: Path, **overrides) -> tuple[dict, list]:
    source = {
        "glob": "**/*.{js,jsx,mjs,cjs}",
        "type": "file",
        "strategy": "tree_sitter",
        "language": "javascript",
    }
    source.update(overrides)
    result = tree_sitter.extract(root, source, {})
    return result.nodes, result.edges


def _props(nodes: dict, node_id: str) -> dict:
    node = nodes.get(node_id)
    assert node is not None, f"{node_id} not in {sorted(nodes)}"
    return dict(node.get("props") or {})


def _js_grammar() -> object:
    return ts_bindings.Language(_ts_parse.load_ts_language("javascript"))


class JavaScriptQueryFileTest(unittest.TestCase):
    """The buckets ``weld/languages/javascript.yaml`` declares."""

    def test_every_declared_query_compiles_against_the_grammar(self) -> None:
        """A query naming an unknown node type is swallowed, not raised.

        ``parse_file_symbols`` catches per-query failures and returns an empty
        list for that bucket, so a pattern naming a production the JavaScript
        grammar does not have -- every TypeScript type node, for a start --
        reaches users as a bucket that is simply always empty. Compiling each
        one here is what turns that silence into a failure.
        """
        grammar = _js_grammar()
        for name, source in tree_sitter.load_language_queries("javascript").items():
            with self.subTest(query=name):
                ts_bindings.Query(grammar, source)

    def test_named_imports_bind_their_local_name_to_the_module(self) -> None:
        """``import_bindings`` is read by the call-graph pass, not by ``symbols``.

        Its captures are ``@specifier`` / ``@source``, so it never appears in
        ``parse_file_symbols`` output at all and would look "declared and
        empty" to any check that read only that. It is exercised through its
        real reader instead, which also pins ``javascript``'s membership of
        ``weld.strategies._ts_call_sites.CALL_SITE_LANGUAGES`` (ADR 0142 D2).
        """
        grammar = _js_grammar()
        tree = ts_bindings.Parser(grammar).parse(_MODERN_JS.encode())
        self.assertEqual(
            _ts_call_sites.import_bindings(
                tree, "javascript", grammar,
                tree_sitter.load_language_queries("javascript"), ts_bindings,
            ),
            {"fmt": ("formatPrice", "@acme/shared")},
        )

    def test_definitions_are_the_module_surface_not_its_bindings(self) -> None:
        """``exports`` is what the module declares at program scope.

        ``express`` and ``router`` are ``const`` bindings of imported values;
        promoting them would claim this file defines the express router.
        ``innerHelper`` is nested, and JavaScript nests named functions
        constantly.
        """
        self.assertEqual(
            sorted(_symbols(_LEGACY_JS).get("exports", [])),
            ["OrderView", "renderOrder"],
        )

    def test_esm_declarations_are_definitions_too(self) -> None:
        """Named, default, class, const and generator export forms."""
        self.assertEqual(
            sorted(_symbols(_MODERN_JS).get("exports", [])),
            ["Home", "VALUE", "Widget", "gen", "namedExport"],
        )

    def test_classes_are_reported_separately(self) -> None:
        self.assertEqual(_symbols(_LEGACY_JS).get("classes"), ["OrderView"])
        self.assertEqual(_symbols(_MODERN_JS).get("classes"), ["Widget"])

    def test_require_and_import_both_yield_import_specifiers(self) -> None:
        """The quote characters survive; ``_strip_quotes`` removes them later."""
        self.assertEqual(
            _symbols(_LEGACY_JS).get("imports"),
            ['"express"', '"@acme/shared"'],
        )
        self.assertEqual(
            _symbols(_MODERN_JS).get("imports"),
            ['"express"', '"@acme/shared"'],
        )

    def test_a_string_argument_to_any_other_call_is_not_an_import(self) -> None:
        """The ``#eq?`` predicate contract, stated as its failure mode.

        Drop the predicate -- or run against a binding that stops evaluating
        predicates inside ``QueryCursor.matches`` -- and ``describe("some
        suite", ...)`` lands in ``imports_from`` beside express.
        """
        self.assertNotIn('"some suite"', _symbols(_LEGACY_JS).get("imports", []))

    def test_commonjs_publication_is_captured_in_every_written_form(self) -> None:
        """``module.exports = {}`` shorthand and pairs, ``exports.x``, ``.late``."""
        self.assertEqual(
            sorted(_symbols(_LEGACY_JS).get("commonjs_exports", [])),
            ["CURRENCY", "late", "renderOrder", "router", "view"],
        )

    def test_a_bare_module_exports_assignment_publishes_that_name(self) -> None:
        symbols = _symbols("function f() {}\nmodule.exports = f;\n")
        self.assertEqual(symbols.get("commonjs_exports"), ["f"])

    def test_reexports_carry_their_names_and_their_sources(self) -> None:
        symbols = _symbols(_MODERN_JS)
        self.assertEqual(symbols.get("reexports"), ["helper"])
        self.assertEqual(
            symbols.get("reexport_sources"), ['"./helper"', '"./star"'],
        )

    def test_a_module_exports_require_is_a_re_export_source(self) -> None:
        """``module.exports = require("./impl")`` is ``export * from "./impl"``.

        It names nothing, forwards everything, and is what a package's
        entry-point facade looks like in CommonJS -- so it belongs with the
        star form rather than in ``commonjs_exports``, which is a list of
        published *names*.
        """
        symbols = _symbols(_FACADE_JS)
        self.assertEqual(symbols.get("reexport_sources"), ['"./impl"'])
        self.assertEqual(symbols.get("commonjs_exports"), [])

    def test_calls_reach_both_bare_and_member_call_sites(self) -> None:
        calls = _symbols(_LEGACY_JS).get("calls", [])
        self.assertIn("require", calls)
        self.assertIn("Router", calls)

    def test_jsx_in_a_js_file_needs_no_dialect_dispatch(self) -> None:
        """``.jsx`` is JavaScript, and the grammar knows JSX.

        The ``.tsx`` sibling of this needed a whole grammar-dispatch module
        (:mod:`weld.strategies._ts_dialect`) because ``language_typescript()``
        reads ``<article>`` as an error and error recovery takes the enclosing
        declaration with it. Stated here so the asymmetry is measured rather
        than assumed.
        """
        self.assertEqual(
            _symbols(_COMPONENT_JSX, rel="src/Card.jsx").get("exports"), ["Card"],
        )


class CommonJsPublicationPropsTest(unittest.TestCase):
    """``_ts_file_props``' half: the CommonJS names on the file node."""

    def test_names_are_deduped_in_source_order(self) -> None:
        symbols = {"commonjs_exports": ["a", "b", "a", "", "b"]}
        self.assertEqual(_ts_file_props.commonjs_exported_names(symbols), ["a", "b"])

    def test_publication_evidence_accepts_either_mechanism(self) -> None:
        self.assertTrue(
            _ts_file_props.has_publication_evidence({"commonjs_exports": ["a"]}),
        )
        self.assertTrue(
            _ts_file_props.has_publication_evidence({"reexport_sources": ['"./m"']}),
        )
        self.assertFalse(_ts_file_props.has_publication_evidence({"exports": ["a"]}))

    def test_the_two_mechanisms_stay_two_props(self) -> None:
        """A re-export names a module to follow; a CommonJS export names itself."""
        props: dict = {}
        _ts_file_props.stamp_publication_evidence(
            props, {"reexports": ["b", "a"], "commonjs_exports": ["d", "c"]},
        )
        self.assertEqual(props, {"reexports": ["a", "b"], "commonjs_exports": ["c", "d"]})

    def test_nothing_published_stamps_nothing(self) -> None:
        props: dict = {}
        _ts_file_props.stamp_publication_evidence(props, {"exports": ["a"]})
        self.assertEqual(props, {})


class JavaScriptExtractionTest(unittest.TestCase):
    """End to end through ``tree_sitter.extract()``."""

    def test_a_commonjs_module_yields_symbols_packages_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "package.json", _PACKAGE_JSON)
            _write(root, "src/legacy.js", _LEGACY_JS)
            nodes, edges = _extract(root)

        file_props = _props(nodes, "file:src/legacy")
        self.assertEqual(sorted(file_props["exports"]), ["OrderView", "renderOrder"])
        self.assertEqual(file_props["types"], ["OrderView"])
        self.assertEqual(
            file_props["commonjs_exports"],
            ["CURRENCY", "late", "renderOrder", "router", "view"],
        )
        self.assertEqual(
            file_props["imports_from"], ['"express"', '"@acme/shared"'],
        )

        symbol_id = "symbol:javascript:src.legacy:renderOrder"
        symbol_props = _props(nodes, symbol_id)
        self.assertEqual(symbol_props["file"], "src/legacy.js")
        self.assertEqual(symbol_props["language"], "javascript")
        self.assertEqual(symbol_props["origin"], "project")
        self.assertEqual(symbol_props["confidence"], "definite")
        self.assertIn(
            ("file:src/legacy", symbol_id, "contains"),
            {(e["from"], e["to"], e["type"]) for e in edges},
            "the definition is not attributed to the file that declares it",
        )

    def test_a_required_package_becomes_dependency_evidence(self) -> None:
        """The ``depends_on`` half, which needed no new edge code.

        ``_typescript_tree_sitter._TS_LANGUAGES`` already claimed
        ``javascript``; it simply never ran, because ``imports`` was never
        populated for a language with no query file.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "package.json", _PACKAGE_JSON)
            _write(root, "src/legacy.js", _LEGACY_JS)
            nodes, edges = _extract(root)

        package_id = "package:typescript:express"
        self.assertEqual(_props(nodes, package_id)["name"], "express")
        self.assertEqual(_props(nodes, package_id)["origin"], "external")
        self.assertIn(
            ("file:src/legacy", package_id, "depends_on"),
            {(e["from"], e["to"], e["type"]) for e in edges},
        )

    def test_a_facade_that_declares_nothing_keeps_its_file_node(self) -> None:
        """The CommonJS twin of a barrel (ADR 0142 D5's rule, one language over).

        Without the publication check it drops out of the graph entirely, and
        it is the file every consumer of the package arrives at first.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "index.js", _FACADE_JS)
            nodes, _edges = _extract(root)

        props = _props(nodes, "file:index")
        self.assertEqual(props["exports"], [])
        self.assertNotIn("commonjs_exports", props)
        self.assertNotIn("reexports", props)
        self.assertEqual(props["imports_from"], ['"./impl"'])

    def test_a_missing_query_file_names_no_absolute_install_path(self) -> None:
        """The warning a user saw before this task shipped the query file.

        It reported an absolute path inside their site-packages, which reads
        as a broken install rather than as an unsupported language.
        """
        with self.assertRaises(FileNotFoundError) as caught:
            tree_sitter.load_language_queries("klingon")
        message = str(caught.exception)
        self.assertIn("weld/languages/klingon.yaml", message)
        self.assertNotIn(str(Path(tree_sitter.__file__).parent), message)


if __name__ == "__main__":
    unittest.main()
