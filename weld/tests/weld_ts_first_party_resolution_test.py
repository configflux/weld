"""First-party TS imports, from the index through the closure (bd lrnx1.4).

The two maps are pinned by their own suites; this one pins what happens when
they meet the graph. Three layers, because a break in any of them shows up in
the same place -- an external ``package`` node for code that is right there in
the repository (ADR 0142 D3, the TypeScript reading of ADR 0141 D4):

* :class:`weld.strategies._ts_first_party.FirstPartyImports` -- which map is
  consulted first, and what an importer's location changes;
* ``_typescript_tree_sitter.enrich_file_node`` -- a bound specifier records a
  target and mints no package node, an unbound one is left exactly as before;
* ``weld.graph_closure._link_imports`` -- the recorded target becomes a
  ``depends_on`` edge onto the defining file's node, and a target the graph
  does not hold draws no edge at all rather than an external claim.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.graph_closure import close_graph
from weld.strategies._ts_first_party import build_first_party_imports
from weld.strategies._typescript_tree_sitter import build_caches, enrich_file_node


def write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def monorepo(root: Path) -> None:
    """One npm workspace, one aliased app, one shared package."""
    write(root, "package.json", json.dumps({
        "name": "acme", "private": True, "workspaces": ["apps/*", "packages/*"],
    }))
    write(root, "apps/web/package.json", json.dumps({"name": "@acme/web"}))
    write(root, "apps/web/tsconfig.json", json.dumps({
        "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}},
    }))
    write(root, "apps/web/src/lib/greeting.ts", "export const greeting = 1;\n")
    write(root, "apps/web/src/app/page.ts", "export const page = 1;\n")
    write(root, "packages/shared/package.json", json.dumps({
        "name": "@acme/shared", "main": "index.ts",
    }))
    write(root, "packages/shared/index.ts", "export * from './money';\n")
    write(root, "packages/shared/money.ts", "export const money = 1;\n")


class IndexResolution(unittest.TestCase):
    def test_both_first_party_spellings_bind_to_their_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            index = build_first_party_imports(root)
            importer = "apps/web/src/app/page.ts"
            self.assertEqual(
                index.resolve("@acme/shared", importer), "packages/shared/index.ts"
            )
            self.assertEqual(
                index.resolve("@/lib/greeting", importer),
                "apps/web/src/lib/greeting.ts",
            )

    def test_a_published_package_is_not_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            index = build_first_party_imports(root)
            for specifier in ("react", "next/navigation", "node:fs", "@scope/pkg"):
                with self.subTest(specifier=specifier):
                    self.assertEqual(
                        index.resolve(specifier, "apps/web/src/app/page.ts"), ""
                    )

    def test_relative_imports_are_left_to_the_closure(self) -> None:
        """The path index already answers those, without touching the disk."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            index = build_first_party_imports(root)
            self.assertEqual(
                index.resolve("./money", "packages/shared/index.ts"), ""
            )

    def test_an_alias_only_answers_inside_its_own_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            index = build_first_party_imports(root)
            self.assertEqual(
                index.resolve("@/lib/greeting", "packages/shared/index.ts"), ""
            )

    def test_a_subpath_of_a_workspace_member_binds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            index = build_first_party_imports(root)
            self.assertEqual(
                index.resolve("@acme/shared/money", "apps/web/src/app/page.ts"),
                "packages/shared/money.ts",
            )

    def test_an_alias_outranks_a_member_of_the_same_name(self) -> None:
        """TypeScript applies ``paths`` before it looks in ``node_modules``."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            write(root, "apps/web/tsconfig.json", json.dumps({"compilerOptions": {
                "baseUrl": ".", "paths": {"@acme/shared": ["src/local-shim.ts"]},
            }}))
            write(root, "apps/web/src/local-shim.ts", "export const shim = 1;\n")
            index = build_first_party_imports(root)
            self.assertEqual(
                index.resolve("@acme/shared", "apps/web/src/app/page.ts"),
                "apps/web/src/local-shim.ts",
            )

    def test_a_workspace_member_answers_from_anywhere_in_the_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            index = build_first_party_imports(root)
            self.assertEqual(
                index.resolve("@acme/shared", "packages/shared/other.ts"),
                "packages/shared/index.ts",
            )


class StrategyStamping(unittest.TestCase):
    def _enrich(self, root: Path, rel: str, imports: list[str]) -> tuple[dict, list]:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        props: dict = {"file": rel}
        caches = build_caches(root, "typescript")
        assert caches is not None
        enrich_file_node(
            nodes, edges, f"file:{rel}", props,
            {"imports": imports}, "", "tree_sitter", root=root, **caches,
        )
        return {"nodes": nodes, "props": props}, edges

    def test_a_bound_specifier_records_a_target_and_no_package_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            result, edges = self._enrich(
                root, "apps/web/src/app/page.ts",
                ['"@acme/shared"', '"@/lib/greeting"'],
            )
            self.assertEqual(result["nodes"], {})
            self.assertEqual(edges, [])
            self.assertEqual(result["props"]["import_targets"], {
                "@/lib/greeting": "apps/web/src/lib/greeting.ts",
                "@acme/shared": "packages/shared/index.ts",
            })

    def test_an_external_import_keeps_its_package_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            write(root, "package.json", json.dumps({
                "name": "acme", "workspaces": ["apps/*", "packages/*"],
                "dependencies": {"react": "18.3.1"},
            }))
            result, edges = self._enrich(
                root, "apps/web/src/app/page.ts", ['"react"', '"@acme/shared"'],
            )
            self.assertEqual(sorted(result["nodes"]), ["package:typescript:react"])
            self.assertEqual([edge["to"] for edge in edges],
                             ["package:typescript:react"])
            self.assertNotIn("@acme/shared", [
                edge["props"]["import_name"] for edge in edges
            ])

    def test_a_file_with_no_first_party_imports_gains_no_prop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            result, _ = self._enrich(root, "apps/web/src/app/page.ts", ['"react"'])
            self.assertNotIn("import_targets", result["props"])

    def test_a_self_import_is_not_recorded(self) -> None:
        """A member whose entry point is the importing file itself."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monorepo(root)
            result, _ = self._enrich(
                root, "packages/shared/index.ts", ['"@acme/shared"'],
            )
            self.assertNotIn("import_targets", result["props"])


