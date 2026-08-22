"""Incremental == full for a resolved cross-glob call-target stub (bd n4nvt).

Sibling of ``incremental_unresolved_symbol_purge_equivalence_test`` (bd
oao53), which fixed the same orphan-survival shape for
``symbol:unresolved:*`` sentinels. This is the shape that module's own
docstring explicitly tracked as a non-goal:
``weld.strategies._python_origin.make_resolved_target_node`` mints a
speculative stub at a REAL ``symbol:py:<module>:<qual>`` id -- not the
``symbol:unresolved:`` namespace -- for a call/inherits/references/
scope-call/decorates target that resolves via import-table syntax to a
project-shaped id the current batch did not itself walk: a cross-glob
target, or a module outside every configured glob. The node carries no
``props.file`` either, so it fell to the same blind spot -- but oao53's
id-prefix rule was deliberately scoped to never reach it, because this id
shape is indistinguishable in FORM from a real, walked symbol id.

Empirically reproduced (this bd issue's own investigation, before any fix
existed): a single-glob fixture where a project file's import resolves to a
module entirely outside every configured glob mints the stub on a full
discover; deleting that sole referencing file leaves the stub behind in the
incremental graph with zero inbound edges, which a fresh full discover of
the same post-delete tree never mints. Fixed by extending
:func:`weld.discovery_state.purge_stale_nodes` to also purge a resolved
stub once every inbound edge OF ANY TYPE it had is gone post-purge
(:mod:`weld._discover_resolved_stub_purge`) -- keyed on the node's own
props (confidence/authority/source_strategy/file-absence), never on id
shape, since id shape alone cannot distinguish this stub from a real symbol.

The tests below prove END TO END -- through the real ``discover()``
incremental path -- that this purge extension gives full node+edge
equivalence for the sole-referencer-delete case, the no-over-purge case
(second referencer survives), the negative-space case (a real, uncalled
function is never collaterally purged), and the stub -> real upgrade path
(ADR 0103's confidence-rank veto) stays undisturbed.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo


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


def _node_ids(graph: dict) -> set[str]:
    return set(graph.get("nodes", {}).keys())


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["type"], e["to"]) for e in graph.get("edges", [])}


def _strip_meta(graph: dict) -> dict:
    """Drop volatile keys, plus ``discovered_from`` -- its ORDER (not set)
    legitimately differs between the two construction paths (bd 8084), an
    orthogonal concern already covered elsewhere and not duplicated here."""
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


_STUB_ID = "symbol:py:pkg_b.callee:target_func"


def _write_yaml(root: Path, *globs: str) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    entries = "\n".join(
        f'  - glob: "{g}"\n    type: symbol\n    strategy: python_callgraph'
        for g in globs
    )
    (weld_dir / "discover.yaml").write_text(f"sources:\n{entries}\n", encoding="utf-8")


def _write_caller(root: Path, rel_dir: str, rel_name: str) -> None:
    d = root / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / rel_name).write_text(
        "from pkg_b.callee import target_func\n\n\n"
        "def do_call():\n    target_func()\n",
        encoding="utf-8",
    )


def _write_callee(root: Path) -> None:
    d = root / "pkg_b"
    d.mkdir(parents=True, exist_ok=True)
    (d / "callee.py").write_text("def target_func():\n    pass\n", encoding="utf-8")


def _write_cross_glob_fixture(root: Path) -> None:
    """pkg_b is never covered by any glob -- the stub is permanent (never
    upgraded to real) until the whole tree is proven equivalent."""
    _write_yaml(root, "pkg_a/**/*.py")
    _write_caller(root, "pkg_a", "caller.py")
    _write_callee(root)  # on disk, but outside every configured glob


class SoleReferencerDeletedEquivalenceTest(unittest.TestCase):
    def test_python_cross_glob_calls_edge(self) -> None:
        with tempfile.TemporaryDirectory(prefix="py-resstub-inc-") as td:
            root = Path(td)
            _git(root)
            _write_cross_glob_fixture(root)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                _STUB_ID, _node_ids(g_baseline),
                "fixture setup assumption broken: the baseline full run must "
                f"mint {_STUB_ID} for the delete round to exercise anything",
            )
            (root / "pkg_a" / "caller.py").unlink()
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)
            # Determinism: a second incremental pass over the same
            # (now-unchanged) tree must report the identical set.
            g_inc_again = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory(prefix="py-resstub-full-") as td:
            root = Path(td)
            _git(root)
            _write_cross_glob_fixture(root)
            (root / "pkg_a" / "caller.py").unlink()
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_nodes, full_nodes = _node_ids(g_inc), _node_ids(g_full)
        self.assertEqual(
            inc_nodes, full_nodes,
            f"incremental node set diverged from a full discover after "
            f"deleting the sole referencer (full-only="
            f"{sorted(full_nodes - inc_nodes)}, inc-only="
            f"{sorted(inc_nodes - full_nodes)})",
        )
        self.assertEqual(_edge_set(g_inc), _edge_set(g_full))
        self.assertEqual(_strip_meta(g_inc), _strip_meta(g_full))
        self.assertNotIn(
            _STUB_ID, inc_nodes,
            f"{_STUB_ID} survived incremental discovery as a zero-inbound-"
            "edge orphan after its sole referencer was deleted (bd n4nvt)",
        )
        self.assertEqual(
            inc_nodes, _node_ids(g_inc_again),
            "stub purge must be deterministic across repeated incremental "
            "passes, not merely correct on the first",
        )

    def test_python_cross_glob_inherits_edge(self) -> None:
        """Same mechanism, a DIFFERENT edge kind: ``_python_inherits`` calls
        the identical ``make_resolved_target_node`` helper for a cross-glob
        base class, proving the purge rule (keyed on the node's own props,
        not on which of the five python_* modules minted it) is not a
        ``calls``-only fix."""
        stub_id = "symbol:py:pkg_b.base:Base"

        def _write(root: Path) -> None:
            _write_yaml(root, "pkg_a/**/*.py")
            d = root / "pkg_a"
            d.mkdir(parents=True, exist_ok=True)
            (d / "sub.py").write_text(
                "from pkg_b.base import Base\n\n\nclass Sub(Base):\n    pass\n",
                encoding="utf-8",
            )
            b = root / "pkg_b"
            b.mkdir(parents=True, exist_ok=True)
            (b / "base.py").write_text("class Base:\n    pass\n", encoding="utf-8")

        with tempfile.TemporaryDirectory(prefix="py-resstub-inh-inc-") as td:
            root = Path(td)
            _git(root)
            _write(root)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(stub_id, _node_ids(g_baseline))
            (root / "pkg_a" / "sub.py").unlink()
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory(prefix="py-resstub-inh-full-") as td:
            root = Path(td)
            _git(root)
            _write(root)
            (root / "pkg_a" / "sub.py").unlink()
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(_node_ids(g_inc), _node_ids(g_full))
        self.assertNotIn(stub_id, _node_ids(g_inc))


class NonSoleReferencerDeletedTest(unittest.TestCase):
    """No over-purge: two Python files call the same cross-glob target;
    deleting ONE must leave the stub alive, carrying only the surviving
    caller's edge -- matching what a full run over the same
    partially-emptied tree would still emit."""

    def test_incremental_keeps_the_stub_after_deleting_one_of_two_callers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="py-resstub-partial-inc-") as td:
            root = Path(td)
            _git(root)
            _write_yaml(root, "pkg_a/**/*.py")
            _write_caller(root, "pkg_a", "caller.py")
            _write_caller(root, "pkg_a", "other_caller.py")
            _write_callee(root)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(_STUB_ID, _node_ids(g_baseline))
            (root / "pkg_a" / "caller.py").unlink()
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        inc_nodes, inc_edges = _node_ids(g_inc), _edge_set(g_inc)
        self.assertIn(
            _STUB_ID, inc_nodes,
            "a stub with a surviving referencer must keep its node",
        )
        self.assertIn(
            ("symbol:py:pkg_a.other_caller:do_call", "calls", _STUB_ID), inc_edges,
        )
        self.assertNotIn(
            ("symbol:py:pkg_a.caller:do_call", "calls", _STUB_ID), inc_edges,
            "the deleted caller's calls edge must not survive",
        )

        with tempfile.TemporaryDirectory(prefix="py-resstub-partial-full-") as td:
            root = Path(td)
            _git(root)
            _write_yaml(root, "pkg_a/**/*.py")
            _write_caller(root, "pkg_a", "other_caller.py")
            _write_callee(root)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(inc_nodes, _node_ids(g_full))
        self.assertEqual(inc_edges, _edge_set(g_full))


class NegativeSpaceRealSymbolSurvivesTest(unittest.TestCase):
    """The over-purge proof, end to end: a real, exported, never-called
    function must survive an unrelated file's deletion -- it must never be
    mistaken for a stub merely because nothing calls it either."""

    def test_uncalled_exported_function_survives_unrelated_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="py-resstub-negspace-") as td:
            root = Path(td)
            _git(root)
            _write_yaml(root, "pkg_a/**/*.py", "pkg_b/**/*.py")
            _write_caller(root, "pkg_a", "caller.py")
            _write_callee(root)
            other = root / "pkg_a"
            (other / "unrelated.py").write_text(
                "def scratch():\n    pass\n", encoding="utf-8",
            )
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            # Real, file-anchored, definite -- upgraded from the stub shape
            # because pkg_b IS covered by a glob in this fixture.
            self.assertIn(_STUB_ID, _node_ids(g_baseline))
            real = g_baseline["nodes"][_STUB_ID]
            self.assertEqual(real["props"].get("confidence"), "definite")
            self.assertEqual(real["props"].get("file"), "pkg_b/callee.py")

            (other / "unrelated.py").unlink()
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        self.assertIn(
            _STUB_ID, _node_ids(g_inc),
            "a real, file-anchored symbol must survive an unrelated file's "
            "deletion even though nothing (still) calls it",
        )
        self.assertEqual(g_inc["nodes"][_STUB_ID]["props"].get("confidence"), "definite")


class StubToRealUpgradeTest(unittest.TestCase):
    """ADR 0103's stub -> real upgrade path must be undisturbed by the new
    purge rule: adding the target module under an already-configured glob
    replaces the stub with the real node, converging to the same shape a
    fresh full discover of the final tree produces."""

    def test_adding_the_target_module_upgrades_the_stub_incrementally(self) -> None:
        with tempfile.TemporaryDirectory(prefix="py-resstub-upgrade-") as td:
            root = Path(td)
            _git(root)
            _write_yaml(root, "pkg_a/**/*.py", "pkg_b/**/*.py")
            _write_caller(root, "pkg_a", "caller.py")
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            stub = g_baseline["nodes"][_STUB_ID]
            self.assertEqual(stub["props"].get("confidence"), "speculative")
            self.assertNotIn("file", stub["props"])

            _write_callee(root)
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        real = g_inc["nodes"][_STUB_ID]
        self.assertEqual(real["props"].get("confidence"), "definite")
        self.assertEqual(real["props"].get("file"), "pkg_b/callee.py")

        with tempfile.TemporaryDirectory(prefix="py-resstub-upgrade-full-") as td:
            root = Path(td)
            _git(root)
            _write_yaml(root, "pkg_a/**/*.py", "pkg_b/**/*.py")
            _write_caller(root, "pkg_a", "caller.py")
            _write_callee(root)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(_node_ids(g_inc), _node_ids(g_full))
        self.assertEqual(_edge_set(g_inc), _edge_set(g_full))
        self.assertEqual(_strip_meta(g_inc), _strip_meta(g_full))


if __name__ == "__main__":
    unittest.main()
