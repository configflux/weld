"""An explicit relative import names the module the interpreter would import.

bd ``zr486``. ``_build_import_table`` read ``node.module`` off every
``ast.ImportFrom`` and dropped ``node.level``, so ``from .helper import work``
inside ``pkg/caller.py`` recorded ``("helper", "work")`` and a bare ``work()``
resolved to ``symbol:py:helper:work`` -- a *confident* edge naming a module that
exists under no spelling, minted ``origin=external``, while the real
``pkg/helper.py`` definition collected no caller edge at all. Same fabrication
class as bd ``sigz2`` next door and bd ``1m1g9`` before it.

Measured on a package-shaped seed, this is what the defect cost: three
fabricated ``external`` symbol nodes, two ``symbol:unresolved:`` sentinels where
``from . import x`` was skipped outright, and every one of the five call edges
pointing somewhere other than the definition it meant.

The cases below are the resolutions. The refusals -- a level that walks past the
top-level package, and a module under no package at all -- are
``weld_python_callgraph_relative_import_refusals_test``; the two are one subject
split at the line-count cap, exactly as the ``sigz2`` pair is.
"""

from __future__ import annotations

import unittest

from weld.graph_closure import close_graph
from weld.strategies._python_import_attr import read_import_attr_hint
from weld.tests._import_table_fixture import ExtractCase, write


class RelativeImportResolutionTest(ExtractCase):
    """The reported shape, plus each level and spelling that reaches the table.

    One package, one subpackage, and every relative spelling that binds a name:
    ``from .x import y`` at level 1, ``from ..x import y`` at level 2, and the
    ``from . import x`` / ``from .. import x`` forms whose ``node.module`` is
    ``None`` and which were therefore skipped outright before. All four read the
    same table slot, which is why the arithmetic is done once where the table is
    built rather than in each of the three resolver branches that consume it.
    """

    CALLER = "symbol:py:pkg.caller"
    DEEP = "symbol:py:pkg.sub.deep"
    HELPER_WORK = "symbol:py:pkg.helper:work"
    SHARED_TOP = "symbol:py:pkg.shared:top"
    PEER_BESIDE = "symbol:py:pkg.sub.peer:beside"

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/sub/__init__.py", "")
        write(self.tmp, "pkg/helper.py", "def work():\n    return 1\n")
        write(self.tmp, "pkg/shared.py", "def top():\n    return 2\n")
        write(self.tmp, "pkg/sub/peer.py", "def beside():\n    return 3\n")
        write(
            self.tmp,
            "pkg/caller.py",
            """
            from .helper import work
            from . import shared


            def calls_sibling():
                return work()

            def calls_via_package_attr():
                return shared.top()
            """,
        )
        write(
            self.tmp,
            "pkg/sub/deep.py",
            """
            from .. import shared
            from ..shared import top
            from .peer import beside


            def calls_parent_attr():
                return shared.top()

            def calls_parent_name():
                return top()

            def calls_peer():
                return beside()
            """,
        )

    # -- the reported shape ------------------------------------------------

    def test_a_level_one_import_lands_on_the_sibling_module(self) -> None:
        """``from .helper import work`` in ``pkg.caller`` means ``pkg.helper``."""
        nodes, edges = self.run_extract()
        self.assertIn(
            self.HELPER_WORK, self.targets(edges, f"{self.CALLER}:calls_sibling")
        )
        self.assertEqual(nodes[self.HELPER_WORK]["props"].get("origin"), "project")

    def test_the_fabricated_top_level_ids_are_gone(self) -> None:
        """Each old answer named a module that exists under no spelling."""
        nodes, edges = self.run_extract()
        targets = {e["to"] for e in edges}
        for fabricated in (
            "symbol:py:helper:work",
            "symbol:py:shared:top",
            "symbol:py:peer:beside",
        ):
            self.assertNotIn(fabricated, nodes)
            self.assertNotIn(fabricated, targets)

    def test_no_first_party_call_is_tagged_external(self) -> None:
        """The ADR 0042 half of the same defect: every id here is this project's."""
        nodes, _edges = self.run_extract()
        external = [
            node_id
            for node_id, node in nodes.items()
            if node_id.startswith("symbol:py:")
            and node["props"].get("origin") == "external"
        ]
        self.assertEqual(external, [])

    # -- level 2, and the two module-is-None spellings ---------------------

    def test_a_level_two_import_walks_one_package_up(self) -> None:
        """``from ..shared import top`` in ``pkg.sub.deep`` means ``pkg.shared``."""
        _nodes, edges = self.run_extract()
        self.assertIn(
            self.SHARED_TOP, self.targets(edges, f"{self.DEEP}:calls_parent_name")
        )

    def test_a_level_one_import_of_a_peer_stays_inside_the_subpackage(self) -> None:
        """Level 1 from ``pkg.sub.deep`` is ``pkg.sub``, not ``pkg``."""
        _nodes, edges = self.run_extract()
        self.assertIn(
            self.PEER_BESIDE, self.targets(edges, f"{self.DEEP}:calls_peer")
        )

    def test_from_dot_import_binds_the_package_itself(self) -> None:
        """``from . import shared`` was skipped outright, so the call fell through.

        ``node.module`` is ``None`` for this spelling, and the old table simply
        ``continue``d past it -- ``shared.top()`` reached
        ``symbol:unresolved:top``. The entry it records now is byte-identical to
        what ``from pkg import shared`` produces, so the submodule-versus-value
        reading of it stays where it already lives.
        """
        _nodes, edges = self.run_extract()
        self.assertIn(
            self.SHARED_TOP,
            self.targets(edges, f"{self.CALLER}:calls_via_package_attr"),
        )

    def test_from_dotdot_import_binds_the_parent_package(self) -> None:
        """The same spelling one level further up, from inside the subpackage."""
        _nodes, edges = self.run_extract()
        self.assertIn(
            self.SHARED_TOP, self.targets(edges, f"{self.DEEP}:calls_parent_attr")
        )

    def test_the_sentinel_the_skipped_spelling_used_to_produce_is_gone(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertNotIn("symbol:unresolved:top", {e["to"] for e in edges})

    # -- the resolution survives its consumers -----------------------------

    def test_the_retargeted_edge_stays_a_definite_import_resolution(self) -> None:
        _nodes, edges = self.run_extract()
        props = self.edge_between(
            edges, f"{self.CALLER}:calls_sibling", self.HELPER_WORK
        )["props"]
        self.assertTrue(props["resolved"])
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["resolution"], "import")

    def test_the_definition_is_reachable_from_every_caller(self) -> None:
        """The dogfood question: who else breaks if I change this?

        ``pkg.shared.top`` is called three ways -- through a level-2 name import
        and through both ``from . import``/``from .. import`` attribute paths --
        and before the fix the real definition collected none of them.
        """
        _nodes, edges = self.run_extract()
        callers = {
            e["from"]
            for e in edges
            if e["type"] == "calls" and e["to"] == self.SHARED_TOP
        }
        self.assertEqual(
            callers,
            {
                f"{self.CALLER}:calls_via_package_attr",
                f"{self.DEEP}:calls_parent_attr",
                f"{self.DEEP}:calls_parent_name",
            },
        )

    def test_the_closure_leaves_the_retarget_alone(self) -> None:
        """Nothing downstream re-decides a resolution the strategy settled."""
        nodes, edges = self.run_extract()
        close_graph(nodes, edges)
        self.assertIn(
            self.HELPER_WORK, self.targets(edges, f"{self.CALLER}:calls_sibling")
        )


