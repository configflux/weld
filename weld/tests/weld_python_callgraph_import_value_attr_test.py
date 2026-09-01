"""An attribute call on a from-imported VALUE is not a module-alias call.

``_build_import_table`` already records the difference: ``import foo.bar as
mod`` stores ``("foo.bar", "")`` with an EMPTY attr slot precisely to mean
"module alias -- treat the call's attribute as the symbol name", while ``from
foo.bar import baz`` stores ``("foo.bar", "baz")``. The attribute-call
resolution path did not read that slot, so ``<imported value>.<method>()``
took the module-alias branch and invented ``symbol:py:<module>:<method>`` --
a first-party node naming a function that exists under no spelling, answered
by ``wd callers`` with a confident edge rather than a miss.

The four shapes below are the whole decision table:

1. from-import of a VALUE, attribute call -- the bug. Falls through to the
   unresolved sentinel now.
2. module alias, attribute call -- unchanged, first-party
   (``import pkg.deep.inner as inner``) and stdlib (``import re`` +
   ``re.compile()``) alike, since both are the empty-attr-slot spelling.
3. from-import of a stdlib VALUE, attribute call -- the same shape as (1),
   and the same sentinel. Being stdlib is not what decided the old answer
   and does not decide the new one; the import spelling is.
4. from-imported NAME called directly -- unchanged; the import table's attr
   slot is exactly what resolves it, and nothing here touches that path.
5. from-import of a first-party CLASS, attribute call -- the same deferral as
   (1) at strategy level, and a different answer once the closure runs. The
   strategy still cannot tell it from (1): both are a non-empty attr slot on a
   name whose nature only the merged node set knows. What it must do is record
   the hint so ``weld._graph_closure_import_attr`` can decide, which is what
   the second half of this file's shape-5 pair checks -- it runs the closure
   over the extract output rather than asserting against a hand-built graph,
   because the two sides agreeing on the *wire format* of that hint is the one
   thing neither side's own suite can see.

The namespace-package submodule case (``from tools import tier1_corpus``,
where the attr slot is non-empty but names a real first-party MODULE) is the
one non-empty-slot shape that resolves in the strategy itself; it has its own
file, ``weld_python_callgraph_namespace_test``.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.graph_closure import close_graph
from weld.strategies import python_callgraph as pc
from weld.strategies._python_import_attr import read_import_attr_hint


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


class ImportedValueAttrCallTest(unittest.TestCase):
    """One fixture, four call shapes, one extract()."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="weld_value_attr_"))
        _write(self.tmp, "pkg/__init__.py", "")
        # The imported module: one dict constant, one real function. A
        # constant is what makes the invented id checkable -- ``TABLE`` has
        # a ``.get`` and ``pkg.tables`` has no ``get`` under any spelling.
        _write(
            self.tmp,
            "pkg/tables.py",
            """
            TABLE = {"a": 1}


            class Corpus:
                @classmethod
                def build(cls, rows):
                    return cls()


            def lookup(key):
                return TABLE[key]
            """,
        )
        _write(self.tmp, "pkg/deep/__init__.py", "")
        _write(
            self.tmp,
            "pkg/deep/inner.py",
            """
            def work():
                return 2
            """,
        )
        _write(
            self.tmp,
            "pkg/caller.py",
            """
            import pkg.deep.inner as inner
            import re

            from pathlib import Path
            from pkg.tables import TABLE, Corpus, lookup

            PATTERN = re.compile("x")


            def shape_one():
                return TABLE.get("a")

            def shape_two():
                return inner.work()

            def shape_three():
                return Path.cwd()

            def shape_four():
                return lookup("a")

            def shape_five():
                return Corpus.build([])
            """,
        )

    def _run(self) -> tuple[dict, list]:
        result = pc.extract(self.tmp, {"glob": "pkg/**/*.py"}, {})
        return result.nodes, result.edges

    def _closed(self) -> tuple[dict, list]:
        """The same extract, put through the closure the discover path runs."""
        nodes, edges = self._run()
        close_graph(nodes, edges)
        return nodes, edges

    def _targets(self, edges: list, caller: str) -> set[str]:
        return {
            e["to"]
            for e in edges
            if e["type"] == "calls"
            and e["from"] == f"symbol:py:pkg.caller:{caller}"
        }

    # -- shape 1: from-import of a first-party value ----------------------

    def test_value_attr_call_does_not_invent_a_sibling_symbol(self) -> None:
        """``TABLE.get()`` must not mint ``symbol:py:pkg.tables:get``."""
        nodes, edges = self._run()
        self.assertNotIn(
            "symbol:py:pkg.tables:get",
            nodes,
            "an attribute call on a from-imported value must not mint a "
            "fabricated sibling of the module the name came from",
        )
        self.assertNotIn(
            "symbol:py:pkg.tables:get",
            {e["to"] for e in edges},
            "no edge may name the fabricated id either",
        )

    def test_value_attr_call_falls_through_to_the_sentinel(self) -> None:
        """The call is still recorded -- as a visible unresolved, not a guess."""
        _, edges = self._run()
        self.assertIn("symbol:unresolved:get", self._targets(edges, "shape_one"))

    def test_value_attr_call_edge_is_speculative(self) -> None:
        """A sentinel edge is never ``resolved``; a fabricated one claimed to be."""
        _, edges = self._run()
        edge = next(
            e
            for e in edges
            if e["from"] == "symbol:py:pkg.caller:shape_one"
            and e["to"] == "symbol:unresolved:get"
        )
        self.assertFalse(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["confidence"], "speculative")

    # -- shape 2: module alias -------------------------------------------

    def test_module_alias_attr_call_is_unchanged(self) -> None:
        """``import pkg.deep.inner as inner`` keeps the module-alias branch."""
        nodes, edges = self._run()
        target = "symbol:py:pkg.deep.inner:work"
        self.assertIn(target, self._targets(edges, "shape_two"))
        self.assertEqual(nodes[target]["props"].get("origin"), "project")

    def test_stdlib_module_alias_keeps_stdlib_resolution(self) -> None:
        """``import re`` + ``re.compile()`` is the empty-slot case, untouched."""
        nodes, _ = self._run()
        self.assertEqual(
            nodes["symbol:py:re:compile"]["props"].get("origin"), "stdlib"
        )

    # -- shape 3: from-import of a stdlib value ---------------------------

    def test_stdlib_value_attr_call_falls_through_to_the_sentinel(self) -> None:
        """``from pathlib import Path`` + ``Path.cwd()`` is shape (1), not (2).

        ``pathlib`` has no top-level ``cwd``, so the old branch fabricated
        there too. Only the module-alias spelling keeps stdlib resolution,
        which shape (2) covers with ``import re`` + ``re.compile()``.
        """
        nodes, edges = self._run()
        self.assertNotIn("symbol:py:pathlib:cwd", nodes)
        self.assertIn("symbol:unresolved:cwd", self._targets(edges, "shape_three"))

    # -- shape 4: from-imported name called directly ----------------------

    def test_from_imported_name_called_directly_is_unchanged(self) -> None:
        """The import-table attr slot still resolves a bare call."""
        nodes, edges = self._run()
        target = "symbol:py:pkg.tables:lookup"
        self.assertIn(target, self._targets(edges, "shape_four"))
        self.assertEqual(nodes[target]["props"].get("origin"), "project")
        edge = next(
            e
            for e in edges
            if e["from"] == "symbol:py:pkg.caller:shape_four" and e["to"] == target
        )
        self.assertTrue(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["resolution"], "import")


    # -- shape 5: from-import of a first-party class ----------------------

    _CLASS_METHOD = "symbol:py:pkg.tables:Corpus.build"

    def test_class_attr_call_defers_with_the_names_the_reading_turns_on(
        self,
    ) -> None:
        """The strategy cannot decide it, so it records what would decide it.

        Same sentinel as shape (1) at this layer -- the difference between a
        class and a dict is not visible from one glob's import table.
        """
        _nodes, edges = self._run()
        self.assertIn("symbol:unresolved:build", self._targets(edges, "shape_five"))
        edge = next(
            e
            for e in edges
            if e["from"] == "symbol:py:pkg.caller:shape_five"
            and e["to"] == "symbol:unresolved:build"
        )
        hint = read_import_attr_hint(edge["props"])
        self.assertIsNotNone(hint)
        self.assertEqual(
            (hint.module, hint.base, hint.attr, hint.side),
            ("pkg.tables", "Corpus", "build", "to"),
        )

    def test_class_attr_call_resolves_to_the_method_once_closed(self) -> None:
        """End to end over the real hint: extract, then the closure rule.

        Both suites either side of this seam pass with a hint neither can
        actually read -- the strategy's against its own emission, the
        closure's against a hand-built one. Only running them in sequence
        catches a field renamed on one side.
        """
        nodes, edges = self._closed()
        self.assertIn(self._CLASS_METHOD, self._targets(edges, "shape_five"))
        self.assertEqual(nodes[self._CLASS_METHOD]["props"]["kind"], "method")

    def test_the_closure_leaves_the_other_four_shapes_alone(self) -> None:
        """The class reading must not drag the value readings with it."""
        nodes, edges = self._closed()
        self.assertIn("symbol:unresolved:get", self._targets(edges, "shape_one"))
        self.assertIn("symbol:unresolved:cwd", self._targets(edges, "shape_three"))
        self.assertNotIn("symbol:py:pkg.tables:get", nodes)
        self.assertNotIn("symbol:py:pathlib:cwd", nodes)
        self.assertIn(
            "symbol:py:pkg.deep.inner:work", self._targets(edges, "shape_two")
        )
        self.assertIn("symbol:py:pkg.tables:lookup", self._targets(edges, "shape_four"))


if __name__ == "__main__":
    unittest.main()
