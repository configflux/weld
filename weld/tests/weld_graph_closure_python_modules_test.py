"""Which spellings of a first-party Python import resolve, and in what order.

Field-eval finding N4. ``_module_index`` keys Python file nodes by their FULL
repo-relative path (``src.acme_notify.config``), but source reads ``from
acme_notify.config import load_config`` -- because ``src`` is the source
root, not part of the module path. The literal name therefore missed the
index and ``_link_imports`` fell through to the external package minter, so a
module the graph demonstrably held first-party got a second, ``external=True``
representation beside its own file node. That was ~894 spurious nodes on the
evaluator's workspace and 1441 on this one.

The fix resolves an import against the importer's own ancestor directories,
deepest first, which covers both shapes the evaluator hit without needing to
*detect* a source root -- worth stating, because the fixture's
``src/acme_notify/__init__.py`` is empty and so has no file node to detect one
from.

Ordering is the subtle half and is pinned here against
``weld/_graph_closure_modules.py``: the literal spelling is tried first so
Python's absolute-import semantics hold, the ancestor walk answers only names
that resolve to nothing real, and the member-stripped reading of a
referenced-symbol capture ranks below both. The bounds on all of that -- what
resolution must refuse to do -- live in
``weld/tests/weld_graph_closure_python_guards_test.py``.
"""

from __future__ import annotations

import unittest

from weld.tests._graph_closure_python_fixture import (
    close,
    depends_on,
    file_node,
    package_ids,
)


class PackageRootRelativeImportTest(unittest.TestCase):
    """``src/acme_notify/runner.py``: ``from acme_notify.config import ...``.

    ``src/acme_notify`` is a package and ``src`` is the source root, so the
    module's import spelling drops the leading ``src``.
    """

    def setUp(self) -> None:
        self.nodes, self.edges = close({
            "file:src/acme_notify/config": file_node("src/acme_notify/config.py"),
            "file:src/acme_notify/runner": file_node(
                "src/acme_notify/runner.py",
                [
                    "acme_notify.config",
                    "acme_notify.config.DEFAULT_RETRIES",
                    "acme_notify.config.load_config",
                ],
            ),
        })

    def test_every_import_lands_on_the_file_node(self) -> None:
        by_name = depends_on(self.edges, "file:src/acme_notify/runner")
        self.assertEqual(
            sorted(by_name),
            [
                "acme_notify.config",
                "acme_notify.config.DEFAULT_RETRIES",
                "acme_notify.config.load_config",
            ],
        )
        for name, edge in sorted(by_name.items()):
            with self.subTest(import_name=name):
                self.assertEqual(edge["to"], "file:src/acme_notify/config")

    def test_the_referenced_symbol_form_resolves_via_its_module(self) -> None:
        """``a.b.C`` is module ``a.b`` plus a member, not a module ``a.b.C``.

        The referenced-symbol capture appends the imported name to the module
        path, which is the shape that produced N4's worst nodes
        (``package:python:acme_notify.config.load_config``).
        """
        edge = depends_on(
            self.edges, "file:src/acme_notify/runner"
        )["acme_notify.config.load_config"]
        self.assertEqual(edge["to"], "file:src/acme_notify/config")
        self.assertEqual(edge["props"]["resolution"], "local_module")
        self.assertEqual(edge["props"]["confidence"], "definite")

    def test_no_external_package_shadows_the_module(self) -> None:
        self.assertEqual(package_ids(self.nodes), [])


class ImporterDirectoryRelativeImportTest(unittest.TestCase):
    """``src/main.py``: ``from broker import Subscriber``.

    Here the importer's own directory *is* the source root, so the bare name
    resolves one level up from the repo-relative path.
    """

    def setUp(self) -> None:
        self.nodes, self.edges = close({
            "file:src/broker": file_node("src/broker.py"),
            "file:src/handlers/order_placed_handler": file_node(
                "src/handlers/order_placed_handler.py"
            ),
            "file:src/main": file_node(
                "src/main.py",
                [
                    "broker",
                    "broker.Subscriber",
                    "handlers.order_placed_handler",
                    "handlers.order_placed_handler.OrderPlacedHandler",
                ],
            ),
        })

    def test_bare_and_nested_first_party_modules_both_resolve(self) -> None:
        by_name = depends_on(self.edges, "file:src/main")
        self.assertEqual(
            {name: edge["to"] for name, edge in by_name.items()},
            {
                "broker": "file:src/broker",
                "broker.Subscriber": "file:src/broker",
                "handlers.order_placed_handler":
                    "file:src/handlers/order_placed_handler",
                "handlers.order_placed_handler.OrderPlacedHandler":
                    "file:src/handlers/order_placed_handler",
            },
        )

    def test_no_external_package_shadows_either_module(self) -> None:
        self.assertEqual(package_ids(self.nodes), [])


