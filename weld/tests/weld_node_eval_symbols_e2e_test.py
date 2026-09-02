"""Gaps G4, G5 and G6: what the corpus's source files contribute, or do not.

Three files in the fixture reached the graph as an address and nothing else --
a Next.js page whose default-exported component was missing, a barrel whose
re-exports left no trace, and a CommonJS module that yields no symbols at all
while the README's tier table promises "exports, classes, imports" for
JavaScript (ADR 0142 D6, bd lrnx1.5 and lrnx1.6).

All three are now fixed and their markers flipped, each by the task that owned
it (ADR 0142 D7): G4 by per-file TSX grammar dispatch and G5 by re-export
evidence (both bd lrnx1.5, ADR 0142 D4/D5), G6 by shipping a JavaScript query
file and promoting its definitions (bd lrnx1.6, ADR 0142 D6). The assertions
are unchanged from the red landing; only the markers are gone.

They run against the hand-wired configuration
(:data:`weld.tests._node_eval_corpus.WIRED_DISCOVER_YAML`), which claims every
dialect the repo contains. That is not the fixture papering over gap G1: a
probe run on the stock config would be red because ``wd init`` never wired the
file, which is G1's finding, and three probes all reproducing one gap would
leave the other two unproven.

Beside them is a pass-today assurance probe: ``money.ts``'s four named exports
-- a const, an interface, a function and a class -- reach the graph as
definite first-party TypeScript symbols. That is the extraction the whole
corpus rests on, it is green today, and the next silent loss of it should trip
this gate rather than a customer.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.tests._graph_invariants import graph_nodes
from weld.tests._node_eval_corpus import (
    BARREL_FILE,
    BARREL_REEXPORT_TARGET,
    DEFAULT_EXPORT_COMPONENTS,
    FORMAT_PRICE_FILE,
    LEGACY_JS_FILE,
    LEGACY_JS_FUNCTION,
    LEGACY_JS_REQUIRED_PACKAGE,
    MONEY_NAMED_EXPORTS,
)
from weld.tests._node_eval_e2e_harness import (
    NodeEvalWorkspace,
    edges_from,
    file_node_id,
    node_props,
    symbols_in_file,
)
#: The bd issue that owns each fix -- issue-id suffixes, the full ledger ids
#: being tracker-internal. G4 and G5 share an owner: default exports and
#: barrels are one task because they are one extraction pass.
#:
#: Kept in full now that all three are green: the inventory guard compares this
#: table against its own independent statement of who owns each gap, and a
#: table that shed its entries as they were fixed would have nothing left to
#: compare.
_BD_FIXES = {
    "G4": "lrnx1.5",
    "G5": "lrnx1.5",
    "G6": "lrnx1.6",
}

_WS: NodeEvalWorkspace | None = None
_TMP: tempfile.TemporaryDirectory | None = None


def setUpModule() -> None:
    global _WS, _TMP
    _TMP = tempfile.TemporaryDirectory()
    _WS = NodeEvalWorkspace.monorepo(Path(_TMP.name))
    _WS.bootstrap_wired()


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def workspace() -> NodeEvalWorkspace:
    assert _WS is not None, "setUpModule did not run"
    return _WS


class ExtractionProbes(unittest.TestCase):
    ws: NodeEvalWorkspace
    graph: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()
        cls.graph = cls.ws.graph()

    def _labels_in(self, rel_path: str) -> set[str]:
        return {
            str(node.get("label"))
            for node in symbols_in_file(self.graph, rel_path).values()
        }

    def test_g4_every_default_exported_component_is_a_symbol(self) -> None:
        """A Next.js page or layout contributes the component it default-exports.

        Both files, not only the broken one. ``layout.tsx``'s ``RootLayout``
        survived the plain-TypeScript parse of a ``.tsx`` file purely through
        where the JSX error recovery landed; asserting only ``page.tsx``
        would have let that accident regress unnoticed while the gap beside
        it was being fixed.

        Root cause, measured rather than assumed: ``export default function
        Home()`` in a plain ``.ts`` file *was* extracted all along, so this
        was never "default exports are invisible" -- the ``tree_sitter``
        strategy had no TSX grammar dispatch (``language: tsx`` did not even
        resolve, the TSX grammar living inside ``tree_sitter_typescript``),
        so JSX was parsed as broken TypeScript and the export was lost with
        it. Fixed in bd lrnx1.5 by choosing the grammar per file rather than
        per source entry (:mod:`weld.strategies._ts_dialect`).
        """
        missing = sorted(
            f"{name} ({rel_path})"
            for name, rel_path in DEFAULT_EXPORT_COMPONENTS.items()
            if name not in self._labels_in(rel_path)
        )
        self.assertEqual(
            missing, [],
            "a default-exported component reaches the graph as nothing: "
            f"{missing}",
        )

    def test_g5_the_barrel_keeps_its_file_node_and_its_re_export_evidence(
        self,
    ) -> None:
        """A barrel is the package entry point, so it must lead somewhere.

        Two halves, and the first is what makes the second meaningful: the
        barrel keeps a file node (true today), *and* something leaves that
        node for the module it re-exports from. An entry point with no
        outbound evidence is a dead end for every read that arrives through
        the package name -- which, this being ``main``, is all of them.

        The evidence is asserted by where it *lands*, not by an edge type: the
        fix may spell it ``re_exports``, ``imports_from`` or ``depends_on``,
        and a probe keyed on a guess about the spelling would have gone red on
        a correct fix. It landed as ``depends_on``, because bd lrnx1.5 gave
        the re-export specifier to ``props.imports_from`` and let the closure
        resolve it exactly as it resolves a plain relative import.
        """
        barrel = file_node_id(self.graph, BARREL_FILE)
        nodes = graph_nodes(self.graph)
        reached = {
            str(node_props(nodes.get(str(edge.get("to")), {})).get("file"))
            for edge in edges_from(self.graph, barrel)
        }
        self.assertIn(
            BARREL_REEXPORT_TARGET, reached,
            f"nothing leaves {barrel} for {BARREL_REEXPORT_TARGET}; the "
            f"barrel reaches {sorted(r for r in reached if r != 'None')}",
        )

    def test_g6_javascript_delivers_symbols_and_require_evidence(self) -> None:
        """The Tier-2 surface the README already claims for JavaScript.

        A plain function declaration becomes a symbol, and a ``require`` of a
        package becomes dependency evidence on the file that requires it. The
        required name is the third-party ``express`` rather than the
        first-party ``@acme/shared`` on purpose: a first-party require would
        also be gap G3, and then neither probe would be red for its own
        reason.

        Root cause, measured rather than assumed: there was no
        ``weld/languages/javascript.yaml`` at all, and
        ``tree_sitter.extract()`` loads a language's queries before it
        resolves a single file -- so a ``language: javascript`` source entry
        raised ``FileNotFoundError``, was caught, and returned an empty result
        with a warning. legacy.js's file node came from the express strategy's
        boundary placeholder, not from any JavaScript extraction. Fixed in bd
        lrnx1.6 by shipping the query file and adding ``javascript`` to
        ``_ts_definitions.T1_DEFINITION_LANGUAGES``; the ``require`` evidence
        needed no new edge code, the per-import package pass having always
        claimed JavaScript.
        """
        self.assertIn(
            LEGACY_JS_FUNCTION, self._labels_in(LEGACY_JS_FILE),
            f"{LEGACY_JS_FILE} contributes no symbol for its own function; it "
            f"has {sorted(self._labels_in(LEGACY_JS_FILE))}",
        )

        legacy = file_node_id(self.graph, LEGACY_JS_FILE)
        nodes = graph_nodes(self.graph)
        required = {
            str(node_props(nodes.get(str(edge.get("to")), {})).get("name"))
            for edge in edges_from(self.graph, legacy)
        }
        self.assertIn(
            LEGACY_JS_REQUIRED_PACKAGE, required,
            f"the require() in {LEGACY_JS_FILE} yields no dependency "
            f"evidence; {legacy} reaches "
            f"{sorted(r for r in required if r != 'None')}",
        )

    # -- pass-today assurance ---------------------------------------------

    def test_typescript_named_exports_reach_the_graph(self) -> None:
        """Every named export of ``money.ts`` is a definite first-party symbol.

        Green today, and the load-bearing green: gap G2's probe needs a
        definition to ask "who calls me" about, and gap G3's needs a file for
        a first-party name to bind to. Asserted as an equality rather than a
        subset -- "the symbols that were found are correct" is true of a run
        that found one of four.

        ``kind`` is deliberately not asserted: TypeScript symbols carry none,
        and ``tools/tier_check_kinds`` records that as a limitation rather
        than a breach. See
        :data:`weld.tests._node_eval_corpus.MONEY_NAMED_EXPORTS`.
        """
        symbols = symbols_in_file(self.graph, FORMAT_PRICE_FILE)
        self.assertEqual(
            {str(node.get("label")) for node in symbols.values()},
            set(MONEY_NAMED_EXPORTS),
            f"the named exports of {FORMAT_PRICE_FILE} are not the four the "
            "file declares",
        )
        for node_id, node in sorted(symbols.items()):
            props = node_props(node)
            self.assertEqual(props.get("language"), "typescript", node_id)
            self.assertEqual(props.get("origin"), "project", node_id)
            self.assertEqual(props.get("confidence"), "definite", node_id)


if __name__ == "__main__":
    unittest.main()
