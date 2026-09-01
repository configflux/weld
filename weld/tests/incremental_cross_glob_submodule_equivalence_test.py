"""Incremental == full for a submodule import whose module another glob owns.

``from lib import inner`` + ``inner.work()`` is the shape ``python_callgraph``
resolves by proving ``lib.inner`` is a first-party module. The strategy sees one
glob at a time, and the set it proves membership from is derived two different
ways: per-glob on a full discover, and (ADR 0074, deliberately) from the whole
post-purge prior node set across every glob on an incremental one. So when the
caller lives in one glob and ``lib/inner.py`` in another, the two paths reached
different answers for the same tree -- the incremental one resolved
``symbol:py:lib.inner:work`` and the full one fell to
``symbol:unresolved:work``. Not a recall difference: the incremental path
produced a graph a full discover of the same tree never produced.

Every other member of this family declares its globs so a call's target sits in
the same glob as its caller, which is exactly why none of them saw this. Two
globs is the whole fixture, and the caller's own glob owns no ``lib`` module of
any kind, so the only way to reach the right answer is the merged view.

The rule now lives in ``weld._graph_closure_import_attr``, inside
``close_graph``, which both paths run once over the whole merged graph -- so the
answer stops depending on which path asked. What this file pins is the pair:
that the answer is the *better* one on both paths (the full path was raised to
the incremental path's answer, not the other way round), and that the merged
view is the only thing deciding it -- deleting ``lib/inner.py`` degrades both
paths to the sentinel, which is the round the closure pass's undo exists for and
the one that fails if a retargeted endpoint is simply inherited.

Both glob declaration orders run, because the strategy publishes a run-level
module union as it goes: an order-sensitive fix would pass in one order and
fail in the other, and reading which order a config happens to use is not a
thing a user should have to do.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

EDITABLE = ("lib/inner.py", "tools/go.py")
CALLER = "symbol:py:tools.go:go"
RESOLVED = "symbol:py:lib.inner:work"
SENTINEL = "symbol:unresolved:work"
BARE_PARENT = "symbol:py:lib:work"

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
    root: Path, sources: str, edited: str | None, deleted: str | None = None,
) -> None:
    """Write the fixture, giving *edited* its changed variant and dropping *deleted*."""

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
    body("lib/inner.py", "def work():\n    return 1\n")
    body(
        "tools/go.py",
        "from lib import inner\n\n\ndef go():\n    return inner.work()\n",
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


def _full_graph(sources: str, edited: str | None, deleted: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, sources, edited, deleted)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


def _incremental_graph(
    sources: str, edited: str | None, deleted: str | None = None,
) -> dict:
    """Seed a full discover of the intact tree, mutate it, refresh incrementally."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, sources, None)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, sources, edited, deleted)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


class CrossGlobSubmoduleAnswerTest(unittest.TestCase):
    """Both paths reach the real submodule symbol, in both declaration orders.

    Equality on its own would be satisfied by both paths agreeing on the
    *sentinel*, which is the answer the full path used to give -- so the answer
    itself is named here rather than left to the comparison below.
    """

    def test_full_discover_resolves_the_real_submodule_symbol(self) -> None:
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                graph = _full_graph(sources, None)
                self.assertIn((CALLER, RESOLVED, "calls"), _edge_set(graph))

    def test_incremental_discover_keeps_the_same_answer(self) -> None:
        """The path that already had the better answer is not narrowed."""
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                graph = _incremental_graph(sources, "tools/go.py")
                self.assertIn((CALLER, RESOLVED, "calls"), _edge_set(graph))

    def test_neither_path_keeps_a_sentinel_or_a_bare_parent(self) -> None:
        """``symbol:py:lib:work`` never existed; ``symbol:unresolved:work`` is gone.

        The sentinel is materialised by the strategy before the closure runs, so
        an orphan left at zero inbound edges is a live leak, not a hypothetical.
        """
        for name, sources in ORDERS.items():
            for build in (_full_graph, _incremental_graph):
                with self.subTest(order=name, path=build.__name__):
                    graph = build(sources, "tools/go.py")
                    self.assertNotIn(SENTINEL, graph["nodes"])
                    self.assertNotIn(BARE_PARENT, graph["nodes"])


class CrossGlobSubmoduleEquivalenceTest(unittest.TestCase):
    def test_every_single_file_edit_matches_full(self) -> None:
        for name, sources in ORDERS.items():
            for edited in EDITABLE:
                with self.subTest(order=name, edited=edited):
                    self.assertEqual(
                        _strip_meta(_incremental_graph(sources, edited)),
                        _strip_meta(_full_graph(sources, edited)),
                    )

    def test_deleting_the_definer_matches_full(self) -> None:
        """The round the closure pass's undo exists for.

        ``lib/inner.py`` goes, so ``lib.inner`` stops being a module and a full
        discover of the post-delete tree answers the sentinel. The incremental
        round never re-walks the clean caller, so its retargeted endpoint has to
        be restored and re-derived rather than inherited.
        """
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                self.assertEqual(
                    _strip_meta(
                        _incremental_graph(sources, None, deleted="lib/inner.py")
                    ),
                    _strip_meta(_full_graph(sources, None, deleted="lib/inner.py")),
                )

    def test_deleting_the_definer_degrades_to_the_sentinel(self) -> None:
        """Both paths agree, and they agree on what a full discover produces."""
        for name, sources in ORDERS.items():
            with self.subTest(order=name):
                graph = _incremental_graph(sources, None, deleted="lib/inner.py")
                self.assertIn((CALLER, SENTINEL, "calls"), _edge_set(graph))
                self.assertNotIn(RESOLVED, graph["nodes"])

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
