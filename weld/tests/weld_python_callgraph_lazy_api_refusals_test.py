"""What the lazy-import accessor rule refuses, and what the definition sees.

bd ``80zz3``. The resolutions are ``weld_python_callgraph_lazy_api_test``; this
file is the other half, and it is the load-bearing one. Reading a function's
return value is exactly the move that fabricates an edge when the value is not
actually written in the source -- the class bd ``sigz2`` and bd ``zr486``
removed next door. The rule's whole defence is that it refuses everything
outside one syntactic shape, so the refusals *are* the contract; an untested
refusal is a rule that only appears narrow.

Every case asserts the refusal from both ends, for the reason the ``zr486``
refusals assert two things: that the call fell to its ``symbol:unresolved:``
sentinel, AND that the declined definition's own caller set is exactly what it
was without the rule. The second is the assertion that matters, because the
caller set is literally what ``wd callers`` renders and what the report was
about -- and unlike ``zr486``'s "no such id was minted", it still has bite when
the declined target is a real symbol the same tree defines, which here it always
is.
"""

from __future__ import annotations

import unittest

from weld.tests._import_table_fixture import ExtractCase, write

#: The definition every refusal below declines to reach.
DECLINED = "symbol:py:pkg.api:build"


class RefusalCase(ExtractCase):
    """An ``ExtractCase`` that reads a target's caller set, not just an edge."""

    CONSUMER = "symbol:py:pkg.consumer"

    def callers_of(self, edges: list, target: str) -> set[str]:
        """Every symbol recorded as calling *target* -- ``wd callers``' answer."""
        return {e["from"] for e in edges if e["type"] == "calls" and e["to"] == target}

    def assert_declined(
        self, caller: str, sentinel: str, *, real_callers: set[str] = frozenset()
    ) -> None:
        """*caller*'s call fell to *sentinel* and did not reach :data:`DECLINED`."""
        _nodes, edges = self.run_extract()
        targets = self.targets(edges, f"{self.CONSUMER}:{caller}")
        self.assertIn(sentinel, targets)
        self.assertNotIn(DECLINED, targets)
        self.assertEqual(set(real_callers), self.callers_of(edges, DECLINED))


class AccessorShapeRefusalTest(RefusalCase):
    """A function whose value is not written in its own body is not read.

    Three ways out of the shape, each a real reason the return stops being
    knowable from this AST: a statement that computes something, a decorator
    (which can replace the function outright), and a returned name the
    accessor's own imports did not bind.

    That last one is a deliberate under-report rather than a fabrication guard,
    and it is pinned here -- against the refusal
    ``weld.strategies._python_lazy_api.lazy_api_accessors`` states in its own
    module docstring (bd ``80zz3``) -- so it stays deliberate rather than
    becoming folklore. ``_returns_a_module_global``
    really does return ``pkg.api.build``: the name is bound by a module-level
    import, and following it would be correct *in this file*. Following it in
    general means proving nothing at module scope rebinds the name between
    definition and call, which is the value tracking this rule exists not to
    do -- and a module-level import needs no accessor anyway, since a direct
    call through it already resolves. So the answer here is silence.
    """

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(
            self.tmp,
            "pkg/api.py",
            "def build(x):\n    return x\n\ndef mint(x):\n    return x\n",
        )
        write(
            self.tmp,
            "pkg/consumer.py",
            """
            from pkg.api import build


            def cache(fn):
                return fn


            def _computes():
                from pkg.api import mint
                chosen = mint
                return chosen


            @cache
            def _decorated():
                from pkg.api import mint
                return mint


            def _returns_a_module_global():
                from pkg.api import mint
                return build


            def uses_computed():
                held = _computes()
                return held(1)


            def uses_decorated():
                held = _decorated()
                return held(2)


            def uses_module_global():
                held = _returns_a_module_global()
                return held(3)
            """,
        )

    def test_a_body_that_computes_is_not_an_accessor(self) -> None:
        self.assert_declined("uses_computed", "symbol:unresolved:held")

    def test_a_decorated_def_is_not_an_accessor(self) -> None:
        self.assert_declined("uses_decorated", "symbol:unresolved:held")

    def test_a_returned_name_the_accessor_did_not_import_is_refused(self) -> None:
        self.assert_declined("uses_module_global", "symbol:unresolved:held")


class AmbiguousImportRefusalTest(RefusalCase):
    """A returned name a second import also binds resolves through neither.

    The module import table holds one slot per local name, so two imports of
    ``build`` leave a slot whose winner is walk order. The accessor's guarantee
    is that its *own* import is what the alias reads; without it the rule would
    answer with whichever import happened to be written last -- a confident
    answer arrived at by accident, which is the shape a wrong rule passes review
    in.
    """

    RIVAL = "symbol:py:pkg.other:build"

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/api.py", "def build(x):\n    return x\n")
        write(self.tmp, "pkg/other.py", "def build(x):\n    return x\n")
        write(
            self.tmp,
            "pkg/consumer.py",
            """
            def _api():
                from pkg.api import build
                return build


            def _rival():
                from pkg.other import build
                return build


            def uses_ambiguous():
                held = _api()
                return held(1)
            """,
        )

    def test_two_imports_of_one_name_refuse_both_accessors(self) -> None:
        _nodes, edges = self.run_extract()
        self.assert_declined("uses_ambiguous", "symbol:unresolved:held")
        self.assertEqual(set(), self.callers_of(edges, self.RIVAL))


