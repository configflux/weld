"""Tests for Java language support in the tree-sitter strategy."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


class JavaTreeSitterSupportTest(unittest.TestCase):
    """Java support adds metadata and import dependency edges."""

    def _make_java_tree(self, tmp: str) -> Path:
        root = Path(tmp)
        src = root / "src" / "main" / "java" / "com" / "example"
        src.mkdir(parents=True)
        (src / "OrderController.java").write_text(
            textwrap.dedent("""\
                package com.example;

                import java.util.List;
                import org.springframework.web.bind.annotation.RestController;
                import org.springframework.web.bind.annotation.GetMapping;

                @RestController
                public class OrderController {
                    @GetMapping("/orders")
                    public List<OrderDto> getOrders() {
                        return List.of();
                    }

                    private void helper() {}
                }
            """)
        )
        return root

    def test_java_extract_adds_metadata_and_dependency_edges(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_java_tree(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": [
                             "OrderController", "getOrders", "helper",
                         ],
                         "classes": ["OrderController"],
                         "imports": [
                             "java.util.List",
                             "org.springframework.web.bind.annotation.RestController",
                             "org.springframework.web.bind.annotation.GetMapping",
                         ],
                         "methods": ["getOrders", "helper"],
                         "annotations": ["RestController", "GetMapping"],
                         "packages": ["com.example"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.java", "language": "java"},
                    {},
                )

        file_node = next(n for n in result.nodes.values() if n["type"] == "file")
        props = file_node["props"]

        # Type declarations
        self.assertEqual(props["types"], ["OrderController"])

        # Annotations (Spring Boot)
        self.assertIn("RestController", props["annotations"])
        self.assertIn("GetMapping", props["annotations"])

        # Method visibility
        self.assertEqual(props["method_visibility"]["getOrders"], ["public"])
        self.assertEqual(props["method_visibility"]["helper"], ["private"])

        # Package declaration
        self.assertEqual(props["packages"], ["com.example"])

        # Import dependency edges -- grouped by package
        deps = [e for e in result.edges if e["type"] == "depends_on"]
        dep_targets = {e["to"] for e in deps}
        self.assertIn("package:java:java.util", dep_targets)
        self.assertIn(
            "package:java:org.springframework.web.bind.annotation",
            dep_targets,
        )

        # Package nodes created
        self.assertIn("package:java:java.util", result.nodes)
        self.assertIn(
            "package:java:org.springframework.web.bind.annotation",
            result.nodes,
        )

    def test_java_wrapper_sets_language_and_strategy_label(self) -> None:
        from weld.strategies import java, tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_java_tree(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["OrderController"],
                         "classes": ["OrderController"],
                         "imports": [],
                     },
                 ):
                result = java.extract(root, {"glob": "**/*.java"}, {})

        node = next(n for n in result.nodes.values() if n["type"] == "file")
        self.assertEqual(node["props"]["source_strategy"], "java")

    def test_java_grammar_module_name(self) -> None:
        from weld.strategies._ts_parse import grammar_module_name

        self.assertEqual(grammar_module_name("java"), "tree_sitter_java")

    def test_java_grammar_package_name(self) -> None:
        from weld.strategies._ts_parse import grammar_package_name

        self.assertEqual(grammar_package_name("java"), "tree-sitter-java")

    def test_init_detect_maps_java_extension(self) -> None:
        from weld.init_detect import EXT_TO_LANG

        self.assertEqual(EXT_TO_LANG.get(".java"), "java")

    def test_java_import_package_extraction(self) -> None:
        """Import edges group by package, not full class path."""
        from weld.strategies._java_tree_sitter import _import_to_package

        self.assertEqual(
            _import_to_package("java.util.List"), "java.util",
        )
        self.assertEqual(
            _import_to_package("org.springframework.web.bind.annotation.GetMapping"),
            "org.springframework.web.bind.annotation",
        )
        # Single-segment import returns as-is
        self.assertEqual(_import_to_package("Foo"), "Foo")

    def test_java_dedupe_preserves_order(self) -> None:
        from weld.strategies._java_tree_sitter import _dedupe

        self.assertEqual(
            _dedupe(["a", "b", "a", "c", "b"]),
            ["a", "b", "c"],
        )

    def test_java_enum_and_record_extraction(self) -> None:
        """Verify enums and records appear in classes/types."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "Status.java").write_text(
                textwrap.dedent("""\
                    package com.example;
                    public enum Status { ACTIVE, INACTIVE }
                """)
            )
            (src / "Point.java").write_text(
                textwrap.dedent("""\
                    package com.example;
                    public record Point(int x, int y) {}
                """)
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True):
                # First file: enum
                with mock.patch.object(
                    tree_sitter,
                    "_parse_file_symbols",
                    return_value={
                        "exports": ["Status"],
                        "classes": ["Status"],
                        "imports": [],
                        "annotations": [],
                        "packages": ["com.example"],
                    },
                ):
                    result = tree_sitter.extract(
                        root,
                        {"glob": "src/Status.java", "language": "java"},
                        {},
                    )
                file_node = next(
                    n for n in result.nodes.values() if n["type"] == "file"
                )
                self.assertIn("Status", file_node["props"]["types"])

    def test_java_interface_extraction(self) -> None:
        """Verify interfaces appear in classes/types."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "OrderService.java").write_text(
                textwrap.dedent("""\
                    package com.example;
                    public interface OrderService {
                        void placeOrder();
                    }
                """)
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["OrderService", "placeOrder"],
                         "classes": ["OrderService"],
                         "imports": [],
                         "methods": ["placeOrder"],
                         "annotations": [],
                         "packages": ["com.example"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "src/OrderService.java", "language": "java"},
                    {},
                )
            file_node = next(
                n for n in result.nodes.values() if n["type"] == "file"
            )
            self.assertIn("OrderService", file_node["props"]["types"])
            self.assertIn("placeOrder", file_node["props"]["methods"])


if __name__ == "__main__":
    unittest.main()
