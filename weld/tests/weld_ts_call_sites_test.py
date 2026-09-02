"""What the TypeScript call-graph pass records about a call site (bd lrnx1.3).

Driven through :func:`weld.strategies._ts_call_graph.extract_call_edges` with
the **real** TypeScript grammar rather than against the helpers in isolation,
because both facts under test are facts about a parse tree: which ancestor of
a captured identifier carries a name, and which import statement bound that
name. A mocked node graph would assert the helper's own control flow and prove
nothing about either -- which is how the whole of ADR 0142's finding stayed
invisible to a green test suite.

The fixture is written so every attribution outcome appears exactly once:
an exported function, an exported ``const`` holding an arrow (the shape a Node
codebase is mostly made of, and the one where the name lives a level above the
anonymous function), an exported class around a method the ``exports`` query
does not capture on its own, a *non*-exported function, and a call at module
level inside an anonymous callback. The last two are the refusals: neither has
a symbol node for an edge to start at, so both keep the file sentinel.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._ts_call_graph import extract_call_edges
from weld.strategies._ts_call_sites import TS_IMPORT_PROP
from weld.strategies.tree_sitter import load_language_queries

MODULE = "src.app"
FILE_CALLER = f"symbol:typescript:{MODULE}:<file>"

#: Every shape the attribution walk has to tell apart, in one file. ``CODE`` is
#: called rather than merely bound so the aliased-import case reaches a call
#: edge; ``util.helper()`` is a namespace call, whose captured identifier is
#: the member and not the binding, so it must carry no hint.
SOURCE = """\
import { formatPrice, CURRENCY as CODE } from "@acme/shared";
import banner from "./banner";
import * as util from "./util";

export function handler(): string {
  return formatPrice(1) + CODE();
}

export const render = () => formatPrice(2);

function unexported(): string {
  return String(formatPrice(3));
}

export class Box {
  show(): string {
    return String(formatPrice(4));
  }
}

register(() => banner() + util.helper());
"""


class TsCallSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        path = Path(cls._tmp.name) / "app.ts"
        path.write_text(SOURCE, encoding="utf-8")
        queries = load_language_queries("typescript")
        cls.nodes, cls.edges = extract_call_edges(
            path, "src/app.ts", "typescript", queries,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _callers_of(self, callee: str) -> set[str]:
        target = f"symbol:unresolved:{callee}"
        return {
            str(edge["from"])
            for edge in self.edges
            if edge.get("to") == target
        }

    def _hint(self, callee: str) -> dict | None:
        for edge in self.edges:
            if edge.get("to") == f"symbol:unresolved:{callee}":
                return (edge.get("props") or {}).get(TS_IMPORT_PROP)
        return None

    def test_the_grammar_was_really_loaded(self) -> None:
        """Guard the suite: no captures means every assertion below is vacuous.

        ``extract_call_edges`` swallows a missing grammar and returns empty
        results by contract (ADR 0002), so without this the whole file would
        pass green on an interpreter with no TypeScript grammar -- asserting
        that nothing was found about nothing.
        """
        self.assertIn(FILE_CALLER, self.nodes)
        self.assertTrue(self.edges, "the calls query captured nothing at all")

    def test_a_call_is_attributed_to_the_export_it_is_written_inside(self) -> None:
        self.assertIn(
            f"symbol:typescript:{MODULE}:handler", self._callers_of("formatPrice"),
        )

    def test_an_arrow_held_by_an_exported_const_is_named_by_the_const(self) -> None:
        """The name is on the ``variable_declarator``, not on the arrow."""
        self.assertIn(
            f"symbol:typescript:{MODULE}:render", self._callers_of("formatPrice"),
        )

    def test_a_method_is_attributed_to_the_class_the_graph_holds(self) -> None:
        """``show`` mints no symbol, so the nearest one that does is ``Box``.

        Coarser than the method, and deliberately so: attributing to ``show``
        would start an edge at an id no node in the graph carries.
        """
        self.assertIn(
            f"symbol:typescript:{MODULE}:Box", self._callers_of("formatPrice"),
        )

    def test_calls_outside_any_exported_definition_keep_the_file_sentinel(self) -> None:
        """The two refusals: a non-exported function and a module-level callback.

        ``unexported`` is a named ancestor the ``exports`` query never
        captured, and the ``register`` callback has no named ancestor at all.
        Both fall back to the file, which is what the graph can prove -- and
        the file sentinel is reached by exactly those two, so the fallback is
        neither over- nor under-applied.
        """
        self.assertIn(FILE_CALLER, self._callers_of("formatPrice"))
        self.assertIn(FILE_CALLER, self._callers_of("register"))

    def test_every_caller_of_the_fixture_is_accounted_for(self) -> None:
        """Stated as an equality: a subset check would pass a lost attribution."""
        self.assertEqual(
            self._callers_of("formatPrice"),
            {
                f"symbol:typescript:{MODULE}:handler",
                f"symbol:typescript:{MODULE}:render",
                f"symbol:typescript:{MODULE}:Box",
                FILE_CALLER,
            },
        )

    def test_a_named_import_is_recorded_on_the_edge(self) -> None:
        self.assertEqual(
            self._hint("formatPrice"),
            {
                "local": "formatPrice",
                "name": "formatPrice",
                "from": "@acme/shared",
                "target": "",
            },
        )

    def test_an_aliased_import_records_both_spellings(self) -> None:
        """``CODE`` is what the call says; ``CURRENCY`` is what to look up."""
        self.assertEqual(
            self._hint("CODE"),
            {
                "local": "CODE",
                "name": "CURRENCY",
                "from": "@acme/shared",
                "target": "",
            },
        )

    def test_default_and_namespace_imports_record_nothing(self) -> None:
        """Both are out of scope, and silence is the recorded decision.

        A default import's local name is not a name any module exports, and a
        namespace call captures the member rather than the binding -- so a
        hint for either could never resolve, and one that could never resolve
        is a prop with no reader.
        """
        self.assertIsNone(self._hint("banner"))
        self.assertIsNone(self._hint("helper"))

    def test_one_definition_calling_the_same_name_twice_yields_one_edge(self) -> None:
        """Dedup is per ``(caller, callee)``, so the pair appears exactly once."""
        pairs = [
            (edge["from"], edge["to"])
            for edge in self.edges
            if edge["to"] == "symbol:unresolved:formatPrice"
        ]
        self.assertEqual(len(pairs), len(set(pairs)))


if __name__ == "__main__":
    unittest.main()
