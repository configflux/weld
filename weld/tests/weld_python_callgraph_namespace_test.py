"""Namespace-package submodule import resolution for python_callgraph.

Regression coverage for bd ptw3. ``tools/`` is a *namespace* package (a
directory with no ``__init__.py``), so ``from tools import tier1_corpus``
followed by ``tier1_corpus.CorpusEntry()`` used to resolve the attribute
call against the *parent* package ``tools`` (the import-table ``module``
slot) and mint ``symbol:py:tools:CorpusEntry``. Bare ``tools`` is not a
file-backed module path, so it fell through to ``origin="external"`` -- a
duplicate of the correctly-resolved
``symbol:py:tools.tier1_corpus:CorpusEntry`` project symbol.

The fix (ADR 0042 Python rules) resolves ``from PARENT import CHILD`` +
``CHILD.attr()`` to the real submodule ``PARENT.CHILD`` *when that dotted
path is a module this run proved first-party*, so the symbol classifies
``origin="project"`` under the real submodule and no bare-parent duplicate
is minted.

The membership gate is also what tells a submodule import apart from a
value import. A non-empty attr slot whose ``PARENT.CHILD`` is *not* a
project module means ``CHILD`` holds an ordinary object, and its attribute
is a method on that object rather than a sibling of ``PARENT`` -- so it
resolves to nothing at all now (bd 1m1g9), where it used to fabricate
``symbol:py:PARENT:attr``. The four-shape decision table lives in
``weld_python_callgraph_import_value_attr_test``; what this file pins is
that the gate is membership-based, in both directions.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.strategies import python_callgraph as pc


class PythonCallgraphNamespacePackageImportTest(unittest.TestCase):
    def setUp(self) -> None:
        # ``ns/`` has NO ``__init__.py`` -- it is a namespace package, the
        # same shape as this repo's ``tools/``.
        self.tmp = Path(tempfile.mkdtemp(prefix="weld_ns_pkg_"))
        (self.tmp / "ns").mkdir()
        # Submodule defining a class used cross-module.
        (self.tmp / "ns" / "corpus.py").write_text(
            textwrap.dedent(
                """
                class CorpusEntry:
                    pass
                """
            ).lstrip(),
            encoding="utf-8",
        )
        # Caller: ``from ns import corpus`` then ``corpus.CorpusEntry()``.
        # ``corpus`` is a *submodule*, not a value, so the attribute call's
        # real defining module is ``ns.corpus`` -- not the bare ``ns``.
        (self.tmp / "ns" / "consumer.py").write_text(
            textwrap.dedent(
                """
                from ns import corpus

                def build():
                    return corpus.CorpusEntry()
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def _run(self) -> tuple[dict, list]:
        result = pc.extract(self.tmp, {"glob": "ns/*.py"}, {})
        return result.nodes, result.edges

    def test_submodule_import_resolves_to_real_submodule(self) -> None:
        """``corpus.CorpusEntry()`` resolves to ``ns.corpus``, not ``ns``."""
        nodes, edges = self._run()
        real = "symbol:py:ns.corpus:CorpusEntry"
        bare = "symbol:py:ns:CorpusEntry"
        self.assertIn(
            real,
            nodes,
            "namespace-package submodule import must resolve to the real "
            "submodule ns.corpus",
        )
        self.assertNotIn(
            bare,
            nodes,
            "must not mint a bare-namespace-package duplicate symbol:py:ns:*",
        )
        # The calls edge must point at the real submodule symbol.
        match = next(
            (
                e
                for e in edges
                if e["from"] == "symbol:py:ns.consumer:build"
                and e["to"] == real
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(
            match, f"calls edge must target the real submodule: {edges}"
        )
        self.assertTrue(match["props"]["resolved"])

    def test_namespace_submodule_symbol_is_project(self) -> None:
        """The resolved submodule symbol classifies ``origin=project``."""
        nodes, _ = self._run()
        real = "symbol:py:ns.corpus:CorpusEntry"
        self.assertEqual(
            nodes[real]["props"].get("origin"),
            "project",
            "a symbol under a first-party namespace-package submodule must "
            "classify origin=project, not external",
        )

    def test_no_bare_namespace_symbol_left_external(self) -> None:
        """No first-party symbol stays mislabelled under the bare package."""
        nodes, _ = self._run()
        leaked = [
            nid
            for nid, n in nodes.items()
            if isinstance(n, dict)
            and n.get("type") == "symbol"
            and (n.get("props") or {}).get("module") == "ns"
            and (n.get("props") or {}).get("origin") == "external"
        ]
        self.assertEqual(
            leaked,
            [],
            f"bare-namespace-package symbols wrongly tagged external: {leaked}",
        )

    def test_value_import_attr_call_resolves_to_neither_module(self) -> None:
        """A non-submodule (value) ``from PARENT import NAME`` resolves to nothing.

        ``ns.corpus`` is a real submodule, so it resolves to ``ns.corpus``.
        ``ns2.widget`` is NOT a project module, so ``widget`` names a value
        and ``widget.render()`` is a method on whatever it holds. Neither
        candidate module may claim it: not ``ns2.widget`` (the ptw3
        membership gate, which is what this half pins), and not the bare
        parent ``ns2`` either -- ``symbol:py:ns2:render`` was the fabricated
        id of bd 1m1g9.
        """
        td = Path(tempfile.mkdtemp(prefix="weld_ns_value_"))
        (td / "ns2").mkdir()  # namespace package, no __init__.py
        (td / "ns2" / "use.py").write_text(
            textwrap.dedent(
                """
                from ns2 import widget

                def go():
                    return widget.render()
                """
            ).lstrip(),
            encoding="utf-8",
        )
        result = pc.extract(td, {"glob": "ns2/*.py"}, {})
        self.assertNotIn("symbol:py:ns2.widget:render", result.nodes)
        self.assertNotIn("symbol:py:ns2:render", result.nodes)
        self.assertIn("symbol:unresolved:render", result.nodes)


if __name__ == "__main__":
    unittest.main()
