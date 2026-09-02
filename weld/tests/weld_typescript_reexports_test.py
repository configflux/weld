"""Barrel files as graph evidence (ADR 0142 D5, bd lrnx1.5).

``packages/shared/index.ts`` -- three lines of ``export { x } from "./money"``
and nothing else -- is what a package's ``main`` points at, so every read that
arrives through the package name arrives *there*. It reached the graph as a
bare anchor: no exports, no imports, and one outbound edge, to its own
``<file>`` call-graph sentinel. The module it forwards was unreachable from
the entry point that publishes it.

What these tests pin is the shape of the fix as much as its effect, because
the tempting version of it is wrong: fold the re-exported names into
``props.exports`` and the barrel is visible again -- and also mints a second
``definite`` ``symbol:...:formatPrice`` claiming a definition three lines from
the real one, which is worse than the silence. So the names land on
``props.reexports``, the specifier joins ``props.imports_from`` where the
closure already knows how to resolve it, and the definitions stay where they
are defined.

Real grammars, declared on the target: the queries here are the subject, and a
mocked parser has no opinion about ``export_specifier``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies import tree_sitter
from weld.strategies._ts_file_props import (
    has_reexport_evidence,
    merge_reexport_sources,
    reexported_names,
    reexport_sources,
)

_BARREL = """\
export { CURRENCY, formatPrice } from "./money";
export type { Money } from "./money";
"""

_MONEY = """\
export const CURRENCY = "USD";

export interface Money {
  cents: number;
}