class PackageInitRelativeImportTest(ExtractCase):
    """``pkg/__init__.py`` is the package, so level 1 does not leave it.

    The one asymmetry ``package_of`` carries. ``_module_dotted_path`` collapses
    ``pkg/__init__.py`` to ``pkg``, the same dotted string a top-level
    ``pkg.py`` would get, and the two have opposite answers: the package's own
    ``__package__`` is ``pkg`` while the module's is empty. Reading the dotted
    path alone would send this import one level too far up and mint
    ``symbol:py:helper:work`` all over again.
    """

    def build_tree(self) -> None:
        write(self.tmp, "pkg/helper.py", "def work():\n    return 1\n")
        write(
            self.tmp,
            "pkg/__init__.py",
            """
            from .helper import work


            def reexport():
                return work()
            """,
        )

    def test_level_one_inside_an_init_stays_in_its_own_package(self) -> None:
        nodes, edges = self.run_extract()
        target = "symbol:py:pkg.helper:work"
        self.assertIn(target, self.targets(edges, "symbol:py:pkg:reexport"))
        self.assertNotIn("symbol:py:helper:work", nodes)


class NamespacePackageRelativeImportTest(ExtractCase):
    """Both import rules are live in a PEP 420 directory; they must compose.

    A directory with no ``__init__.py`` is where ``_python_sibling_import``
    fires -- and it is also a legal namespace package, where a relative import
    is legal too. The relative rule runs first and produces an already-absolute
    name; the sibling rule's ``glob_modules`` membership test is what stops it
    re-prefixing that name into ``ns.sub.ns.sub.helper``.
    """

    def build_tree(self) -> None:
        write(self.tmp, "ns/sub/helper.py", "def work():\n    return 1\n")
        write(
            self.tmp,
            "ns/sub/mod.py",
            """
            from .helper import work


            def calls_it():
                return work()
            """,
        )

    def test_a_relative_import_in_a_namespace_directory_is_not_re_prefixed(
        self,
    ) -> None:
        nodes, edges = self.run_extract()
        target = "symbol:py:ns.sub.helper:work"
        self.assertIn(target, self.targets(edges, "symbol:py:ns.sub.mod:calls_it"))
        self.assertEqual(nodes[target]["props"].get("origin"), "project")
        self.assertNotIn("symbol:py:ns.sub.ns.sub.helper:work", nodes)


class DeferredAttributeCallTest(ExtractCase):
    """A relative import the glob cannot settle still hands over a real module.

    ``from . import outside`` names a submodule this glob does not own, so the
    strategy declines to read ``outside.thing()`` as a module attribute and
    defers via ``props.import_attr`` to
    :mod:`weld._graph_closure_import_attr`, which runs over the merged graph.
    The hint is the whole point of the deferral: one naming the source's bare
    spelling would send every closure rule looking under a module that is not
    there.
    """

    GLOB = "pkg/*.py"

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/outside/__init__.py", "")
        write(self.tmp, "pkg/outside/thing.py", "def thing():\n    return 1\n")
        write(
            self.tmp,
            "pkg/caller.py",
            """
            from . import outside


            def calls_out():
                return outside.thing()
            """,
        )

    def test_the_deferred_hint_names_the_resolved_package(self) -> None:
        _nodes, edges = self.run_extract()
        edge = self.edge_between(
            edges, "symbol:py:pkg.caller:calls_out", "symbol:unresolved:thing"
        )
        hint = read_import_attr_hint(edge["props"])
        self.assertIsNotNone(hint)
        self.assertEqual((hint.module, hint.base, hint.attr), ("pkg", "outside", "thing"))


if __name__ == "__main__":  # pragma: no cover - bazel runs unittest directly
    unittest.main()
