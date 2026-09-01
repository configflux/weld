"""Incremental == full for the imported-value attribute call, through discover().

``<from-imported value>.<method>()`` used to resolve as if the value were a
module alias and mint ``symbol:py:<module>:<method>`` -- a first-party stub
naming a function that exists under no spelling. It now falls through to the
``symbol:unresolved:<method>`` sentinel. The resolution itself is per-file and
re-derived from the caller's own AST every round, so no endpoint is retained
the way ``incremental_reexport_equivalence_test``'s retarget is; what this file
exists for is the population the change *moves* work onto.

Two hazards, both about the sentinel rather than about the resolution:

* The sentinel id is a bare-name-keyed namespace shared across every strategy
  that fails to resolve the same name, so bd oao53's purge counts inbound edges
  of any type. Deleting the sole caller must leave no ``symbol:unresolved:get``
  behind on either path -- and the caller's file is where both the edge and its
  provenance live, so the incremental path has to reach that node through the
  zero-inbound rule, not through ``props.file``.
* Editing the *definer* while the caller stays clean never re-walks the caller.
  A full discover of the same tree resolves the call from the caller's AST
  alone, so the retained edge has to agree with it for free. That round is the
  cheap pin; the delete round is the one with a mechanism behind it.

The fixture is its own minimal cast, matching how every member of this family
isolates a new population: a module holding one constant and one real function,
and a caller that reaches the constant's method and the function itself in the
same body. Both call shapes in one caller is deliberate -- it pins that the fix
narrowed the attribute branch only, leaving the bare-name import lookup that
shares the same table entry untouched.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

EDITABLE = ("src/tables.py", "src/caller.py")
CALLER = "symbol:py:src.caller:run"
FUNCTION = "symbol:py:src.tables:lookup"
SENTINEL = "symbol:unresolved:get"
FABRICATED = "symbol:py:src.tables:get"


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
    body(
        "src/tables.py",
        'TABLE = {"a": 1}\n\n\ndef lookup(key):\n    return TABLE[key]\n',
    )
    body(
        "src/caller.py",
        "from src.tables import TABLE, lookup\n\n\n"
        "def run():\n    return TABLE.get(lookup(\"a\"))\n",
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


class ImportedValueAttrIsRealTest(unittest.TestCase):
    """The fixture actually exercises the new branch, on the real discover path.

    Without this, every equivalence assertion below could pass on a graph where
    the value attribute call never appeared at all.
    """

    def setUp(self) -> None:
        self.graph = _full_graph(None)

    def test_the_value_attr_call_lands_on_the_sentinel(self) -> None:
        self.assertIn((CALLER, SENTINEL, "calls"), _edge_set(self.graph))

    def test_the_fabricated_sibling_is_absent(self) -> None:
        self.assertNotIn(FABRICATED, self.graph["nodes"])

    def test_the_bare_name_import_still_resolves(self) -> None:
        """The same import-table entry still resolves ``lookup()`` itself."""
        self.assertIn((CALLER, FUNCTION, "calls"), _edge_set(self.graph))


class ImportedValueAttrEquivalenceTest(unittest.TestCase):
    def test_every_single_file_edit_matches_full(self) -> None:
        for edited in EDITABLE:
            with self.subTest(edited=edited):
                self.assertEqual(
                    _strip_meta(_incremental_graph(edited)),
                    _strip_meta(_full_graph(edited)),
                )

    def test_deleting_the_definer_matches_full(self) -> None:
        """The imported module goes; the caller's own call shapes do not change."""
        self.assertEqual(
            _strip_meta(_incremental_graph(None, deleted="src/tables.py")),
            _strip_meta(_full_graph(None, deleted="src/tables.py")),
        )

    def test_deleting_the_caller_matches_full(self) -> None:
        """The round with a mechanism behind it -- see the module docstring."""
        self.assertEqual(
            _strip_meta(_incremental_graph(None, deleted="src/caller.py")),
            _strip_meta(_full_graph(None, deleted="src/caller.py")),
        )

    def test_deleting_the_caller_leaves_no_orphan_sentinel(self) -> None:
        """Both paths agree, and they agree on the state a full run produces.

        Equality alone would be satisfied by both paths keeping a zero-inbound
        sentinel, which is the leak bd oao53's purge exists for -- so the shape
        is named outright rather than left to the comparison.
        """
        graph = _incremental_graph(None, deleted="src/caller.py")
        self.assertNotIn(SENTINEL, graph["nodes"])


if __name__ == "__main__":
    unittest.main()
