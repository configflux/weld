"""Incremental == full when a `symbol:unresolved:*` sentinel's sole
referencing file is deleted (bd oao53).

Sibling of ``incremental_external_package_purge_equivalence_test`` (bd
pkz2s), which fixed the same orphan-survival shape for
``graph_closure``-minted external package placeholders. This is the third
placeholder shape pkz2s's own mini spec explicitly scoped OUT of that fix:
``python_callgraph``, ``_go_inherits``, ``_rust_inherits``,
``_typescript_inherits`` (and siblings) mint a ``symbol:unresolved:<name>``
node lazily for every call/inherits/implements reference that does not
resolve, anchored purely by inbound edges from whichever files reference it.
When the last referencer is deleted, the edge is correctly purged (the
referencing file is always the edge's own ``from`` endpoint, so the ordinary
endpoint-membership purge already catches it) but the sentinel node itself
used to linger with zero inbound edges, because ``purge_stale_nodes``
matched nodes to purge by ``props.file`` alone and this node carries none.

Empirically reproduced (this bd issue's own investigation) for python
(``calls``), go (``inherits``, struct embedding), rust (``implements``,
trait impl), and typescript (``inherits``, ``extends``) via real
``discover()`` incremental-vs-full runs before any fix existed. Fixed by
extending :func:`weld.discovery_state.purge_stale_nodes` to also purge an
unresolved-symbol sentinel once every inbound edge OF ANY TYPE it had is
gone post-purge (:mod:`weld._discover_unresolved_symbol_purge`) -- unlike
pkz2s's ``depends_on``-only signal, this one must count every edge type
because the sentinel id is a namespace shared across every strategy that
fails to resolve the same bare name. The tests below prove END TO END --
through the real ``discover()`` incremental path -- that this one purge
extension is enough for full node+edge equivalence across four languages,
with no over-purge when a second referencer survives.
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


# ---------------------------------------------------------------------------
# Fixture writers: one sole-referencer file (deleted mid-test), one unrelated
# "other" file (proves this is not a blanket sweep), each wired to its own
# minimal discover.yaml.
# ---------------------------------------------------------------------------

def _write_python(root: Path) -> None:
    a = root / "a"
    a.mkdir(exist_ok=True)
    (a / "caller.py").write_text(
        "def use_it():\n    return totally_unresolvable_free_function(1)\n",
        encoding="utf-8",
    )
    other = root / "other"
    other.mkdir(exist_ok=True)
    (other / "mod.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    _write_yaml(root, '"**/*.py"', strategy="python_callgraph")


def _write_go(root: Path, *, with_second_embedder: bool = False) -> None:
    a = root / "a"
    a.mkdir(exist_ok=True)
    (a / "a.go").write_text(
        "package a\n\ntype Widget struct {\n\tTotallyUnresolvableBase\n}\n",
        encoding="utf-8",
    )
    if with_second_embedder:
        b = root / "b"
        b.mkdir(exist_ok=True)
        (b / "b.go").write_text(
            "package b\n\ntype Gadget struct {\n\tTotallyUnresolvableBase\n}\n",
            encoding="utf-8",
        )
    other = root / "other"
    other.mkdir(exist_ok=True)
    (other / "other.go").write_text(
        "package other\n\nfunc Sq(x float64) float64 {\n\treturn x * x\n}\n",
        encoding="utf-8",
    )
    (root / "go.mod").write_text("module example.com/sample\n\ngo 1.22\n", encoding="utf-8")
    _write_yaml(root, '"**/*.go"', strategy="tree_sitter", language="go")


def _write_rust(root: Path) -> None:
    a = root / "a"
    a.mkdir(exist_ok=True)
    (a / "a.rs").write_text(
        "pub struct Widget;\n\nimpl TotallyUnresolvableTrait for Widget {}\n",
        encoding="utf-8",
    )
    other = root / "other"
    other.mkdir(exist_ok=True)
    (other / "other.rs").write_text("pub fn sq(x: f64) -> f64 { x * x }\n", encoding="utf-8")
    (root / "Cargo.toml").write_text(
        '[package]\nname = "sample"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    _write_yaml(root, '"**/*.rs"', strategy="tree_sitter", language="rust")


def _write_typescript(root: Path) -> None:
    a = root / "a"
    a.mkdir(exist_ok=True)
    (a / "a.ts").write_text(
        "export class Widget extends TotallyUnresolvableBase {}\n", encoding="utf-8",
    )
    other = root / "other"
    other.mkdir(exist_ok=True)
    (other / "other.ts").write_text(
        "export function sq(x: number): number { return x * x; }\n", encoding="utf-8",
    )
    _write_yaml(root, '"**/*.ts"', strategy="tree_sitter", language="typescript")


def _write_yaml(root: Path, glob: str, *, strategy: str, language: str = "") -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    lang_line = f"\n    language: {language}" if language else ""
    (weld_dir / "discover.yaml").write_text(
        f'sources:\n  - glob: {glob}\n    type: file\n    strategy: {strategy}{lang_line}\n',
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Shared assertion helper
# ---------------------------------------------------------------------------

def _assert_sole_referencer_purge_matches_full(
    case: unittest.TestCase,
    *,
    prefix: str,
    write_fixture,
    sole_rel_path: str,
    sentinel_id: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix=f"{prefix}-unres-inc-") as td:
        root = Path(td)
        _git(root)
        write_fixture(root)
        _commit(root)
        g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
        case.assertIn(
            sentinel_id, _node_ids(g_baseline),
            "fixture setup assumption broken: the baseline full run must "
            f"mint {sentinel_id} for the delete round to exercise anything",
        )
        (root / sole_rel_path).unlink()
        _commit(root)
        g_inc = _discover_single_repo(root, incremental=True, write_graph=True)
        # Determinism: a second incremental pass over the same (now
        # unchanged) tree must report the identical set, not a first-run
        # coincidence.
        g_inc_again = _discover_single_repo(root, incremental=True, write_graph=True)

    with tempfile.TemporaryDirectory(prefix=f"{prefix}-unres-full-") as td:
        root = Path(td)
        _git(root)
        write_fixture(root)
        (root / sole_rel_path).unlink()
        _commit(root)
        g_full = _discover_single_repo(root, incremental=False, write_graph=True)

    inc_nodes, full_nodes = _node_ids(g_inc), _node_ids(g_full)
    case.assertEqual(
        inc_nodes, full_nodes,
        f"incremental node set diverged from a full discover after deleting "
        f"the sole referencer (full-only={sorted(full_nodes - inc_nodes)}, "
        f"inc-only={sorted(inc_nodes - full_nodes)})",
    )
    case.assertEqual(_edge_set(g_inc), _edge_set(g_full))
    case.assertEqual(_strip_meta(g_inc), _strip_meta(g_full))
    case.assertNotIn(
        sentinel_id, inc_nodes,
        f"{sentinel_id} survived incremental discovery as a zero-inbound-"
        "edge orphan after its sole referencer was deleted (bd oao53)",
    )
    case.assertEqual(
        inc_nodes, _node_ids(g_inc_again),
        "sentinel purge must be deterministic across repeated incremental "
        "passes, not merely correct on the first",
    )


class SoleReferencerDeletedEquivalenceTest(unittest.TestCase):
    def test_python_calls_edge(self) -> None:
        _assert_sole_referencer_purge_matches_full(
            self, prefix="py", write_fixture=_write_python,
            sole_rel_path="a/caller.py",
            sentinel_id="symbol:unresolved:totally_unresolvable_free_function",
        )

    def test_go_inherits_edge(self) -> None:
        _assert_sole_referencer_purge_matches_full(
            self, prefix="go", write_fixture=_write_go,
            sole_rel_path="a/a.go",
            sentinel_id="symbol:unresolved:TotallyUnresolvableBase",
        )

    def test_rust_implements_edge(self) -> None:
        _assert_sole_referencer_purge_matches_full(
            self, prefix="rs", write_fixture=_write_rust,
            sole_rel_path="a/a.rs",
            sentinel_id="symbol:unresolved:TotallyUnresolvableTrait",
        )

    def test_typescript_inherits_edge(self) -> None:
        _assert_sole_referencer_purge_matches_full(
            self, prefix="ts", write_fixture=_write_typescript,
            sole_rel_path="a/a.ts",
            sentinel_id="symbol:unresolved:TotallyUnresolvableBase",
        )


class NonSoleReferencerDeletedTest(unittest.TestCase):
    """No over-purge: two Go files embed the same unresolvable base; deleting
    ONE must leave the sentinel alive, carrying only the surviving
    embedder's edge -- matching what a full run over the same
    partially-emptied tree would still emit."""

    def test_incremental_keeps_the_sentinel_after_deleting_one_of_two_embedders(
        self,
    ) -> None:
        sentinel_id = "symbol:unresolved:TotallyUnresolvableBase"
        with tempfile.TemporaryDirectory(prefix="go-unres-partial-inc-") as td:
            root = Path(td)
            _git(root)
            _write_go(root, with_second_embedder=True)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(sentinel_id, _node_ids(g_baseline))
            (root / "a" / "a.go").unlink()
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        inc_nodes, inc_edges = _node_ids(g_inc), _edge_set(g_inc)
        self.assertIn(
            sentinel_id, inc_nodes,
            "a sentinel with a surviving referencer must keep its node",
        )
        self.assertIn(
            ("symbol:go:b.b:Gadget", "inherits", sentinel_id), inc_edges,
        )
        self.assertNotIn(
            ("symbol:go:a.a:Widget", "inherits", sentinel_id), inc_edges,
            "the deleted embedder's inherits edge must not survive",
        )

        with tempfile.TemporaryDirectory(prefix="go-unres-partial-full-") as td:
            root = Path(td)
            _git(root)
            _write_go(root, with_second_embedder=True)
            (root / "a" / "a.go").unlink()
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(inc_nodes, _node_ids(g_full))
        self.assertEqual(inc_edges, _edge_set(g_full))


if __name__ == "__main__":
    unittest.main()
