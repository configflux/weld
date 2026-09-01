"""Incremental == full for a classmethod reached through an imported class.

``from lib.tables import Corpus`` + ``Corpus.build()`` is the deferred shape
whose answer is the method's own symbol, ``symbol:py:lib.tables:Corpus.build``.
``python_callgraph`` cannot reach it from one glob's import table -- a class and
a dict are the same non-empty attr slot there -- so it records the hint and
``weld._graph_closure_import_attr`` decides against the merged node set, inside
``close_graph``, which both discover paths run once.

What this file pins is what the sibling
``incremental_cross_glob_submodule_equivalence_test`` pins for the submodule
reading, because this rule shares its one dangerous property: it moves an
endpoint on a *retained* edge. An incremental round never re-walks a clean
caller, so a retarget made in an earlier round would be inherited no matter
what happened to the class since -- and nothing dangles to force the re-walk,
because the endpoint it landed on is a real node that is still there right up
until the definition goes.

The class-base reading has one degradation the submodule reading does not, and
it gets its own round here: **the method alone can disappear**. Deleting
``lib/tables.py`` takes the class with it, which is the file-shaped round the
sibling already covers; deleting just ``build`` leaves the class, the file, and
the module all standing, and only the member the caller named is gone. A pass
that keyed its undo on "is the module still there" would sail through that one
and keep pointing a resolved edge at a symbol nothing defines any more.

Two globs, and the caller's own glob owns no ``lib`` module of any kind, so the
merged view is the only thing that can answer. Both declaration orders run for
the same reason they do next door: the strategy publishes a run-level module
union as it goes, and reading which order a config happens to use is not a
thing a user should have to do.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

EDITABLE = ("lib/tables.py", "tools/go.py")
CALLER = "symbol:py:tools.go:go"
CLASS = "symbol:py:lib.tables:Corpus"
RESOLVED = "symbol:py:lib.tables:Corpus.build"
SENTINEL = "symbol:unresolved:build"
#: The id this shape minted before the imported-value fix -- a first-party
#: ``build`` under a module that defines no such name.
FABRICATED = "symbol:py:lib.tables:build"

_CLASS_WITH_METHOD = (
    'TABLE = {"a": 1}\n\n\n'
    "class Corpus:\n"
    "    @classmethod\n"
    "    def build(cls, rows):\n"
    "        return cls()\n"
)
#: Same class, same file, same module -- only the member the caller names is
#: gone. ``fields`` keeps the class non-empty so its symbol still exists.
_CLASS_WITHOUT_METHOD = (
    'TABLE = {"a": 1}\n\n\n'
    "class Corpus:\n"
    "    def fields(self):\n"
    "        return TABLE\n"
)

_LIB_ENTRIES = (
    "  - strategy: python_module\n"
    "    glob: lib/**/*.py\n"
    "    type: file\n"
    "  - strategy: python_callgraph\n"
    "    glob: lib/**/*.py\n"
    "    type: symbol\n"
)
_TOOLS_ENTRIES = (
    "  - strategy: python_module\n"
    "    glob: tools/*.py\n"
    "    type: file\n"
    "  - strategy: python_callgraph\n"
    "    glob: tools/*.py\n"
    "    type: symbol\n"
)
#: The two declaration orders, keyed by which glob is declared first.
ORDERS = {
    "lib_first": _LIB_ENTRIES + _TOOLS_ENTRIES,
    "tools_first": _TOOLS_ENTRIES + _LIB_ENTRIES,
}


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _write_fixture(
    root: Path,
    sources: str,
    edited: str | None,
    deleted: str | None = None,
    drop_method: bool = False,
) -> None:
    """Write the fixture, with *edited* changed and *deleted* absent.

    ``drop_method`` rewrites ``lib/tables.py`` to the same class minus
    ``build`` -- the degradation that leaves every file and module in place.
    """

    def body(rel: str, text: str) -> None:
        if rel == deleted:
            (root / rel).unlink(missing_ok=True)
            return
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == edited:
            text = text + "\n# edited\n"
        path.write_text(text, encoding="utf-8")

    body("lib/__init__.py", "")
    body(
        "lib/tables.py",
        _CLASS_WITHOUT_METHOD if drop_method else _CLASS_WITH_METHOD,
    )
    body(
        "tools/go.py",
        "from lib.tables import Corpus\n\n\n"
        "def go():\n    return Corpus.build([])\n",
    )
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n" + sources, encoding="utf-8"
    )


def _strip_meta(graph: dict) -> dict:
    g = dict(graph)
    meta = dict(g.get("meta", {}))
    for key in ("discovered_from", "updated_at", "git_sha"):
        meta.pop(key, None)
    g["meta"] = meta
    return g


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["to"], e["type"]) for e in graph.get("edges", [])}


def _full_graph(sources: str, edited: str | None, **mutation) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, sources, edited, **mutation)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


def _incremental_graph(sources: str, edited: str | None, **mutation) -> dict:
    """Seed a full discover of the intact tree, mutate it, refresh incrementally."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, sources, None)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, sources, edited, **mutation)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