class ClosureBinding(unittest.TestCase):
    """What ``close_graph`` does with a recorded target."""

    @staticmethod
    def _graph(targets: dict[str, str], *, hold_target: bool) -> tuple[dict, list]:
        nodes: dict[str, dict] = {
            "file:apps/web/src/app/page": {
                "type": "file",
                "props": {
                    "file": "apps/web/src/app/page.ts",
                    "imports_from": ['"@acme/shared"'],
                    "import_targets": targets,
                },
            },
        }
        if hold_target:
            nodes["file:packages/shared/index"] = {
                "type": "file",
                "props": {"file": "packages/shared/index.ts"},
            }
        edges: list[dict] = []
        close_graph(nodes, edges)
        return nodes, edges

    def test_a_recorded_target_becomes_an_edge_onto_the_defining_file(self) -> None:
        nodes, edges = self._graph(
            {"@acme/shared": "packages/shared/index.ts"}, hold_target=True,
        )
        depends = [edge for edge in edges if edge.get("type") == "depends_on"]
        self.assertEqual(
            [(edge["from"], edge["to"], edge["props"]["resolution"])
             for edge in depends],
            [("file:apps/web/src/app/page", "file:packages/shared/index",
              "first_party")],
        )
        self.assertEqual(
            [node_id for node_id in nodes if node_id.startswith("package:")], [],
        )

    def test_a_target_the_graph_does_not_hold_draws_no_edge(self) -> None:
        """Never an ``external`` claim about a name proven first-party."""
        nodes, edges = self._graph(
            {"@acme/shared": "packages/shared/index.ts"}, hold_target=False,
        )
        self.assertEqual([edge for edge in edges if edge.get("type") == "depends_on"], [])
        self.assertEqual(
            [node_id for node_id in nodes if node_id.startswith("package:")], [],
        )

    def test_without_a_recorded_target_the_package_node_is_unchanged(self) -> None:
        nodes, edges = self._graph({}, hold_target=True)
        self.assertIn("package:typescript:acme-shared", nodes)
        self.assertEqual(
            [edge["props"]["resolution"] for edge in edges
             if edge.get("type") == "depends_on"],
            ["external"],
        )

    def test_a_malformed_prop_from_an_older_graph_is_ignored(self) -> None:
        for payload in ("nonsense", {"@acme/shared": 7}, {5: "x"}, []):
            with self.subTest(payload=payload):
                nodes, _ = self._graph(payload, hold_target=True)  # type: ignore[arg-type]
                self.assertIn("package:typescript:acme-shared", nodes)


if __name__ == "__main__":
    unittest.main()
