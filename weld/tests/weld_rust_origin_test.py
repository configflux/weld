"""Unit tests for the Rust-specific origin helpers (ADR 0042 § Rust).

Covers the pure helpers in :mod:`weld.strategies._rust_origin`:

* The hard-coded Rust standard-library / runtime crate set
  (``RUST_STDLIB_CRATES``).
* The ``Cargo.toml`` parsers that extract the package name and the
  union of declared dependencies
  (:func:`parse_cargo_package_name`, :func:`parse_cargo_dependencies`).
* The four-way classifier that maps a use-path + manifest signals to
  one of ``project`` / ``stdlib`` / ``external`` / ``unresolved``
  (:func:`classify_rust_use_path`).

Strategy-level integration (the file-node ``imports_origin`` map
written by ``_rust_tree_sitter.stamp_import_origins``) is asserted in
:mod:`weld.tests.weld_rust_tree_sitter_test`. The fixture-based
acceptance test for the four-way classification on a small Rust
project (Cargo.toml + two .rs files, at least one declared
dependency) lives in :class:`RustOriginFixtureTest` and is the
per-issue acceptance gate.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path


from weld.strategies._rust_origin import (  # noqa: E402
    RUST_PROJECT_PATH_KEYWORDS,
    RUST_STDLIB_CRATES,
    classify_rust_use_path,
    parse_cargo_dependencies,
    parse_cargo_package_name,
)


# ---------------------------------------------------------------------------
# Stdlib detection (frozen-set surface)
# ---------------------------------------------------------------------------


class RustStdlibSurfaceTest(unittest.TestCase):
    def test_stdlib_set_is_frozenset(self) -> None:
        self.assertIsInstance(RUST_STDLIB_CRATES, frozenset)

    def test_stdlib_set_contains_canonical_crates(self) -> None:
        for crate in ("std", "core", "alloc", "proc_macro"):
            self.assertIn(crate, RUST_STDLIB_CRATES)

    def test_path_keywords_set_is_frozenset(self) -> None:
        self.assertIsInstance(RUST_PROJECT_PATH_KEYWORDS, frozenset)

    def test_path_keywords_set_canonical(self) -> None:
        for kw in ("crate", "self", "super"):
            self.assertIn(kw, RUST_PROJECT_PATH_KEYWORDS)


# ---------------------------------------------------------------------------
# Cargo.toml parsing
# ---------------------------------------------------------------------------


class ParseCargoPackageNameTest(unittest.TestCase):
    """``parse_cargo_package_name`` extracts ``[package].name``."""

    def test_simple(self) -> None:
        text = textwrap.dedent("""\
            [package]
            name = "myapi"
            version = "0.1.0"
            edition = "2021"
        """)
        self.assertEqual(parse_cargo_package_name(text), "myapi")

    def test_hyphen_normalised_to_underscore(self) -> None:
        text = textwrap.dedent("""\
            [package]
            name = "my-api"
            version = "0.1.0"
        """)
        # Cargo names with hyphens are imported by Rust as underscored
        # crate paths; the helper must normalise so callers can match
        # captured use-paths directly.
        self.assertEqual(parse_cargo_package_name(text), "my_api")

    def test_missing_package_table(self) -> None:
        text = '[dependencies]\nserde = "1.0"\n'
        self.assertEqual(parse_cargo_package_name(text), "")

    def test_missing_name_field(self) -> None:
        text = '[package]\nversion = "0.1.0"\n'
        self.assertEqual(parse_cargo_package_name(text), "")

    def test_empty_text(self) -> None:
        self.assertEqual(parse_cargo_package_name(""), "")

    def test_malformed_toml_falls_back(self) -> None:
        # Invalid TOML must not raise -- it returns "".
        text = "[package\nname = oops"
        self.assertEqual(parse_cargo_package_name(text), "")


class ParseCargoDependenciesTest(unittest.TestCase):
    """``parse_cargo_dependencies`` unions declared dependency tables."""

    MANIFEST = textwrap.dedent("""\
        [package]
        name = "myapi"
        version = "0.1.0"

        [dependencies]
        serde = "1.0"
        tokio = { version = "1.32", features = ["full"] }
        rename-me = { version = "0.2", package = "actual-crate" }

        [dev-dependencies]
        pretty_assertions = "1.4"

        [build-dependencies]
        cc = "1.0"
    """)

    def test_dependencies_section(self) -> None:
        deps = parse_cargo_dependencies(self.MANIFEST)
        self.assertIn("serde", deps)
        self.assertIn("tokio", deps)

    def test_dev_dependencies_section(self) -> None:
        self.assertIn("pretty_assertions", parse_cargo_dependencies(self.MANIFEST))

    def test_build_dependencies_section(self) -> None:
        self.assertIn("cc", parse_cargo_dependencies(self.MANIFEST))

    def test_rename_uses_table_key_not_package_field(self) -> None:
        # Cargo's ``foo = { package = "real-name" }`` rename pattern
        # exposes the dependency on the Rust side as ``foo``. The
        # classifier must store the table key, not the underlying
        # package name, so use-paths against ``rename_me`` (or
        # ``rename-me``) classify external.
        deps = parse_cargo_dependencies(self.MANIFEST)
        self.assertIn("rename_me", deps)
        # The underlying package name must NOT leak into the set.
        self.assertNotIn("actual_crate", deps)
        self.assertNotIn("actual-crate", deps)

    def test_workspace_dependencies(self) -> None:
        # Workspace-root manifests declare shared deps under
        # [workspace.dependencies]; the classifier folds those in.
        text = textwrap.dedent("""\
            [workspace]
            members = ["crate-a", "crate-b"]

            [workspace.dependencies]
            anyhow = "1.0"
            futures = "0.3"
        """)
        deps = parse_cargo_dependencies(text)
        self.assertIn("anyhow", deps)
        self.assertIn("futures", deps)

    def test_empty_text(self) -> None:
        self.assertEqual(parse_cargo_dependencies(""), frozenset())

    def test_malformed_toml_falls_back(self) -> None:
        self.assertEqual(parse_cargo_dependencies("[deps\noops"), frozenset())

    def test_no_dependency_tables(self) -> None:
        text = '[package]\nname = "myapi"\nversion = "0.1.0"\n'
        self.assertEqual(parse_cargo_dependencies(text), frozenset())

    def test_target_conditional_dependencies(self) -> None:
        # Cargo target-conditional tables -- ``[target.<spec>.dependencies]``
        # plus its dev-/build-dependencies sub-tables, across cfg-spec
        # and explicit target-triple keys -- must all fold into the
        # returned set. Hyphen normalisation and the rename-via-package
        # pattern apply identically. Without this fold, a crate whose
        # deps live entirely under target-conditional blocks would
        # classify as ``unresolved``.
        text = textwrap.dedent("""\
            [package]
            name = "myapi"

            [target.'cfg(unix)'.dependencies]
            libc = "0.2"
            some-crate = "1"
            local_name = { version = "1", package = "real-crate" }

            [target.'cfg(unix)'.dev-dependencies]
            tempfile = "3"

            [target.'cfg(unix)'.build-dependencies]
            bindgen = "0.69"

            [target.x86_64-pc-windows-msvc.dependencies]
            winapi = "0.3"

            [target.'cfg(target_os = "linux")'.dependencies]
            inotify = "0.10"
        """)
        deps = parse_cargo_dependencies(text)
        for name in (
            "libc", "some_crate", "local_name",
            "tempfile", "bindgen", "winapi", "inotify",
        ):
            self.assertIn(name, deps, f"{name!r} should fold in from target table")
        # Rename pattern: import-side key is stored, package field is not.
        self.assertNotIn("real_crate", deps)

    def test_target_section_malformed_does_not_raise(self) -> None:
        # A non-dict ``dependencies`` value under a target spec, and a
        # target spec that carries no dependency sub-table at all, must
        # both fall through silently. Plain ``[dependencies]`` still
        # surfaces.
        text = textwrap.dedent("""\
            [package]
            name = "myapi"

            [target.'cfg(unix)']
            rustflags = ["-C", "link-arg=-s"]

            [target.'cfg(windows)']
            dependencies = "not-a-table"

            [dependencies]
            serde = "1.0"
        """)
        self.assertEqual(parse_cargo_dependencies(text), frozenset({"serde"}))


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class ClassifyRustUsePathTest(unittest.TestCase):
    """``classify_rust_use_path`` returns the ADR-0042 four-way origin."""

    PACKAGE = "myapi"
    DEPS = frozenset({"serde", "tokio", "rename_me"})

    def _classify(self, path: str, *, package: str | None = None) -> str:
        return classify_rust_use_path(
            path,
            package_name=self.PACKAGE if package is None else package,
            dependencies=self.DEPS,
        )

    def test_stdlib_top_level(self) -> None:
        for path in ("std", "core", "alloc", "proc_macro"):
            self.assertEqual(
                self._classify(path), "stdlib",
                f"expected {path!r} to classify stdlib",
            )

    def test_stdlib_qualified_path(self) -> None:
        for path in (
            "std::collections::HashMap",
            "core::fmt::Debug",
            "alloc::boxed::Box",
        ):
            self.assertEqual(
                self._classify(path), "stdlib",
                f"expected {path!r} to classify stdlib",
            )

    def test_external_dependency(self) -> None:
        for path in ("serde", "tokio::runtime", "tokio::sync::Mutex"):
            self.assertEqual(
                self._classify(path), "external",
                f"expected {path!r} to classify external",
            )

    def test_external_renamed_dependency(self) -> None:
        # The renamed dependency is imported by its table-key shape.
        self.assertEqual(self._classify("rename_me::feature"), "external")

    def test_project_via_package_name(self) -> None:
        self.assertEqual(self._classify("myapi::handlers"), "project")

    def test_project_via_path_keyword(self) -> None:
        for path in ("crate::utils", "self::sibling", "super::parent"):
            self.assertEqual(
                self._classify(path), "project",
                f"expected {path!r} to classify project",
            )

    def test_unresolved_unknown(self) -> None:
        self.assertEqual(self._classify("some_unknown_crate::foo"), "unresolved")

    def test_empty_use_path_is_unresolved(self) -> None:
        self.assertEqual(self._classify(""), "unresolved")

    def test_leading_double_colon_tolerated(self) -> None:
        # Rust permits ``::std::foo`` to anchor at the crate root; the
        # classifier strips the prefix so the path still classifies as
        # stdlib.
        self.assertEqual(self._classify("::std::collections"), "stdlib")

    def test_no_manifest_falls_through(self) -> None:
        # No manifest: stdlib still classifies via the static crate
        # set, ``crate``/``self``/``super`` keywords still classify
        # project, and everything else falls through to unresolved
        # (NOT external -- without a manifest we cannot tell external
        # from project).
        no_deps = frozenset()
        self.assertEqual(
            classify_rust_use_path("std::io", package_name="", dependencies=no_deps),
            "stdlib",
        )
        self.assertEqual(
            classify_rust_use_path("crate::utils", package_name="", dependencies=no_deps),
            "project",
        )
        self.assertEqual(
            classify_rust_use_path("serde", package_name="", dependencies=no_deps),
            "unresolved",
        )

    def test_hyphen_normalised_use_path(self) -> None:
        # If a caller surfaces the hyphenated form, the classifier
        # still resolves via the underscore-normalised shape.
        self.assertEqual(self._classify("rename-me"), "external")


# ---------------------------------------------------------------------------
# Fixture-based assertion: the canned Cargo.toml + .rs files classify as expected
# ---------------------------------------------------------------------------


class RustOriginFixtureTest(unittest.TestCase):
    """Fixture mirrors a small Rust project: stdlib + project + external use-paths.

    The fixture under ``weld/tests/fixtures/rust_origin_project/`` declares
    a package name and dependencies in ``Cargo.toml`` and contains two
    ``.rs`` files whose ``use`` declarations cover stdlib, project, and
    external classifications.

    Per the issue acceptance gate: "A unit test fixtures a small Rust
    project (a couple of `.rs` files plus `Cargo.toml` declaring at
    least one dependency) and asserts the classification for project
    / external / stdlib."
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "rust_origin_project"
        )

    def test_fixture_exists(self) -> None:
        self.assertTrue((self.fixture / "Cargo.toml").is_file())
        self.assertTrue((self.fixture / "src" / "lib.rs").is_file())
        self.assertTrue((self.fixture / "src" / "handlers.rs").is_file())

    def test_manifest_parsed(self) -> None:
        text = (self.fixture / "Cargo.toml").read_text(encoding="utf-8")
        self.assertEqual(parse_cargo_package_name(text), "myapi")
        deps = parse_cargo_dependencies(text)
        for name in ("serde", "tokio", "pretty_assertions", "cc"):
            self.assertIn(name, deps)

    def _classify_with_fixture(self, path: str) -> str:
        text = (self.fixture / "Cargo.toml").read_text(encoding="utf-8")
        return classify_rust_use_path(
            path,
            package_name=parse_cargo_package_name(text),
            dependencies=parse_cargo_dependencies(text),
        )

    def test_lib_rs_classification(self) -> None:
        # lib.rs uses: std::collections, serde, crate::handlers
        self.assertEqual(self._classify_with_fixture("std::collections"), "stdlib")
        self.assertEqual(self._classify_with_fixture("serde::Serialize"), "external")
        self.assertEqual(self._classify_with_fixture("crate::handlers"), "project")

    def test_handlers_rs_classification(self) -> None:
        # handlers.rs uses: core::fmt, tokio::runtime, myapi::build_map
        self.assertEqual(self._classify_with_fixture("core::fmt::Debug"), "stdlib")
        self.assertEqual(
            self._classify_with_fixture("tokio::runtime::Runtime"), "external",
        )
        # ``myapi::build_map`` uses the package name itself as the
        # leading segment -- legitimately project-local even though
        # ``crate::`` is the more common idiom.
        self.assertEqual(self._classify_with_fixture("myapi::build_map"), "project")


if __name__ == "__main__":
    unittest.main()
