"""Structural-inheritance edges must downgrade, not vanish (bd rifzk).

Regression for a gap in the ADR 0074 provenance-purge family: the purge
(``weld._incremental_purge.purge_edges_by_provenance``) and its widen-retry
(``weld._discover_orphan_edges.orphaned_producer_files`` +
``weld._discover_incremental_merge.run_incremental_merge``) are both already
edge-type-agnostic -- they key purely on ``props.provenance.file``, never on
``type``. But of the seven ``inherits``/``implements`` edge emitters
(cpp/csharp/go/java/python/rust/typescript), only
``weld.strategies._python_inherits`` ever stamped that field. The other six
left every structural-inheritance edge on the conservative
endpoint-membership purge floor, so deleting the file that defines an
embedded/base/trait type -- while the file that *declares* the
embedding/derived type stays clean -- silently drops the edge outright on
the incremental path instead of downgrading it to the
``symbol:unresolved:<Base>`` sentinel a full discover produces (the fix
lands in each strategy's edge-emission function, mirroring
``_python_inherits.emit_inherits_edges`` exactly).

Go is the reported repro (this bd issue); Rust and TypeScript prove the
fix is the same general mechanism, not a Go special case -- both use
Bazel-locked tree-sitter grammars so neither round needs an optional-extra
skip guard. Mirrors the harness and assertion shape of the sibling
``incremental_callgraph_provenance_purge_test`` /
``incremental_deleted_package_node_equivalence_test`` files: git-backed
temp repos, ``_discover_single_repo`` for both the incremental round and
the from-scratch full-discover comparison, whole-graph
(sans-volatile-meta) byte-identity as the strongest available proof.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo  # noqa: E402


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


def _strip_meta(graph: dict) -> dict:
    """Drop volatile + path-order-volatile meta; nodes/edges must match."""
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


def _inherits_like_edges(graph: dict) -> set[tuple[str, str, str, str]]:
    return {
        (e["from"], e["type"], e["to"], e["props"].get("confidence"))
        for e in graph.get("edges", [])
        if e.get("type") in ("inherits", "implements")
    }


class GoEmbeddedBaseFileDeletedTest(unittest.TestCase):
    """The exact bd rifzk repro: delete the file declaring the embedded
    struct (``shapes.go``'s ``Base``); the embedding file (``geometry.go``)
    is untouched. A full discover downgrades ``Circle -> Base`` to the
    ``symbol:unresolved:Base`` sentinel; incremental must match."""

    def _fixture(self, root: Path, *, with_shapes: bool) -> None:
        shapes = root / "shapes"
        geometry = root / "geometry"
        geometry.mkdir(exist_ok=True)
        (geometry / "geometry.go").write_text(
            "package geometry\n\n"
            'import "example.com/sample/shapes"\n\n'
            "type Circle struct {\n"
            "\tshapes.Base\n"
            "\tRadius float64\n"
            "}\n\n"
            "func (c Circle) Area() float64 {\n"
            "\treturn 3.14 * c.Radius * c.Radius\n"
            "}\n",
            encoding="utf-8",
        )
        if with_shapes:
            shapes.mkdir(exist_ok=True)
            (shapes / "shapes.go").write_text(
                "package shapes\n\n"
                "type Base struct {\n"
                "\tUnit string\n"
                "}\n\n"
                "func (b Base) Describe() string {\n"
                '\treturn "a shape measured in " + b.Unit\n'
                "}\n",
                encoding="utf-8",
            )
        weld_dir = root / ".weld"
        weld_dir.mkdir(exist_ok=True)
        (weld_dir / "discover.yaml").write_text(
            "sources:\n"
            '  - glob: "**/*.go"\n'
            "    type: file\n"
            "    strategy: tree_sitter\n"
            "    language: go\n",
            encoding="utf-8",
        )

    def test_incremental_matches_full_after_deleting_embedded_base_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="go-inh-inc-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, with_shapes=True)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                "symbol:go:shapes.shapes:Base", g_baseline.get("nodes", {}),
                "fixture setup assumption broken: baseline full run must "
                "mint the real Base symbol for the delete round to "
                "actually exercise the downgrade path",
            )
            shutil.rmtree(root / "shapes")
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory(prefix="go-inh-full-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, with_shapes=False)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_inherits = _inherits_like_edges(g_inc)
        full_inherits = _inherits_like_edges(g_full)
        expected = (
            "symbol:go:geometry.geometry:Circle", "inherits",
            "symbol:unresolved:Base", "speculative",
        )
        self.assertIn(
            expected, full_inherits,
            "fixture/harness assumption broken: a full discover of the "
            "post-delete tree must itself downgrade to the unresolved "
            "sentinel for this test to prove anything",
        )
        self.assertIn(
            expected, inc_inherits,
            "incremental discover silently dropped the inherits edge "
            "instead of downgrading it to symbol:unresolved:Base (bd rifzk)",
        )
        self.assertEqual(
            inc_inherits, full_inherits,
            "incremental inherits/implements edge set diverged from a full "
            "discover after deleting the embedded base's file",
        )
        self.assertIn("symbol:unresolved:Base", g_inc.get("nodes", {}))
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph must be byte-identical to a full discover "
            "at the same post-delete source state",
        )


class RustTraitDefinitionFileDeletedTest(unittest.TestCase):
    """Same shape, Rust: delete the file declaring the trait; the ``impl
    Trait for Type`` file stays clean. Proves the fix is not Go-specific."""

    def _fixture(self, root: Path, *, with_trait: bool) -> None:
        (root / "impl_mod.rs").write_text(
            "use crate::trait_mod::Greet;\n\n"
            "pub struct Widget;\n\n"
            "impl Greet for Widget {\n"
            '    fn greet(&self) -> String { "hi".to_string() }\n'
            "}\n",
            encoding="utf-8",
        )
        if with_trait:
            (root / "trait_mod.rs").write_text(
                "pub trait Greet {\n"
                "    fn greet(&self) -> String;\n"
                "}\n",
                encoding="utf-8",
            )
        weld_dir = root / ".weld"
        weld_dir.mkdir(exist_ok=True)
        (weld_dir / "discover.yaml").write_text(
            "sources:\n"
            '  - glob: "**/*.rs"\n'
            "    type: file\n"
            "    strategy: tree_sitter\n"
            "    language: rust\n",
            encoding="utf-8",
        )

    def test_incremental_matches_full_after_deleting_trait_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rs-inh-inc-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, with_trait=True)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                "symbol:rust:trait_mod:Greet", g_baseline.get("nodes", {}),
                "fixture setup assumption broken: baseline full run must "
                "mint the real Greet symbol",
            )
            (root / "trait_mod.rs").unlink()
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory(prefix="rs-inh-full-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, with_trait=False)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_impl = _inherits_like_edges(g_inc)
        full_impl = _inherits_like_edges(g_full)
        expected = (
            "symbol:rust:impl_mod:Widget", "implements",
            "symbol:unresolved:Greet", "speculative",
        )
        self.assertIn(expected, full_impl)
        self.assertIn(
            expected, inc_impl,
            "incremental discover silently dropped the implements edge "
            "instead of downgrading it to symbol:unresolved:Greet",
        )
        self.assertEqual(inc_impl, full_impl)
        self.assertEqual(_strip_meta(g_inc), _strip_meta(g_full))


class TypeScriptBaseClassFileDeletedTest(unittest.TestCase):
    """Same shape, TypeScript: delete the file declaring the base class;
    the ``extends`` file stays clean. Proves the fix generalizes to a
    language with BOTH inherits and implements edges in one emitter."""

    def _fixture(self, root: Path, *, with_base: bool) -> None:
        (root / "derived.ts").write_text(
            'import { Base } from "./base";\n\n'
            "export class Derived extends Base {\n"
            "    extra(): number { return 1; }\n"
            "}\n",
            encoding="utf-8",
        )
        if with_base:
            (root / "base.ts").write_text(
                "export class Base {\n"
                '    describe(): string { return "base"; }\n'
                "}\n",
                encoding="utf-8",
            )
        weld_dir = root / ".weld"
        weld_dir.mkdir(exist_ok=True)
        (weld_dir / "discover.yaml").write_text(
            "sources:\n"
            '  - glob: "**/*.ts"\n'
            "    type: file\n"
            "    strategy: tree_sitter\n"
            "    language: typescript\n",
            encoding="utf-8",
        )

    def test_incremental_matches_full_after_deleting_base_class_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ts-inh-inc-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, with_base=True)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                "symbol:typescript:base:Base", g_baseline.get("nodes", {}),
                "fixture setup assumption broken: baseline full run must "
                "mint the real Base symbol",
            )
            (root / "base.ts").unlink()
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory(prefix="ts-inh-full-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, with_base=False)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_inherits = _inherits_like_edges(g_inc)
        full_inherits = _inherits_like_edges(g_full)
        expected = (
            "symbol:typescript:derived:Derived", "inherits",
            "symbol:unresolved:Base", "speculative",
        )
        self.assertIn(expected, full_inherits)
        self.assertIn(
            expected, inc_inherits,
            "incremental discover silently dropped the inherits edge "
            "instead of downgrading it to symbol:unresolved:Base",
        )
        self.assertEqual(inc_inherits, full_inherits)
        self.assertEqual(_strip_meta(g_inc), _strip_meta(g_full))


if __name__ == "__main__":
    unittest.main()
