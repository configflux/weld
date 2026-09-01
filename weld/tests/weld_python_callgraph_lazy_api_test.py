"""A name unpacked from a lazy-import accessor calls what the accessor imported.

bd ``80zz3``. The repo defers an import into a function on purpose -- to keep a
symbol-id shape or an import-table shape defined in exactly one place while
avoiding a circular import at module load -- and then unpacks the accessor's
return into locals::

    def _callgraph_api():
        from weld.strategies.python_callgraph import _build_import_table, _symbol_id
        return _symbol_id, _build_import_table

    _symbol_id, build_import_table = _callgraph_api()   # inside _module_facts
    build_import_table(tree, package=...)

Two mechanisms sit between that call and its target, and only one of them was
broken. The ``from ... import`` inside a function body was already in the table
-- ``_build_import_table`` walks the whole module -- so a call by the *imported*
name already resolved; :class:`FunctionScopedImportIsAlreadyResolvedTest` pins
that half so a later change cannot quietly take it away. The local name bound by
unpacking the accessor's return was the miss: it appears in no import table, so
``build_import_table(...)`` fell to a ``symbol:unresolved:`` sentinel and
``wd callers`` on the real definition answered one caller where two exist --
complete-looking and wrong, on the module whose contract the caller was about to
change.

The licence for reading the accessor is that its value is not inferred. Its body
is imports and one ``return`` of names those imports bound, so what it evaluates
to is written in the source. Everything outside that shape is refused, and the
refusals are ``weld_python_callgraph_lazy_api_refusals_test`` -- one subject
split at the line-count cap, as the ``sigz2``/``zr486`` pairs next door are.
"""

from __future__ import annotations

import unittest

from weld.tests._import_table_fixture import ExtractCase, write


class LazyApiUnpackResolutionTest(ExtractCase):
    """The reported shape and the spellings that reach the same table slot.

    One accessor returning a tuple, one returning a single name, unpacked in a
    function body, at module scope, and in a class body -- the three scopes the
    visitor sweeps for calls -- plus a relative import inside the accessor, which
    proves the alias reads the *corrected* module slot (bd ``zr486``) rather than
    the source's bare spelling.
    """

    CONSUMER = "symbol:py:pkg.consumer"
    BUILD = "symbol:py:pkg.api:build"
    MINT = "symbol:py:pkg.api:mint"
    ONLY = "symbol:py:pkg.api:only"

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(
            self.tmp,
            "pkg/api.py",
            """
            def build(x):
                return x

            def mint(x):
                return x

            def only(x):
                return x
            """,
        )
        write(
            self.tmp,
            "pkg/consumer.py",
            '''
            def _api():
                """Lazy handle, imported here on purpose."""
                from pkg.api import build, mint
                return mint, build


            def _one():
                from .api import only
                return only


            def unpacks_tuple():
                mint_here, build_here = _api()
                return build_here(1), mint_here(2)


            def binds_single():
                only_here = _one()
                return only_here(3)


            class Holder:
                mint_at_class, build_at_class = _api()
                AT_CLASS = build_at_class(4)


            AT_MODULE_MINT, AT_MODULE_BUILD = _api()
            AT_MODULE = AT_MODULE_BUILD(5)
            ''',
        )

    # -- the reported shape ------------------------------------------------

    def test_tuple_unpacked_name_calls_the_imported_definition(self) -> None:
        _nodes, edges = self.run_extract()
        targets = self.targets(edges, f"{self.CONSUMER}:unpacks_tuple")
        self.assertEqual({self.BUILD, self.MINT, f"{self.CONSUMER}:_api"}, targets)

    def test_the_alias_edge_is_definite_and_reads_as_an_import(self) -> None:
        _nodes, edges = self.run_extract()
        props = self.edge_between(
            edges, f"{self.CONSUMER}:unpacks_tuple", self.BUILD
        )["props"]
        self.assertTrue(props["resolved"])
        self.assertEqual("definite", props["confidence"])
        self.assertEqual("import", props["resolution"])
        self.assertEqual("build_here", props["raw"])

    def test_no_sentinel_survives_for_an_aliased_name(self) -> None:
        nodes, _edges = self.run_extract()
        self.assertNotIn("symbol:unresolved:build_here", nodes)
        self.assertNotIn("symbol:unresolved:mint_here", nodes)

    # -- the other spellings that reach the same slot ----------------------

    def test_a_single_returned_name_binds_through_a_plain_assignment(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertIn(
            self.ONLY, self.targets(edges, f"{self.CONSUMER}:binds_single")
        )

    def test_the_accessors_relative_import_resolves_to_the_real_module(self) -> None:
        nodes, edges = self.run_extract()
        self.assertIn(
            self.ONLY, self.targets(edges, f"{self.CONSUMER}:binds_single")
        )
        self.assertNotIn("symbol:py:api:only", nodes)

    def test_a_class_body_unpack_binds_for_that_class_body(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertIn(
            self.BUILD, self.targets(edges, f"{self.CONSUMER}:Holder")
        )

    def test_a_module_scope_unpack_binds_at_module_scope(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertIn(self.BUILD, self.targets(edges, "file:pkg/consumer"))


class FunctionScopedImportIsAlreadyResolvedTest(ExtractCase):
    """Half (1) of the report: a function-body import was never the blocker.

    ``weld.strategies.python_callgraph._build_import_table`` walks the whole
    module, so an import written inside a function body already lands in the
    table and a call by the imported name already resolves. Measured before any
    code changed (bd ``80zz3``), and pinned here because the fix for half (2)
    sits directly on top of that behaviour: if a later narrowing of the table to
    module-level imports took this away, the alias rule above would lose the
    entry it resolves through, and both halves would go quiet at once.
    """

    CALLER = "symbol:py:pkg.caller:uses_deferred_import"
    WORK = "symbol:py:pkg.helper:work"

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/helper.py", "def work():\n    return 1\n")
        write(
            self.tmp,
            "pkg/caller.py",
            """
            def uses_deferred_import():
                from pkg.helper import work
                return work()
            """,
        )

    def test_a_call_by_the_function_scoped_imported_name_resolves(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertIn(self.WORK, self.targets(edges, self.CALLER))


if __name__ == "__main__":
    unittest.main()
