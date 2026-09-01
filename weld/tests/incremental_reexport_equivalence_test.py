"""Incremental == full for the re-export retarget, through the real discover().

``weld._graph_closure_reexport`` is the first closure rule that moves an
endpoint on an edge the strategies already emitted. Every other rule in
``close_graph`` re-derives its output from node props each round and is
self-correcting for free -- delete a module and ``_link_imports`` re-resolves
the importer's retained ``imports_from`` against the new index. A retarget has
no such property: the caller of a re-exported symbol is not dirty when the
facade or the definition changes, so its already-moved edge is retained
verbatim while a full discover of the same tree would resolve it differently.

Which delete round proves that was measured, not assumed, by disabling the undo
and seeing which assertion moved. Deleting the *definition* is not it: the
retained edge dangles, ADR 0074's widen-and-retry re-runs the caller as an
orphaned producer, and the stub is re-minted for free -- that round passes
either way and is kept as a pin, not as the proof.

Deleting the *facade* is the round the undo exists for, and it is the harder one
because nothing looks broken: the edge still names a symbol that still exists,
so nothing dangles, nothing re-runs, and the caller is never re-walked. The
facade's stub is then absent where a full discover mints it -- and because that
stub is what the module index binds the facade's name to once its file node is
gone, the caller's own ``depends_on`` lands on an external package on one path
and on the stub on the other.

The fixture is its own minimal three-file cast rather than an extension of the
``incremental_cross_source_equivalence_test`` one, matching how every other
member of this family isolates a new edge population: a definer, a facade whose
only job is to re-export, and a caller that reaches the symbol through it.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

EDITABLE = ("src/definer.py", "src/facade.py", "src/caller.py")
DEFINER = "symbol:py:src.definer:widget"
FACADE_STUB = "symbol:py:src.facade:widget"
CALLER = "symbol:py:src.caller:run"


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


def _write_fixture(root: Path, edited: str | None, deleted: str | None = None) -> None:
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

    body("src/__init__.py", "")
    body("src/definer.py", "def widget():\n    return 1\n")
    body(
        "src/facade.py",
        '"""Public import path; the implementation lives next door."""\n\n'
        "from src.definer import widget\n\n"
        '__all__ = ["widget"]\n',
    )
    body(
        "src/caller.py",
        "from src.facade import widget\n\n\n"
        "def run():\n    return widget()\n",
    )
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: src/**/*.py\n"
        "    type: file\n"
        "  - strategy: python_callgraph\n"
        "    glob: src/**/*.py\n"
        "    type: symbol\n",
        encoding="utf-8",
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


def _incremental_graph(edited: str | None, deleted: str | None = None) -> dict:
    """Seed a full discover of the intact tree, mutate it, refresh incrementally."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, None)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, edited, deleted)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_graph(edited: str | None, deleted: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, edited, deleted)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class ReexportRetargetIsRealTest(unittest.TestCase):
    """The fixture actually exercises the retarget, on the real discover path.

    Without this, every equivalence assertion below could pass on a graph where
    the facade never resolved at all.
    """

    def setUp(self) -> None:
        self.graph = _full_graph(None)

    def test_the_call_lands_on_the_definer(self) -> None:
        self.assertIn((CALLER, DEFINER, "calls"), _edge_set(self.graph))

    def test_the_facade_stub_is_absent(self) -> None:
        self.assertNotIn(FACADE_STUB, self.graph["nodes"])


class ReexportEquivalenceTest(unittest.TestCase):
    def test_every_single_file_edit_matches_full(self) -> None:
        for edited in EDITABLE:
            with self.subTest(edited=edited):
                self.assertEqual(
                    _strip_meta(_incremental_graph(edited)),
                    _strip_meta(_full_graph(edited)),
                )

    def test_deleting_the_definer_matches_full(self) -> None:
        """The retarget's target disappears; the dangling edge re-runs the caller."""
        self.assertEqual(
            _strip_meta(_incremental_graph(None, deleted="src/definer.py")),
            _strip_meta(_full_graph(None, deleted="src/definer.py")),
        )

    def test_deleting_the_definer_restores_the_stub(self) -> None:
        """Both paths agree, and they agree on the state a full run produces.

        Equality alone would be satisfied by both paths losing the edge, which
        is the failure this whole family exists to catch -- so the shape is
        named outright rather than left to the comparison.
        """
        graph = _incremental_graph(None, deleted="src/definer.py")
        self.assertIn(FACADE_STUB, graph["nodes"])
        self.assertIn((CALLER, FACADE_STUB, "calls"), _edge_set(graph))

    def test_deleting_the_facade_matches_full(self) -> None:
        """The round the undo is load-bearing for -- see the module docstring.

        Sabotage-verified: disabling the restore pass fails this assertion and
        no other in this file.
        """
        self.assertEqual(
            _strip_meta(_incremental_graph(None, deleted="src/facade.py")),
            _strip_meta(_full_graph(None, deleted="src/facade.py")),
        )

    def test_deleting_the_caller_matches_full(self) -> None:
        self.assertEqual(
            _strip_meta(_incremental_graph(None, deleted="src/caller.py")),
            _strip_meta(_full_graph(None, deleted="src/caller.py")),
        )


if __name__ == "__main__":
    unittest.main()
