"""A bare module name inside a non-package directory names the sibling file.

bd sigz2. ``tools/lint_pinned_citations.py`` says ``from lint_test_hygiene
import changed_test_lines`` and calls it bare. There is
no top-level ``lint_test_hygiene`` module anywhere; the file it means is
``tools/lint_test_hygiene.py``, which weld names
``symbol:py:tools.lint_test_hygiene:changed_test_lines``. Python finds it
because ``tools/`` has no ``__init__.py``, so the directory itself goes on
``sys.path`` and a bare name binds there first.

``_build_import_table`` recorded the module string verbatim, so the call
resolved to ``symbol:py:lint_test_hygiene:changed_test_lines`` -- a *confident*
edge to a module that exists under no spelling, minted ``origin=external``. The
real definition collected no caller edge and ``wd callers`` on the shared
primitive answered 1 of 3. Same failure class as bd 1m1g9's fabricated
``symbol:py:<module>:<method>``: a miss is recoverable, a confident wrong answer
is what a reader acts on.

This file holds what the rule *resolves*. What it refuses -- and each refusal is
a shape live in this repo -- is
``weld_python_callgraph_sibling_import_refusals_test``; the two are one subject
split at the line-count cap.
"""

from __future__ import annotations

import unittest

from weld.graph_closure import close_graph
from weld.strategies._python_import_attr import read_import_attr_hint
from weld.tests._import_table_fixture import ExtractCase, write


class ScriptDirectorySiblingTest(ExtractCase):
    """The three branches that read the import table's module slot.

    ``scripts/`` mirrors ``tools/``: plain modules run as scripts (or as a Bazel
    ``py_binary``/``py_test`` whose main sits there), importing each other by
    bare name. All three spellings below read the same table entry, which is why
    the table is corrected once rather than each branch being taught the rule.
    """

    CONSUMER = "symbol:py:scripts.consumer"
    SHARED = "symbol:py:scripts.helper:shared"

    def build_tree(self) -> None:
        write(
            self.tmp,
            "scripts/helper.py",
            """
            TABLE = {"a": 1}


            def shared(root):
                return TABLE.get(root)
            """,
        )
        write(
            self.tmp,
            "scripts/aliased.py",
            """
            def work():
                return 2
            """,
        )
        write(
            self.tmp,
            "scripts/consumer.py",
            """
            import aliased

            from helper import TABLE, shared


            def calls_the_sibling():
                return shared(".")

            def calls_through_a_plain_import():
                return aliased.work()

            def calls_a_method_on_an_imported_value():
                return TABLE.get("a")
            """,
        )

    # -- the reported shape ------------------------------------------------

    def test_bare_call_of_a_from_imported_sibling_names_the_real_symbol(self) -> None:
        nodes, edges = self.run_extract()
        self.assertIn(
            self.SHARED,
            self.targets(edges, f"{self.CONSUMER}:calls_the_sibling"),
            "a bare call of a name imported from a sibling module must land on "
            "the sibling's own symbol",
        )
        self.assertEqual(nodes[self.SHARED]["props"].get("origin"), "project")

    def test_the_fabricated_top_level_id_is_gone(self) -> None:
        """The old answer named a module that exists under no spelling."""
        nodes, edges = self.run_extract()
        self.assertNotIn("symbol:py:helper:shared", nodes)
        self.assertNotIn("symbol:py:helper:shared", {e["to"] for e in edges})

    def test_the_retargeted_edge_stays_a_definite_import_resolution(self) -> None:
        _nodes, edges = self.run_extract()
        edge = self.edge_between(
            edges, f"{self.CONSUMER}:calls_the_sibling", self.SHARED
        )
        self.assertTrue(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["confidence"], "definite")
        self.assertEqual(edge["props"]["resolution"], "import")

    def test_the_definition_is_reachable_from_its_caller(self) -> None:
        """The dogfood question: who else breaks if I change this?"""
        _nodes, edges = self.run_extract()
        callers = {
            e["from"]
            for e in edges
            if e["type"] == "calls" and e["to"] == self.SHARED
        }
        self.assertEqual(callers, {f"{self.CONSUMER}:calls_the_sibling"})

    # -- the same directory fact, read through the other two branches ------

    def test_a_plain_import_of_a_sibling_resolves_its_attribute_call(self) -> None:
        """``import aliased`` + ``aliased.work()`` is the empty-attr-slot path.

        It reads the same table entry's module, so correcting the table is what
        fixes both spellings rather than one branch of the resolver.
        """
        nodes, edges = self.run_extract()
        target = "symbol:py:scripts.aliased:work"
        self.assertIn(
            target,
            self.targets(edges, f"{self.CONSUMER}:calls_through_a_plain_import"),
        )
        self.assertEqual(nodes[target]["props"].get("origin"), "project")
        self.assertNotIn("symbol:py:aliased:work", nodes)

    def test_a_deferred_attribute_call_carries_the_corrected_module(self) -> None:
        """``TABLE.get()`` still defers -- but on the module Python binds.

        The hint is what ``weld._graph_closure_import_attr`` reads back, so one
        naming the fabricated module would send every closure rule looking under
        a module that is not there.
        """
        _nodes, edges = self.run_extract()
        edge = self.edge_between(
            edges,
            f"{self.CONSUMER}:calls_a_method_on_an_imported_value",
            "symbol:unresolved:get",
        )
        hint = read_import_attr_hint(edge["props"])
        self.assertIsNotNone(hint)
        self.assertEqual(
            (hint.module, hint.base, hint.attr), ("scripts.helper", "TABLE", "get")
        )

    def test_the_closure_leaves_the_retarget_alone(self) -> None:
        """Nothing downstream re-decides a resolution the strategy settled."""
        nodes, edges = self.run_extract()
        close_graph(nodes, edges)
        self.assertIn(
            self.SHARED, self.targets(edges, f"{self.CONSUMER}:calls_the_sibling")
        )


