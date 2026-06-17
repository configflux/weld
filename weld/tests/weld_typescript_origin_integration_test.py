"""Integration tests for ADR 0042 TypeScript / JavaScript origin tagging.

Exercises :func:`weld.strategies.tree_sitter.extract` against a small
on-disk fixture project: a couple of ``.ts`` / ``.js`` files plus a
``package.json`` declaring at least one dependency, and a
``node_modules/`` tree carrying a separate package directory. The test
mocks the tree-sitter call so a live grammar install is not required;
the symbols dict it returns is the same shape ``_parse_file_symbols``
would emit for the fixture sources.

Coverage focus:

- A relative import (``./util``) must NOT mint a ``package`` node
  (relative imports stay project-internal).
- A ``package.json`` dep import (``react``) emits a ``package`` node
  with ``origin="external"``.
- A ``node_modules/``-only import (``lodash``, not in the manifest
  but installed on disk) also emits ``origin="external"``.
- A ``node:`` protocol import (``node:fs``) emits ``origin="stdlib"``.
- An unknown bare specifier emits ``origin="unresolved"`` so the
  classifier never silently coerces the value.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock



def _build_fixture(root: Path) -> None:
    """Create the fixture TS / JS project under *root*."""
    src = root / "src"
    src.mkdir()
    (src / "util.ts").write_text(
        "export function helper(): number { return 1; }\n",
        encoding="utf-8",
    )
    (src / "app.ts").write_text(
        # Source content is not parsed by the test (the call to
        # ``_parse_file_symbols`` is mocked) but we still write a
        # realistic body so future upgrades to a live grammar can
        # exercise the same fixture.
        "import {helper} from './util';\n"
        "import React from 'react';\n"
        "import lodash from 'lodash';\n"
        "import fs from 'node:fs';\n"
        "import {weird} from 'mystery-package';\n"
        "export const main = () => helper();\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "version": "0.0.0",
                "dependencies": {"react": "^18.0.0"},
            }
        ),
        encoding="utf-8",
    )
    # ``lodash`` is installed on disk but NOT in package.json -- this
    # simulates a transitive install that node_modules resolution
    # picks up even though the manifest does not list it.
    (root / "node_modules" / "lodash").mkdir(parents=True)


def _symbols_for(file_path: Path) -> dict[str, list[str]]:
    """Return the symbol dict the mocked parser feeds back per file.

    Keyed by file basename so we can drive different captures into
    different files in one ``extract`` call.
    """
    if file_path.name == "app.ts":
        return {
            "exports": ["main"],
            "classes": [],
            "imports": [
                "'./util'",
                "'react'",
                "'lodash'",
                "'node:fs'",
                "'mystery-package'",
            ],
        }
    return {"exports": ["helper"], "classes": [], "imports": []}


class TypescriptImportOriginIntegrationTest(unittest.TestCase):
    """End-to-end: ``tree_sitter.extract`` on a TS fixture project."""

    def _run_extract(self, root: Path) -> dict[str, dict]:
        from weld.strategies import tree_sitter as ts_strategy

        def fake_parse(file_path, language, queries, **_kw):  # noqa: ARG001
            return _symbols_for(file_path)

        with mock.patch.object(
            ts_strategy, "TREE_SITTER_AVAILABLE", True,
        ), mock.patch.object(
            ts_strategy, "_parse_file_symbols", side_effect=fake_parse,
        ):
            result = ts_strategy.extract(
                root=root,
                source={
                    "glob": "src/**/*.ts",
                    "language": "typescript",
                    "source_strategy": "tree_sitter",
                },
                context={},
            )
        return result.nodes

    def _packages_by_name(
        self, nodes: dict[str, dict]
    ) -> dict[str, dict]:
        return {
            n["props"].get("name"): n
            for n in nodes.values()
            if n.get("type") == "package"
        }

    def test_external_dep_from_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _build_fixture(root)
            nodes = self._run_extract(root)
            packages = self._packages_by_name(nodes)
            self.assertIn("react", packages)
            self.assertEqual(packages["react"]["props"]["origin"], "external")

    def test_external_dep_from_node_modules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _build_fixture(root)
            nodes = self._run_extract(root)
            packages = self._packages_by_name(nodes)
            # ``lodash`` is only present under ``node_modules/``; it
            # must still classify as ``external`` per ADR 0042 §TS / JS.
            self.assertIn("lodash", packages)
            self.assertEqual(
                packages["lodash"]["props"]["origin"], "external",
            )

    def test_node_protocol_is_stdlib(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _build_fixture(root)
            nodes = self._run_extract(root)
            packages = self._packages_by_name(nodes)
            # The ``node:fs`` import strips to a ``node:fs`` package
            # name (the helper preserves the prefix on the bare-root
            # specifier so the package node's identity stays stable
            # across runs and is not confused with the npm ``fs``).
            self.assertIn("node:fs", packages)
            self.assertEqual(
                packages["node:fs"]["props"]["origin"], "stdlib",
            )

    def test_unknown_bare_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _build_fixture(root)
            nodes = self._run_extract(root)
            packages = self._packages_by_name(nodes)
            self.assertIn("mystery-package", packages)
            self.assertEqual(
                packages["mystery-package"]["props"]["origin"],
                "unresolved",
            )

    def test_relative_import_does_not_mint_package_node(self) -> None:
        # ADR 0042 §TS / JS classifies relative imports as project; the
        # strategy already mints file nodes for those via the project
        # glob, so the enricher must not also create a ``package`` node
        # for ``./util``.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _build_fixture(root)
            nodes = self._run_extract(root)
            packages = self._packages_by_name(nodes)
            self.assertNotIn("./util", packages)
            self.assertNotIn("util", packages)

    def test_file_node_origin_is_project(self) -> None:
        # Sanity: the file-type node itself must still carry
        # origin="project" (already covered by the existing
        # language-origin integration test, but re-asserted here so a
        # regression in this code path is caught locally).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _build_fixture(root)
            nodes = self._run_extract(root)
            file_nodes = [
                n for n in nodes.values() if n.get("type") == "file"
            ]
            self.assertTrue(file_nodes)
            for node in file_nodes:
                self.assertEqual(node["props"].get("origin"), "project")

    def test_every_package_node_has_origin(self) -> None:
        # ADR 0042's totality contract: every emitted package node
        # carries one of the four origin values, never ``None``.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _build_fixture(root)
            nodes = self._run_extract(root)
            packages = self._packages_by_name(nodes)
            self.assertTrue(packages)
            for name, node in packages.items():
                origin = node["props"].get("origin")
                self.assertIn(
                    origin,
                    ("project", "stdlib", "external", "unresolved"),
                    f"package {name!r} has invalid origin {origin!r}",
                )


class TypescriptOriginCachesTest(unittest.TestCase):
    """``build_caches`` returns the right shape for TS-family languages."""

    def test_typescript_returns_dict(self) -> None:
        from weld.strategies import _typescript_tree_sitter

        with tempfile.TemporaryDirectory() as td:
            caches = _typescript_tree_sitter.build_caches(
                Path(td), "typescript",
            )
            self.assertIsNotNone(caches)
            assert caches is not None  # for the type-checker
            self.assertEqual(
                set(caches.keys()),
                {"package_deps", "node_modules_packages"},
            )

    def test_tsx_jsx_javascript_supported(self) -> None:
        from weld.strategies import _typescript_tree_sitter

        with tempfile.TemporaryDirectory() as td:
            for lang in ("tsx", "javascript", "jsx"):
                self.assertIsNotNone(
                    _typescript_tree_sitter.build_caches(Path(td), lang)
                )

    def test_non_ts_language_returns_none(self) -> None:
        from weld.strategies import _typescript_tree_sitter

        with tempfile.TemporaryDirectory() as td:
            for lang in ("python", "rust", "go", "cpp", ""):
                self.assertIsNone(
                    _typescript_tree_sitter.build_caches(Path(td), lang)
                )


if __name__ == "__main__":
    unittest.main()