class AbsoluteReadingWinsOverRelativeTest(unittest.TestCase):
    """A real module at the name's own spelling beats an ancestor-relative one.

    Python 3 resolves ``import c.d`` against ``sys.path``, so when ``c/d.py``
    exists at the repository root it is the answer even though the importer
    also has an ``a/c/d.py`` beside it. Ancestor-relative reading is a
    fallback for names that resolve to nothing real, never an override.
    """

    def test_the_root_module_wins_over_the_importers_sibling(self) -> None:
        _, edges = close({
            "file:c/d": file_node("c/d.py"),
            "file:a/c/d": file_node("a/c/d.py"),
            "file:a/b": file_node("a/b.py", ["c.d"]),
        })
        self.assertEqual(depends_on(edges, "file:a/b")["c.d"]["to"], "file:c/d")

    def test_the_relative_reading_answers_when_nothing_else_does(self) -> None:
        _, edges = close({
            "file:a/c/d": file_node("a/c/d.py"),
            "file:a/b": file_node("a/b.py", ["c.d"]),
        })
        self.assertEqual(
            depends_on(edges, "file:a/b")["c.d"]["to"], "file:a/c/d"
        )


class DeeperAncestorWinsTest(unittest.TestCase):
    """The nearest enclosing source root wins over a farther one.

    Two repos vendored side by side can both hold ``config.py`` under their
    own ``src``. The importer's closest ancestor is the one whose source root
    it actually sits in, so the walk goes deepest-first.
    """

    def test_the_closest_matching_ancestor_is_chosen(self) -> None:
        _, edges = close({
            "file:src/config": file_node("src/config.py"),
            "file:src/vendor/pkg/config": file_node("src/vendor/pkg/config.py"),
            "file:src/vendor/pkg/app": file_node(
                "src/vendor/pkg/app.py", ["config"]
            ),
        })
        self.assertEqual(
            depends_on(edges, "file:src/vendor/pkg/app")["config"]["to"],
            "file:src/vendor/pkg/config",
        )


class ParentModuleNeverOutranksTheWholeNameTest(unittest.TestCase):
    """``import a.b`` resolves to ``a.b``, never to its parent ``a``.

    The member-stripped reading is a fallback for the referenced-symbol
    capture shape; letting it run before the literal lookup would answer every
    submodule import with its own package's ``__init__``.
    """

    def test_the_full_module_wins_over_its_package_init(self) -> None:
        _, edges = close({
            "file:pkg/__init__": file_node("pkg/__init__.py"),
            "file:pkg/sub": file_node("pkg/sub.py"),
            "file:app": file_node("app.py", ["pkg.sub"]),
        })
        self.assertEqual(
            depends_on(edges, "file:app")["pkg.sub"]["to"], "file:pkg/sub"
        )


class ResolutionIsDeterministicTest(unittest.TestCase):
    """Node insertion order must not decide which file an import lands on.

    ``_module_index`` is order-independent by construction; the ancestor walk
    must not reintroduce an ordering dependence through the back door, so the
    same graph is closed twice with the node dict built in opposite orders.
    """

    IMPORTS = ["acme_notify.config", "acme_notify.config.load_config"]

    def _targets(self, reverse: bool) -> dict[str, str]:
        spec = [
            ("file:src/acme_notify/config", file_node("src/acme_notify/config.py")),
            (
                "file:src/acme_notify/runner",
                file_node("src/acme_notify/runner.py", list(self.IMPORTS)),
            ),
        ]
        nodes = dict(reversed(spec) if reverse else spec)
        _, edges = close(nodes)
        by_name = depends_on(edges, "file:src/acme_notify/runner")
        return {name: edge["to"] for name, edge in by_name.items()}

    def test_both_insertion_orders_agree(self) -> None:
        self.assertEqual(self._targets(False), self._targets(True))




class ModuleNameAgreementTest(unittest.TestCase):
    """The name the walk looks a definition up by is the name ids are minted under.

    ``python_callgraph`` derives a symbol id's module from the defining file's
    path; ``weld._graph_closure_modules.python_dotted_module`` re-derives it from
    ``props.file``. The two cannot be one function -- the strategy reads a live
    filesystem path through ``pathlib.Path``, the closure reads the POSIX
    spelling the graph stores -- so the agreement is pinned rather than assumed.
    A drift here would not raise: the walk would look up an id nothing mints,
    find nothing, and quietly stop resolving facades.
    """

    _PATHS = (
        "pkg/mod.py",
        "pkg/__init__.py",
        "pkg/sub/deep/mod.py",
        "mod.py",
        "__init__.py",
    )

    def test_the_closure_and_the_strategy_spell_a_module_the_same(self) -> None:
        from weld._graph_closure_modules import python_dotted_module
        from weld.strategies.python_callgraph import _module_dotted_path

        for rel_path in self._PATHS:
            with self.subTest(path=rel_path):
                self.assertEqual(
                    python_dotted_module(rel_path), _module_dotted_path(rel_path)
                )

    def test_a_non_python_path_has_no_module_name(self) -> None:
        """The closure's own addition: a path index holds every language."""
        from weld._graph_closure_modules import python_dotted_module

        for rel_path in ("pkg/mod.ts", "pkg/mod.go", "pkg/mod"):
            with self.subTest(path=rel_path):
                self.assertEqual(python_dotted_module(rel_path), "")

if __name__ == "__main__":
    unittest.main()