class StdlibShadowedByASiblingTest(ExtractCase):
    """``sys.path[0]`` is searched before the stdlib, so a sibling wins there too.

    Not a special case in the rule and deliberately not written as one -- the
    candidate is looked up before anything else is asked, which is exactly the
    interpreter's own precedence. This is the shape that would break if the rule
    ever grew a "leave standard-library names alone" shortcut.
    """

    def build_tree(self) -> None:
        write(self.tmp, "scripts/json.py", "def loads(text):\n    return {}\n")
        write(
            self.tmp,
            "scripts/consumer.py",
            """
            from json import loads


            def parse(text):
                return loads(text)
            """,
        )

    def test_a_sibling_named_like_a_stdlib_module_wins(self) -> None:
        nodes, edges = self.run_extract()
        caller = "symbol:py:scripts.consumer:parse"
        target = "symbol:py:scripts.json:loads"
        self.assertIn(target, self.targets(edges, caller))
        self.assertNotIn("symbol:py:json:loads", nodes)
        self.assertEqual(
            self.edge_between(edges, caller, target)["props"]["resolution"], "import"
        )


class RepoShapeSiblingTest(ExtractCase):
    """The reported files, reduced: two consumers, one shared primitive.

    The measured symptom was a caller *count*, and a fixture with one consumer
    cannot reproduce a count. This one reproduces the reported topology --
    ``tools/lint_test_hygiene.py`` called from itself and from two siblings --
    so the assertion is the three-caller set the issue asked for.
    """

    PRIMITIVE = "symbol:py:tools.lint_test_hygiene:changed_test_lines"

    def build_tree(self) -> None:
        write(
            self.tmp,
            "tools/lint_test_hygiene.py",
            """
            def changed_test_lines(root, pathspec=()):
                return {}


            def has_optout(line):
                return False


            def lint_test_hygiene(root):
                return changed_test_lines(root)
            """,
        )
        write(
            self.tmp,
            "tools/lint_hand_built_payloads.py",
            """
            from lint_test_hygiene import changed_test_lines, has_optout


            def lint_hand_built_payloads(root):
                return changed_test_lines(root), has_optout("x")
            """,
        )
        write(
            self.tmp,
            "tools/lint_pinned_citations.py",
            """
            from lint_test_hygiene import changed_test_lines, has_optout

            SCOPE_PREFIX = "weld/tests/"


            def lint_pinned_citations(root):
                return changed_test_lines(root, pathspec=(SCOPE_PREFIX,))
            """,
        )

    def test_the_shared_primitive_reports_all_three_callers(self) -> None:
        _nodes, edges = self.run_extract()
        callers = {
            e["from"]
            for e in edges
            if e["type"] == "calls" and e["to"] == self.PRIMITIVE
        }
        self.assertEqual(
            callers,
            {
                "symbol:py:tools.lint_test_hygiene:lint_test_hygiene",
                "symbol:py:tools.lint_hand_built_payloads:lint_hand_built_payloads",
                "symbol:py:tools.lint_pinned_citations:lint_pinned_citations",
            },
        )


if __name__ == "__main__":
    unittest.main()
