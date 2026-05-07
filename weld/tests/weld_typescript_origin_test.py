"""Unit tests for ADR 0042 TypeScript / JavaScript origin helpers.

Covers the pure helpers in :mod:`weld.strategies._typescript_origin`:
the relative / stdlib specifier predicates, the package-root extractor,
the ``package.json`` and ``node_modules/`` cache loaders, and the
four-way ``classify_import_specifier`` dispatcher.

The classifier is pure; the cache loaders perform a single read each
and are tested against a real ``tempfile`` fixture. No tree-sitter
install is required.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies._typescript_origin import (  # noqa: E402
    JS_STDLIB_GLOBALS,
    classify_import_specifier,
    is_js_stdlib_specifier,
    is_relative_import,
    load_node_modules_packages,
    load_package_deps,
    package_root_from_specifier,
)


class IsRelativeImportTest(unittest.TestCase):
    """``is_relative_import`` matches ``./`` and ``../`` only."""

    def test_dot_slash(self) -> None:
        self.assertTrue(is_relative_import("./foo"))
        self.assertTrue(is_relative_import("./foo/bar"))

    def test_dotdot_slash(self) -> None:
        self.assertTrue(is_relative_import("../bar"))
        self.assertTrue(is_relative_import("../../baz"))

    def test_bare_specifier_is_not_relative(self) -> None:
        for s in ("react", "lodash/fp", "@scope/pkg", "fs"):
            self.assertFalse(is_relative_import(s))

    def test_absolute_path_is_not_relative(self) -> None:
        # Absolute paths are not ECMAScript relative imports.
        self.assertFalse(is_relative_import("/abs/path"))

    def test_url_is_not_relative(self) -> None:
        self.assertFalse(is_relative_import("https://example.com/mod.js"))

    def test_empty(self) -> None:
        self.assertFalse(is_relative_import(""))


class IsJsStdlibSpecifierTest(unittest.TestCase):
    """``is_js_stdlib_specifier`` recognises ``node:`` prefix and globals."""

    def test_node_prefix(self) -> None:
        for s in ("node:fs", "node:path", "node:stream/web"):
            self.assertTrue(is_js_stdlib_specifier(s))

    def test_implicit_global(self) -> None:
        # The implicit-lookup globals double as stdlib import keys (the
        # call-graph sentinel classifier shares this list).
        self.assertTrue(is_js_stdlib_specifier("Array"))
        self.assertTrue(is_js_stdlib_specifier("Math"))

    def test_bare_fs_is_not_stdlib(self) -> None:
        # ``"fs"`` (no protocol prefix) is ambiguous: npm publishes a
        # package by that name and the helper must not silently claim
        # stdlib without manifest evidence.
        self.assertFalse(is_js_stdlib_specifier("fs"))
        self.assertFalse(is_js_stdlib_specifier("path"))

    def test_relative_is_not_stdlib(self) -> None:
        self.assertFalse(is_js_stdlib_specifier("./foo"))

    def test_empty(self) -> None:
        self.assertFalse(is_js_stdlib_specifier(""))


class PackageRootFromSpecifierTest(unittest.TestCase):
    """``package_root_from_specifier`` strips sub-paths only."""

    def test_unscoped_root(self) -> None:
        self.assertEqual(package_root_from_specifier("react"), "react")

    def test_unscoped_subpath(self) -> None:
        self.assertEqual(package_root_from_specifier("lodash/fp"), "lodash")
        self.assertEqual(
            package_root_from_specifier("react-dom/server"), "react-dom",
        )

    def test_scoped_root(self) -> None:
        self.assertEqual(
            package_root_from_specifier("@scope/pkg"), "@scope/pkg",
        )

    def test_scoped_subpath(self) -> None:
        self.assertEqual(
            package_root_from_specifier("@scope/pkg/sub/path"),
            "@scope/pkg",
        )

    def test_lone_at_passes_through(self) -> None:
        # Malformed scope (no second segment) -> pass through unchanged.
        self.assertEqual(package_root_from_specifier("@scope"), "@scope")

    def test_empty(self) -> None:
        self.assertEqual(package_root_from_specifier(""), "")


class LoadPackageDepsTest(unittest.TestCase):
    """``load_package_deps`` reads the four conventional dep buckets."""

    def _write_manifest(self, root: Path, payload: dict) -> None:
        (root / "package.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )

    def test_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_manifest(
                root, {"dependencies": {"react": "^18.0.0"}},
            )
            deps = load_package_deps(root)
            self.assertEqual(deps, frozenset({"react"}))

    def test_all_buckets_unioned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_manifest(
                root,
                {
                    "dependencies": {"react": "^18.0.0"},
                    "devDependencies": {"jest": "^29.0.0"},
                    "peerDependencies": {"react-dom": "^18.0.0"},
                    "optionalDependencies": {"fsevents": "*"},
                },
            )
            deps = load_package_deps(root)
            self.assertEqual(
                deps,
                frozenset({"react", "jest", "react-dom", "fsevents"}),
            )

    def test_missing_manifest_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_package_deps(Path(td)), frozenset())

    def test_malformed_json_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(
                "{not valid json", encoding="utf-8",
            )
            self.assertEqual(load_package_deps(root), frozenset())

    def test_non_object_root_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text("[]", encoding="utf-8")
            self.assertEqual(load_package_deps(root), frozenset())

    def test_no_dep_keys_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_manifest(root, {"name": "x", "version": "1.0.0"})
            self.assertEqual(load_package_deps(root), frozenset())

    def test_non_dict_bucket_skipped(self) -> None:
        # Hand-edited manifests sometimes have the wrong shape; the
        # helper must shrug rather than raise.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_manifest(
                root, {"dependencies": ["react"], "devDependencies": None},
            )
            self.assertEqual(load_package_deps(root), frozenset())


class LoadNodeModulesPackagesTest(unittest.TestCase):
    """``load_node_modules_packages`` enumerates top-level package dirs."""

    def test_unscoped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "node_modules" / "react").mkdir(parents=True)
            (root / "node_modules" / "lodash").mkdir(parents=True)
            self.assertEqual(
                load_node_modules_packages(root),
                frozenset({"react", "lodash"}),
            )

    def test_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "node_modules" / "@scope" / "pkg").mkdir(parents=True)
            (root / "node_modules" / "@other" / "thing").mkdir(parents=True)
            self.assertEqual(
                load_node_modules_packages(root),
                frozenset({"@scope/pkg", "@other/thing"}),
            )

    def test_mixed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "node_modules" / "react").mkdir(parents=True)
            (root / "node_modules" / "@scope" / "pkg").mkdir(parents=True)
            self.assertEqual(
                load_node_modules_packages(root),
                frozenset({"react", "@scope/pkg"}),
            )

    def test_skips_hidden_dirs(self) -> None:
        # ``node_modules/.bin`` is tooling, not a package root.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "node_modules" / ".bin").mkdir(parents=True)
            (root / "node_modules" / "react").mkdir(parents=True)
            self.assertEqual(
                load_node_modules_packages(root),
                frozenset({"react"}),
            )

    def test_skips_files(self) -> None:
        # A stray file under node_modules/ must not appear as a pkg.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "node_modules").mkdir()
            (root / "node_modules" / "stray.txt").write_text("x")
            (root / "node_modules" / "react").mkdir()
            self.assertEqual(
                load_node_modules_packages(root),
                frozenset({"react"}),
            )

    def test_missing_node_modules_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_node_modules_packages(Path(td)), frozenset())

    def test_node_modules_is_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "node_modules").write_text("not a dir")
            self.assertEqual(load_node_modules_packages(root), frozenset())


class ClassifyImportSpecifierTest(unittest.TestCase):
    """``classify_import_specifier`` is total over the four origin values."""

    EMPTY: frozenset[str] = frozenset()

    def test_relative_is_project(self) -> None:
        for s in ("./foo", "../bar", "./a/b/c"):
            self.assertEqual(
                classify_import_specifier(
                    s,
                    package_deps=self.EMPTY,
                    node_modules_packages=self.EMPTY,
                ),
                "project",
            )

    def test_node_protocol_is_stdlib(self) -> None:
        self.assertEqual(
            classify_import_specifier(
                "node:fs",
                package_deps=self.EMPTY,
                node_modules_packages=self.EMPTY,
            ),
            "stdlib",
        )

    def test_implicit_global_is_stdlib(self) -> None:
        self.assertEqual(
            classify_import_specifier(
                "Array",
                package_deps=self.EMPTY,
                node_modules_packages=self.EMPTY,
            ),
            "stdlib",
        )

    def test_package_json_match_is_external(self) -> None:
        self.assertEqual(
            classify_import_specifier(
                "react",
                package_deps=frozenset({"react"}),
                node_modules_packages=self.EMPTY,
            ),
            "external",
        )

    def test_node_modules_match_is_external(self) -> None:
        self.assertEqual(
            classify_import_specifier(
                "react",
                package_deps=self.EMPTY,
                node_modules_packages=frozenset({"react"}),
            ),
            "external",
        )

    def test_subpath_classifies_on_root(self) -> None:
        self.assertEqual(
            classify_import_specifier(
                "lodash/fp",
                package_deps=frozenset({"lodash"}),
                node_modules_packages=self.EMPTY,
            ),
            "external",
        )

    def test_scoped_package(self) -> None:
        self.assertEqual(
            classify_import_specifier(
                "@scope/pkg/sub",
                package_deps=self.EMPTY,
                node_modules_packages=frozenset({"@scope/pkg"}),
            ),
            "external",
        )

    def test_unknown_bare_is_unresolved(self) -> None:
        self.assertEqual(
            classify_import_specifier(
                "mystery-package",
                package_deps=self.EMPTY,
                node_modules_packages=self.EMPTY,
            ),
            "unresolved",
        )

    def test_empty_is_unresolved(self) -> None:
        self.assertEqual(
            classify_import_specifier(
                "",
                package_deps=self.EMPTY,
                node_modules_packages=self.EMPTY,
            ),
            "unresolved",
        )

    def test_node_prefix_wins_over_external_clash(self) -> None:
        # ``fs`` is a real npm package, but ``node:fs`` is the explicit
        # stdlib protocol form. Stdlib must classify before the
        # external manifest check so the protocol form is honoured.
        self.assertEqual(
            classify_import_specifier(
                "node:fs",
                package_deps=frozenset({"fs"}),
                node_modules_packages=self.EMPTY,
            ),
            "stdlib",
        )

    def test_relative_wins_over_external(self) -> None:
        # A specifier like ``./react`` must be project-origin even if
        # the project happens to depend on a package called ``react``.
        self.assertEqual(
            classify_import_specifier(
                "./react",
                package_deps=frozenset({"react"}),
                node_modules_packages=self.EMPTY,
            ),
            "project",
        )


class ModuleSurfaceTest(unittest.TestCase):
    """``JS_STDLIB_GLOBALS`` re-exports the language-origin frozenset."""

    def test_re_export_identity(self) -> None:
        from weld.strategies._language_origin import JS_BUILTIN_GLOBALS

        self.assertIs(JS_STDLIB_GLOBALS, JS_BUILTIN_GLOBALS)


if __name__ == "__main__":
    unittest.main()
