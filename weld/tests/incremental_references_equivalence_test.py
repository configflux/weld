"""Incremental == full for ADR 0127's ``references`` edge kind (bd lid2).

``purge_edges_by_provenance`` (``weld/_incremental_purge.py``) and
``orphaned_producer_files`` (``weld/_discover_orphan_edges.py``) are both
edge-type- and node-type-agnostic: they key only on
``props.provenance.file`` presence, never on ``edge["type"]`` or either
endpoint's node type. ADR 0074's four amendments should therefore already
protect the new ``references`` edge population automatically, with no
orchestrator change, *provided* it stamps provenance the same way
``python_callgraph``'s existing ``calls``/``decorates`` edges do.

This is the dedicated test that verifies that claim rather than just
asserting it -- the same discipline
``incremental_decorates_scope_calls_equivalence_test.py`` used for ADR
0122's two new edge populations. The fixture is deliberately minimal and
self-contained (distinct from both that file's fixture and the shared
``incremental_cross_source_equivalence_test.py`` one) so this addition
carries zero risk to either already-pinned shape: two python files, a
same-module reference in one (ADR 0127's own scope -- see the ADR's
"Mind index/graph growth" discussion for why cross-module stays out) and
an unrelated edit surface in the other, swept through edit and delete
rounds against a full discover of the identical end state.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo  # noqa: E402

EDITABLE = ("src/alpha.py", "src/beta.py")


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


def _write_fixture(root: Path, edited: str | None) -> None:
    """Write the fixture, giving *edited* (if any) its changed-content variant.

    ``alpha.py`` defines a class (``Tool``) and a function
    (``build_tools``) that names it as a bare-name keyword-argument VALUE
    in its own module -- the exact bd lid2 shape, and the only edge kind
    this fixture needs to prove: a same-module ``references`` edge sourced
    at ``build_tools``. ``beta.py`` carries no reference at all; it exists
    purely as the unrelated edit/delete surface that must NOT disturb
    alpha's clean-provenance edge on an incremental round.
    """
    def body(rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == edited:
            text = text + "\n# edited\n"
        path.write_text(text, encoding="utf-8")

    body("src/__init__.py", "")
    body(
        "src/alpha.py",
        "class Tool:\n    pass\n\n\n"
        "def other(tool_cls):\n    return tool_cls\n\n\n"
        "def build_tools():\n    return other(tool_cls=Tool)\n",
    )
    body("src/beta.py", "def unrelated():\n    return 1\n")
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
    meta.pop("discovered_from", None)
    meta.pop("updated_at", None)
    meta.pop("git_sha", None)
    g["meta"] = meta
    return g


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["to"], e["type"]) for e in graph.get("edges", [])}


def _incremental_graph(edited: str | None) -> dict:
    """Seed a full discover, edit *edited*, refresh incrementally."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, None)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, edited)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_graph(edited: str | None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, edited)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class ReferencesEquivalenceTest(unittest.TestCase):
    def test_every_single_file_edit_matches_full(self) -> None:
        for edited in EDITABLE:
            with self.subTest(edited=edited):
                inc, full = _incremental_graph(edited), _full_graph(edited)
                inc_e, full_e = _edge_set(inc), _edge_set(full)
                self.assertEqual(
                    inc_e, full_e,
                    f"editing {edited} diverged the incremental edge set "
                    f"from a full discover over the identical tree "
                    f"(full-only={sorted(full_e - inc_e)}, "
                    f"inc-only={sorted(inc_e - full_e)})",
                )
                self.assertEqual(_strip_meta(inc), _strip_meta(full))

    def test_fixture_actually_exercises_the_new_edge_kind(self) -> None:
        """Guard the guard: if the fixture stops emitting a ``references``
        edge, the equivalence test above would trivially pass without
        proving anything."""
        full = _full_graph(None)
        references = [e for e in full["edges"] if e["type"] == "references"]
        self.assertTrue(references, "fixture must emit at least one references edge")
        self.assertEqual(
            (references[0]["from"], references[0]["to"]),
            ("symbol:py:src.alpha:build_tools", "symbol:py:src.alpha:Tool"),
        )

    def test_unrelated_file_edit_leaves_the_reference_edge_untouched(self) -> None:
        """Editing beta.py (which carries no reference) must not disturb
        alpha's clean-provenance references edge on an incremental round --
        the specific cross-file purge-safety claim this file exists to
        check for the new edge kind."""
        inc = _incremental_graph("src/beta.py")
        inc_e = _edge_set(inc)
        self.assertIn(
            ("symbol:py:src.alpha:build_tools", "symbol:py:src.alpha:Tool",
             "references"),
            inc_e,
        )

    def test_deleted_beta_still_matches_full(self) -> None:
        """The orphan-edge / purge path (ADR 0074 fourth amendment, bd
        znzu), pinned for ``references`` specifically: deleting the file
        that carries no reference must not disturb alpha's edge, and the
        incremental result must match a full discover of the same
        deleted-beta end state.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, None)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            (root / "src" / "beta.py").unlink()
            _commit(root)
            inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, None)
            (root / "src" / "beta.py").unlink()
            _commit(root)
            full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_e, full_e = _edge_set(inc), _edge_set(full)
        self.assertEqual(
            inc_e, full_e,
            f"deleting beta.py diverged the incremental edge set "
            f"(full-only={sorted(full_e - inc_e)}, "
            f"inc-only={sorted(inc_e - full_e)})",
        )
        self.assertEqual(_strip_meta(inc), _strip_meta(full))
        self.assertIn(
            ("symbol:py:src.alpha:build_tools", "symbol:py:src.alpha:Tool",
             "references"),
            inc_e,
        )


if __name__ == "__main__":
    unittest.main()
