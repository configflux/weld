"""Tests for Rust tree-sitter import-origin enrichment (ADR 0042 § Rust).

Mirrors :mod:`weld.tests.weld_go_tree_sitter_test`. These tests assert
the wiring between the universal tree-sitter strategy and the Rust
helper module (:mod:`weld.strategies._rust_tree_sitter`):

* ``load_cargo_metadata`` reads ``root/Cargo.toml`` and returns the
  ``(package_name, dependencies)`` pair the classifier needs.
* ``import_origin_map`` classifies a list of raw use-paths against
  that pair.
* ``tree_sitter.extract`` stamps ``props.imports_origin`` on the Rust
  file node and preserves ``props.origin = "project"`` on the file
  node itself (the ADR 0042 acceptance criterion: every emitted node
  carries ``props.origin``).
* The wiring still works when no ``Cargo.toml`` is present (legacy
  fallback in :func:`weld._graph_origin.classify_node`).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from weld.strategies import _rust_tree_sitter


class RustTreeSitterCargoMetadataTest(unittest.TestCase):
    def test_load_cargo_metadata_reads_root_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Cargo.toml").write_text(
                textwrap.dedent("""\
                    [package]
                    name = "myapi"
                    version = "0.1.0"

                    [dependencies]
                    serde = "1.0"
                    tokio = "1.32"
                """),
                encoding="utf-8",
            )
            package, deps = _rust_tree_sitter.load_cargo_metadata(root, "rust")
            self.assertEqual(package, "myapi")
            self.assertIn("serde", deps)
            self.assertIn("tokio", deps)

    def test_load_cargo_metadata_short_circuits_non_rust(self) -> None:
        # Even when a Cargo.toml exists, a non-rust language must not
        # pay the parse cost; the helper short-circuits to the empty
        # pair.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "myapi"\n', encoding="utf-8",
            )
            self.assertEqual(
                _rust_tree_sitter.load_cargo_metadata(root, "go"),
                _rust_tree_sitter.EMPTY_CARGO_METADATA,
            )

    def test_load_cargo_metadata_missing_file_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(
                _rust_tree_sitter.load_cargo_metadata(root, "rust"),
                _rust_tree_sitter.EMPTY_CARGO_METADATA,
            )


class RustTreeSitterImportOriginMapTest(unittest.TestCase):
    def test_import_origin_map_classifies_use_paths(self) -> None:
        cargo: _rust_tree_sitter.CargoMetadata = (
            "myapi", frozenset({"serde", "tokio"}),
        )
        imports = ["std::collections", "serde", "crate::handlers", "tokio::runtime"]
        self.assertEqual(
            _rust_tree_sitter.import_origin_map(imports, cargo),
            {
                "crate::handlers": "project",
                "serde": "external",
                "std::collections": "stdlib",
                "tokio::runtime": "external",
            },
        )

    def test_import_origin_map_drops_blank_entries(self) -> None:
        cargo = ("myapi", frozenset())
        self.assertEqual(
            _rust_tree_sitter.import_origin_map(["", "std::io"], cargo),
            {"std::io": "stdlib"},
        )


class RustTreeSitterExtractIntegrationTest(unittest.TestCase):
    """``tree_sitter.extract`` stamps imports_origin on Rust file nodes."""

    def _make_rust_tree(self, tmp: str) -> Path:
        root = Path(tmp)
        src = root / "src"
        src.mkdir()
        (root / "Cargo.toml").write_text(
            textwrap.dedent("""\
                [package]
                name = "myapi"
                version = "0.1.0"

                [dependencies]
                serde = "1.0"
            """),
            encoding="utf-8",
        )
        (src / "lib.rs").write_text(
            "use std::collections::HashMap;\npub fn run() {}\n",
            encoding="utf-8",
        )
        return root

    def test_extract_stamps_imports_origin(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_rust_tree(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["run"],
                         "classes": [],
                         "imports": [
                             "std::collections",
                             "serde",
                             "crate::handlers",
                         ],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.rs", "language": "rust"},
                    {},
                )
        file_node = next(n for n in result.nodes.values() if n["type"] == "file")
        props = file_node["props"]
        # ADR 0042 acceptance: every emitted node carries props.origin.
        self.assertEqual(props["origin"], "project")
        self.assertIn("imports_origin", props)
        origins = props["imports_origin"]
        self.assertEqual(origins["std::collections"], "stdlib")
        self.assertEqual(origins["serde"], "external")
        self.assertEqual(origins["crate::handlers"], "project")

    def test_extract_without_cargo_toml_still_classifies_stdlib(self) -> None:
        # Existing graphs without a Cargo.toml must still classify
        # what they can (stdlib, ``crate::``) and emit project origin
        # on the file node itself. This exercises the legacy-fallback
        # acceptance criterion: the classify_node fallback in
        # :mod:`weld._graph_origin` still works on these graphs.
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "lib.rs").write_text(
                "use std::io;\npub fn run() {}\n", encoding="utf-8",
            )
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["run"],
                         "classes": [],
                         "imports": ["std::io"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.rs", "language": "rust"},
                    {},
                )
        file_node = next(n for n in result.nodes.values() if n["type"] == "file")
        props = file_node["props"]
        self.assertEqual(props["origin"], "project")
        self.assertEqual(props["imports_origin"]["std::io"], "stdlib")


if __name__ == "__main__":
    unittest.main()