class ClassBaseAnswerTest(unittest.TestCase):
    """Both paths reach the method's own symbol, in both declaration orders.

    Named rather than left to the comparison below: equality alone is satisfied
    by both paths agreeing on the *sentinel*, which is what both gave before
    this rule existed.
    """

    def test_full_discover_resolves_the_method_symbol(self) -> None:
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                graph = _full_graph(sources, None)
                self.assertIn((CALLER, RESOLVED, "calls"), _edge_set(graph))

    def test_incremental_discover_reaches_the_same_answer(self) -> None:
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                graph = _incremental_graph(sources, "tools/go.py")
                self.assertIn((CALLER, RESOLVED, "calls"), _edge_set(graph))

    def test_neither_path_keeps_a_sentinel_or_the_fabricated_id(self) -> None:
        """``symbol:py:lib.tables:build`` is what this call used to mint."""
        for name, sources in ORDERS.items():
            for build in (_full_graph, _incremental_graph):
                with self.subTest(order=name, path=build.__name__):
                    graph = build(sources, "tools/go.py")
                    self.assertNotIn(SENTINEL, graph["nodes"])
                    self.assertNotIn(FABRICATED, graph["nodes"])


class ClassBaseEquivalenceTest(unittest.TestCase):
    def test_every_single_file_edit_matches_full(self) -> None:
        for name, sources in ORDERS.items():
            for edited in EDITABLE:
                with self.subTest(order=name, edited=edited):
                    self.assertEqual(
                        _strip_meta(_incremental_graph(sources, edited)),
                        _strip_meta(_full_graph(sources, edited)),
                    )

    def test_deleting_the_definer_matches_full(self) -> None:
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                self.assertEqual(
                    _strip_meta(
                        _incremental_graph(sources, None, deleted="lib/tables.py")
                    ),
                    _strip_meta(
                        _full_graph(sources, None, deleted="lib/tables.py")
                    ),
                )

    def test_deleting_the_definer_degrades_to_the_sentinel(self) -> None:
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                graph = _incremental_graph(sources, None, deleted="lib/tables.py")
                self.assertIn((CALLER, SENTINEL, "calls"), _edge_set(graph))
                self.assertNotIn(RESOLVED, graph["nodes"])

    def test_dropping_only_the_method_matches_full(self) -> None:
        """The round no module-shaped undo would catch.

        The class, its file, and its module all survive; only ``build`` is
        gone. The retained caller edge has to be restored and re-derived, and
        re-derivation has to notice that the member half of the proof no longer
        holds even though the class half still does.
        """
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                self.assertEqual(
                    _strip_meta(
                        _incremental_graph(sources, None, drop_method=True)
                    ),
                    _strip_meta(_full_graph(sources, None, drop_method=True)),
                )

    def test_dropping_only_the_method_degrades_to_the_sentinel(self) -> None:
        """And it degrades to a miss, not to a stale edge or a minted stub."""
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                graph = _incremental_graph(sources, None, drop_method=True)
                self.assertIn((CALLER, SENTINEL, "calls"), _edge_set(graph))
                self.assertNotIn(RESOLVED, graph["nodes"])
                self.assertIn(CLASS, graph["nodes"])

    def test_deleting_the_caller_matches_full(self) -> None:
        """No orphan is left behind on either side of the retarget."""
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                self.assertEqual(
                    _strip_meta(
                        _incremental_graph(sources, None, deleted="tools/go.py")
                    ),
                    _strip_meta(_full_graph(sources, None, deleted="tools/go.py")),
                )


if __name__ == "__main__":
    unittest.main()
