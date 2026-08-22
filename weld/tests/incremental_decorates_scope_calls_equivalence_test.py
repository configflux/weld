"""Incremental == full for ADR 0122's new edge kinds (bd vysw).

``purge_edges_by_provenance`` (``weld/_incremental_purge.py``) and
``orphaned_producer_files`` (``weld/_discover_orphan_edges.py``) are both
edge-type- and node-type-agnostic: they key only on
``props.provenance.file`` presence, never on ``edge["type"]`` or either
endpoint's node type. ADR 0074's four amendments should therefore already
protect the two new edge populations -- ``decorates`` and file-sourced
``calls`` -- automatically, with no orchestrator change, *provided* they
stamp provenance the same way ``python_callgraph``'s existing ``calls``/
``inherits`` edges do.

This is the dedicated test that verifies that claim rather than just
asserting it. The fixture is deliberately minimal and self-contained
(distinct from the shared ``incremental_cross_source_equivalence_test.py``
fixture, which bd znzu/cpkp already own) so this addition carries zero risk
to that sensitive, already-pinned shape: two python files, a cross-file
decorator and a cross-file module-level call, swept through edit and
delete rounds against a full discover of the identical end state -- the
same shape bd znzu's fourth amendment proved necessary for the plain
``calls`` case.
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

    ``alpha.py`` defines a plain function (``alpha_fn``, a cross-file
    module-level-call target) and a decorator (``alpha_deco``, a
    cross-file decorates target). ``beta.py`` imports both: a
    module-level statement calls ``alpha_fn`` (sourced at ``file:src/beta``)
    and ``@alpha_deco`` decorates ``beta_fn`` (sourced at ``alpha_deco``,
    targeting ``beta_fn``) -- so both new edge kinds cross the exact
    producer/endpoint boundary bd znzu's amendment exists to protect: the
    producer (``beta.py``) is clean while the endpoint (an ``alpha.py``
    symbol) is the one that goes stale.
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
        "def alpha_fn():\n    return 1\n\n\ndef alpha_deco(f):\n    return f\n",
    )
    body(
        "src/beta.py",
        "from src.alpha import alpha_deco, alpha_fn\n\n\n"
        "@alpha_deco\n"
        "def beta_fn():\n"
        "    return 1\n\n\n"
        "MODULE_CALL = alpha_fn()\n",
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
    meta.pop("discovered_from", None)
    meta.pop("updated_at", None)
    meta.pop("git_sha", None)
    g["meta"] = meta
    return g


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["to"], e["type"]) for e in graph.get("edges", [])}


def _incremental_graph(edited: str | None) -> dict:
    """Seed a full discover, edit *edited* (or delete it if None-sentinel
    is not used here -- deletion has its own test), refresh incrementally."""
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


class DecoratesScopeCallsEquivalenceTest(unittest.TestCase):
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

    def test_fixture_actually_exercises_both_new_edge_kinds(self) -> None:
        """Guard the guard: if the fixture stops emitting a ``decorates``
        edge or a file-sourced ``calls`` edge, the equivalence test above
        would trivially pass without proving anything."""
        full = _full_graph(None)
        edge_types_from_file = {
            (e["type"]) for e in full["edges"] if e["from"] == "file:src/beta"
        }
        self.assertIn("calls", edge_types_from_file)
        decorates = [e for e in full["edges"] if e["type"] == "decorates"]
        self.assertTrue(decorates, "fixture must emit at least one decorates edge")
        self.assertEqual(
            decorates[0]["to"], "symbol:py:src.beta:beta_fn",
        )

    def test_deleted_producer_of_the_decorated_symbol_still_matches_full(self) -> None:
        """The orphan-edge re-mint path (ADR 0074 fourth amendment, bd
        znzu), pinned for the two new edge kinds specifically: deleting
        ``alpha.py`` purges the real ``alpha_fn``/``alpha_deco`` nodes;
        ``beta.py``'s clean-provenance edges into them must survive the
        purge, dangle when the endpoint doesn't come back, and then get
        ``beta.py`` re-parsed once to re-mint the same cross-module
        stand-in a full discover would produce -- exactly like the plain
        ``calls`` case bd znzu fixed, now exercised for ``decorates`` and
        a file-sourced ``calls`` edge too.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, None)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            (root / "src" / "alpha.py").unlink()
            _commit(root)
            inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, None)
            (root / "src" / "alpha.py").unlink()
            _commit(root)
            full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_e, full_e = _edge_set(inc), _edge_set(full)
        self.assertEqual(
            inc_e, full_e,
            f"deleting alpha.py diverged the incremental edge set "
            f"(full-only={sorted(full_e - inc_e)}, "
            f"inc-only={sorted(inc_e - full_e)})",
        )
        self.assertEqual(_strip_meta(inc), _strip_meta(full))
        # Both new edge kinds must have survived the delete-and-remint
        # round, pointing at the same cross-module stand-in a full
        # discover mints -- not silently dropped, not left dangling.
        self.assertIn(
            ("symbol:py:src.alpha:alpha_deco", "symbol:py:src.beta:beta_fn",
             "decorates"),
            inc_e,
        )
        self.assertIn(
            ("file:src/beta", "symbol:py:src.alpha:alpha_fn", "calls"), inc_e,
        )


if __name__ == "__main__":
    unittest.main()