export function formatPrice(cents: number): string {
  return `${cents} ${CURRENCY}`;
}
"""

#: A local ``export { a }`` publishes a name this file *defines*; it is not a
#: forward, and counting it as one would put a re-export claim on every module
#: that groups its exports at the bottom.
_LOCAL_EXPORT_CLAUSE = """\
const a = 1;
export { a };
export { a as b };
"""


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _parse(body: str, suffix: str = ".ts") -> dict[str, list[str]]:
    from weld.strategies._ts_parse import parse_file_symbols

    queries = tree_sitter.load_language_queries("typescript")
    with tempfile.TemporaryDirectory() as td:
        path = _write(Path(td), f"subject{suffix}", body)
        return parse_file_symbols(path, "typescript", queries)


def _extract(root: Path) -> tuple[dict, list]:
    result = tree_sitter.extract(
        root,
        {
            "glob": "**/*.ts",
            "type": "file",
            "strategy": "tree_sitter",
            "language": "typescript",
        },
        {},
    )
    return result.nodes, result.edges


class ReexportQueryTest(unittest.TestCase):
    """What the two new query buckets capture, and what they refuse."""

    def test_named_and_type_only_reexports_are_captured(self) -> None:
        symbols = _parse(_BARREL)
        self.assertEqual(
            sorted(reexported_names(symbols)),
            ["CURRENCY", "Money", "formatPrice"],
        )

    def test_the_source_module_is_captured_once_per_module(self) -> None:
        """Two statements, one dependency: the specifier is deduped."""
        self.assertEqual(reexport_sources(_parse(_BARREL)), ['"./money"'])

    def test_a_renamed_reexport_publishes_the_alias(self) -> None:
        """``export { formatPrice as fmt }`` publishes ``fmt``.

        A consumer importing ``formatPrice`` from this module gets nothing,
        so recording the source name would be a claim about a surface that
        does not exist.
        """
        symbols = _parse('export { formatPrice as fmt, CURRENCY } from "./money";\n')
        self.assertEqual(sorted(reexported_names(symbols)), ["CURRENCY", "fmt"])

    def test_a_star_reexport_yields_a_source_and_no_names(self) -> None:
        """The commonest barrel form, and the reason evidence is either half."""
        symbols = _parse('export * from "./money";\n')
        self.assertEqual(reexported_names(symbols), [])
        self.assertEqual(reexport_sources(symbols), ['"./money"'])
        self.assertTrue(has_reexport_evidence(symbols))

    def test_a_local_export_clause_is_not_a_reexport(self) -> None:
        symbols = _parse(_LOCAL_EXPORT_CLAUSE)
        self.assertEqual(reexported_names(symbols), [])
        self.assertEqual(reexport_sources(symbols), [])
        self.assertFalse(has_reexport_evidence(symbols))

    def test_a_file_that_defines_and_forwards_reports_both_separately(self) -> None:
        symbols = _parse(
            'import { x } from "./x";\n'
            "export function own(): void {}\n"
            'export { y } from "./y";\n'
        )
        self.assertEqual(symbols.get("exports"), ["own"])
        self.assertEqual(reexported_names(symbols), ["y"])
        self.assertEqual(
            merge_reexport_sources(symbols), ['"./x"', '"./y"'],
        )

    def test_a_tsx_barrel_is_read_by_the_tsx_grammar_too(self) -> None:
        """Component barrels are ``.tsx`` as often as they are ``.ts``."""
        symbols = _parse('export { Button } from "./Button";\n', suffix=".tsx")
        self.assertEqual(reexported_names(symbols), ["Button"])


class BarrelNodeTest(unittest.TestCase):
    """The node a barrel now mints, and what it does not claim."""

    def _barrel_graph(self) -> tuple[dict, list]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "packages/shared/index.ts", _BARREL)
            _write(root, "packages/shared/money.ts", _MONEY)
            return _extract(root)

    def test_the_barrel_gets_its_own_file_node(self) -> None:
        nodes, _edges = self._barrel_graph()
        self.assertIn("file:packages/shared/index", nodes)
        self.assertEqual(
            nodes["file:packages/shared/index"]["props"]["source_strategy"],
            "tree_sitter",
        )

    def test_the_forwarded_module_lands_in_imports_from(self) -> None:
        """The half the closure reads: this is what becomes ``depends_on``."""
        nodes, _edges = self._barrel_graph()
        self.assertEqual(
            nodes["file:packages/shared/index"]["props"]["imports_from"],
            ['"./money"'],
        )

    def test_the_forwarded_names_land_on_their_own_prop(self) -> None:
        nodes, _edges = self._barrel_graph()
        props = nodes["file:packages/shared/index"]["props"]
        self.assertEqual(props["reexports"], ["CURRENCY", "Money", "formatPrice"])
        self.assertEqual(props["exports"], [])

    def test_the_barrel_defines_nothing(self) -> None:
        """No ``symbol:`` node may claim the barrel as its defining file."""
        nodes, _edges = self._barrel_graph()
        claimed = sorted(
            node_id
            for node_id, node in nodes.items()
            if node.get("type") == "symbol"
            and node["props"].get("file") == "packages/shared/index.ts"
            and node["props"].get("qualname") != "<file>"
        )
        self.assertEqual(claimed, [])

    def test_the_definitions_stay_in_the_module_that_holds_them(self) -> None:
        nodes, _edges = self._barrel_graph()
        defined = {
            str(node.get("label"))
            for node in nodes.values()
            if node.get("type") == "symbol"
            and node["props"].get("file") == "packages/shared/money.ts"
        }
        self.assertEqual(
            defined & {"CURRENCY", "Money", "formatPrice"},
            {"CURRENCY", "Money", "formatPrice"},
        )

    def test_a_file_with_neither_definitions_nor_forwards_stays_out(self) -> None:
        """The skip is narrowed, not removed: an implementation-only module
        with no exported surface still contributes no file node."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "src/private.ts", "const internal = 1;\n")
            nodes, _edges = _extract(root)
        self.assertEqual(
            [n for n in nodes if n.startswith("file:")], []
        )

    def test_a_module_that_only_groups_its_own_exports_claims_no_reexports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "src/grouped.ts", _LOCAL_EXPORT_CLAUSE)
            nodes, _edges = _extract(root)
        for node in nodes.values():
            self.assertNotIn("reexports", node.get("props", {}))


if __name__ == "__main__":
    unittest.main()