class BindingSiteRefusalTest(RefusalCase):
    """The unpack has to be the shape, and has to be the only binding.

    An arity mismatch and a starred target both mean the names do not line up
    with the returned tuple one-for-one -- a starred target binds a *list* of
    what is left, which is not one of the returned callables and shifts every
    name after it. A name the scope binds a second time (by reassignment, or as
    a parameter of the enclosing function) is not the accessor's any more by the
    time it is called, and which binding wins is a question about execution
    order this rule does not answer.
    """

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(
            self.tmp,
            "pkg/api.py",
            "def build(x):\n    return x\n\ndef mint(x):\n    return x\n",
        )
        write(
            self.tmp,
            "pkg/consumer.py",
            """
            def _api():
                from pkg.api import build, mint
                return mint, build


            def wrong_arity():
                held = _api()
                return held(1)


            def starred():
                first, *rest = _api()
                return first(2)


            def rebound():
                mint_here, build_here = _api()
                build_here = mint_here
                return build_here(3)


            def shadowed_by_parameter(build_here):
                mint_here, build_here = _api()
                return build_here(4)
            """,
        )

    def test_one_target_for_two_returned_names_is_refused(self) -> None:
        self.assert_declined("wrong_arity", "symbol:unresolved:held")

    def test_a_starred_target_is_refused(self) -> None:
        self.assert_declined("starred", "symbol:unresolved:first")

    def test_a_name_rebound_in_the_same_scope_is_refused(self) -> None:
        self.assert_declined("rebound", "symbol:unresolved:build_here")

    def test_a_name_the_signature_already_binds_is_refused(self) -> None:
        self.assert_declined(
            "shadowed_by_parameter", "symbol:unresolved:build_here"
        )


class AccessorReachRefusalTest(RefusalCase):
    """The accessor has to be a module-level ``def`` in the file being read.

    An imported accessor's body lives in another module, which a per-file walk
    does not hold; a nested one binds a name this module's scope cannot see.
    Both refuse for the same reason -- the returned names are not readable off
    the AST in hand -- and together they are the boundary that keeps this from
    growing into cross-module value tracking.
    """

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/api.py", "def build(x):\n    return x\n")
        write(
            self.tmp,
            "pkg/vendor.py",
            """
            def shared_api():
                from pkg.api import build
                return build
            """,
        )
        write(
            self.tmp,
            "pkg/consumer.py",
            """
            from pkg.vendor import shared_api


            def uses_imported_accessor():
                held = shared_api()
                return held(1)


            def uses_nested_accessor():
                def _nested():
                    from pkg.api import build
                    return build

                held = _nested()
                return held(2)
            """,
        )

    def test_an_accessor_imported_from_another_module_is_refused(self) -> None:
        self.assert_declined("uses_imported_accessor", "symbol:unresolved:held")

    def test_an_accessor_nested_in_a_function_is_refused(self) -> None:
        self.assert_declined("uses_nested_accessor", "symbol:unresolved:held")


class AliasDoesNotLeakAcrossScopesTest(RefusalCase):
    """One scope's unpack binds nothing in its sibling or in its nested def.

    The alias table is rebuilt per scope from that scope's own statements. A
    module-wide table would have been half the code and would have answered a
    sibling function's unrelated local with the first function's import -- a
    fabrication reached purely by ignoring scope, and the cheapest way this rule
    could have gone wrong.

    ``outer.inner`` is the pointed one, and it is a deliberate under-report
    rather than a fabrication guard: ``inner`` really does close over
    ``outer``'s alias, so following it would be correct *here*. Following a
    closure in general is value tracking across scopes, which is the thing this
    rule exists not to do -- and the scope hooks keep an enclosing scope's
    aliases live only for a nested ``def``'s DECORATOR, which evaluates in the
    enclosing scope, never for its body.
    """

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/api.py", "def build(x):\n    return x\n")
        write(
            self.tmp,
            "pkg/consumer.py",
            """
            def _api():
                from pkg.api import build
                return build


            def binds_it():
                build_here = _api()
                return build_here(1)


            def sibling(build_here):
                return build_here(2)


            def outer():
                build_here = _api()

                def inner():
                    return build_here(3)

                return inner


            class Holder:
                held = _api()

                def method(self):
                    return held(4)
            """,
        )

    def test_only_the_scope_that_bound_the_alias_reaches_the_definition(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertEqual(
            {f"{self.CONSUMER}:binds_it"}, self.callers_of(edges, DECLINED)
        )

    def test_a_sibling_scope_falls_to_the_sentinel(self) -> None:
        self.assert_declined(
            "sibling",
            "symbol:unresolved:build_here",
            real_callers={f"{self.CONSUMER}:binds_it"},
        )

    def test_a_closure_over_the_alias_falls_to_the_sentinel(self) -> None:
        self.assert_declined(
            "outer.inner",
            "symbol:unresolved:build_here",
            real_callers={f"{self.CONSUMER}:binds_it"},
        )

    def test_a_method_does_not_inherit_its_class_bodys_alias(self) -> None:
        self.assert_declined(
            "Holder.method",
            "symbol:unresolved:held",
            real_callers={f"{self.CONSUMER}:binds_it"},
        )


if __name__ == "__main__":
    unittest.main()
