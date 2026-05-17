"""ADR 0042 origin classification tests for the Java strategy.

Companion to ``weld_java_treesitter_test``; this module focuses on
``props.origin`` classification of Java import packages: the
``java.*``/``javax.*``/``jdk.*`` JDK stdlib prefixes, project groupId
detection from ``pom.xml``, and external classification from
``<dependency>`` declarations.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


class JavaOriginIntegrationTest(unittest.TestCase):
    """End-to-end: tree_sitter.extract emits classified package nodes."""

    def test_classifies_stdlib_external_project_unresolved(self) -> None:
        """Java imports get origin=stdlib/external/project from pom.xml.

        Fixtures a small Maven project that declares its own groupId
        and one third-party dependency, then asserts each of the four
        ADR 0042 origin tags lands on the right package node:

        * ``java.util`` -> ``stdlib`` (JDK prefix)
        * ``org.springframework.web.bind.annotation`` -> ``external``
          (matches the declared dependency groupId)
        * ``com.example.shop.api`` -> ``project`` (under the project
          groupId)
        * ``net.unknown.lib`` -> ``unresolved`` (neither stdlib nor
          declared anywhere)
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pom.xml").write_text(
                textwrap.dedent("""\
                    <?xml version="1.0" encoding="UTF-8"?>
                    <project xmlns="http://maven.apache.org/POM/4.0.0">
                      <groupId>com.example.shop</groupId>
                      <artifactId>shop-api</artifactId>
                      <version>1.0.0</version>
                      <dependencies>
                        <dependency>
                          <groupId>org.springframework</groupId>
                          <artifactId>spring-web</artifactId>
                          <version>6.1.0</version>
                        </dependency>
                      </dependencies>
                    </project>
                """),
                encoding="utf-8",
            )
            src = root / "src" / "main" / "java" / "com" / "example" / "shop"
            src.mkdir(parents=True)
            (src / "OrderController.java").write_text(
                textwrap.dedent("""\
                    package com.example.shop;
                    import java.util.List;
                    import org.springframework.web.bind.annotation.RestController;
                    import com.example.shop.api.OrderApi;
                    import net.unknown.lib.Helper;

                    @RestController
                    public class OrderController {}
                """),
                encoding="utf-8",
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["OrderController"],
                         "classes": ["OrderController"],
                         "imports": [
                             "java.util.List",
                             "org.springframework.web.bind.annotation.RestController",
                             "com.example.shop.api.OrderApi",
                             "net.unknown.lib.Helper",
                         ],
                         "annotations": ["RestController"],
                         "packages": ["com.example.shop"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.java", "language": "java"},
                    {},
                )

        self.assertEqual(
            result.nodes["package:java:java.util"]["props"]["origin"],
            "stdlib",
        )
        self.assertEqual(
            result.nodes[
                "package:java:org.springframework.web.bind.annotation"
            ]["props"]["origin"],
            "external",
        )
        self.assertEqual(
            result.nodes["package:java:com.example.shop.api"]["props"]["origin"],
            "project",
        )
        self.assertEqual(
            result.nodes["package:java:net.unknown.lib"]["props"]["origin"],
            "unresolved",
        )

    def test_classifies_javax_and_jdk_as_stdlib(self) -> None:
        """``javax.*`` and ``jdk.*`` prefixes also classify as stdlib."""
        from weld.strategies import _java_tree_sitter

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        node_props: dict = {"file": "Foo.java"}
        symbols: dict[str, list[str]] = {
            "imports": [
                "javax.crypto.Cipher",
                "jdk.incubator.foreign.MemoryAddress",
            ],
            "exports": [],
            "classes": [],
        }
        _java_tree_sitter.enrich_file_node(
            nodes, edges, "file:foo", node_props, symbols, "", "java",
        )
        self.assertEqual(
            nodes["package:java:javax.crypto"]["props"]["origin"], "stdlib",
        )
        self.assertEqual(
            nodes["package:java:jdk.incubator.foreign"]["props"]["origin"],
            "stdlib",
        )


class JavaOriginHelperTest(unittest.TestCase):
    """Pure-helper unit tests for ``classify_import_package``."""

    def test_classify_each_origin(self) -> None:
        """``classify_import_package`` is total over the four origins."""
        from weld.strategies._java_origin import classify_import_package

        project = frozenset({"com.example.shop"})
        deps = frozenset({"org.springframework"})

        self.assertEqual(
            classify_import_package(
                "java.util",
                project_groupids=project,
                dependency_groupids=deps,
            ),
            "stdlib",
        )
        self.assertEqual(
            classify_import_package(
                "org.springframework.web",
                project_groupids=project,
                dependency_groupids=deps,
            ),
            "external",
        )
        self.assertEqual(
            classify_import_package(
                "com.example.shop.api",
                project_groupids=project,
                dependency_groupids=deps,
            ),
            "project",
        )
        self.assertEqual(
            classify_import_package(
                "net.unknown",
                project_groupids=project,
                dependency_groupids=deps,
            ),
            "unresolved",
        )

    def test_classify_empty_is_unresolved(self) -> None:
        from weld.strategies._java_origin import classify_import_package

        self.assertEqual(
            classify_import_package(
                "",
                project_groupids=frozenset(),
                dependency_groupids=frozenset(),
            ),
            "unresolved",
        )

    def test_project_wins_over_dependency_collision(self) -> None:
        """A project groupId always overrides a same-named external dep."""
        from weld.strategies._java_origin import classify_import_package

        self.assertEqual(
            classify_import_package(
                "com.example",
                project_groupids=frozenset({"com.example"}),
                dependency_groupids=frozenset({"com.example"}),
            ),
            "project",
        )


class JavaOriginPomMetadataTest(unittest.TestCase):
    """Pom-discovery helper tests for ``load_pom_metadata``."""

    def test_handles_parent_inheritance(self) -> None:
        """A child pom inherits ``<parent><groupId>`` when no own groupId."""
        from weld.strategies._java_origin import load_pom_metadata

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pom.xml").write_text(
                textwrap.dedent("""\
                    <project xmlns="http://maven.apache.org/POM/4.0.0">
                      <parent>
                        <groupId>com.example.parent</groupId>
                        <artifactId>parent</artifactId>
                        <version>1.0</version>
                      </parent>
                      <artifactId>child</artifactId>
                    </project>
                """),
                encoding="utf-8",
            )
            meta = load_pom_metadata(root)
        self.assertIn("com.example.parent", meta["project_groupids"])

    def test_skips_unparseable_pom(self) -> None:
        """Malformed poms are skipped, not raised."""
        from weld.strategies._java_origin import load_pom_metadata

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pom.xml").write_text("<project>oops", encoding="utf-8")
            meta = load_pom_metadata(root)
        self.assertEqual(meta["project_groupids"], frozenset())
        self.assertEqual(meta["dependency_groupids"], frozenset())

    def test_aggregates_dependency_groupids_across_poms(self) -> None:
        """``<dependencies>`` blocks across multiple poms are merged."""
        from weld.strategies._java_origin import load_pom_metadata

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pom.xml").write_text(
                textwrap.dedent("""\
                    <project>
                      <groupId>com.example</groupId>
                      <artifactId>app</artifactId>
                      <dependencies>
                        <dependency>
                          <groupId>com.fasterxml.jackson.core</groupId>
                          <artifactId>jackson-databind</artifactId>
                        </dependency>
                      </dependencies>
                    </project>
                """),
                encoding="utf-8",
            )
            mod = root / "module-a"
            mod.mkdir()
            (mod / "pom.xml").write_text(
                textwrap.dedent("""\
                    <project>
                      <groupId>com.example.module</groupId>
                      <artifactId>module-a</artifactId>
                      <dependencies>
                        <dependency>
                          <groupId>org.slf4j</groupId>
                          <artifactId>slf4j-api</artifactId>
                        </dependency>
                      </dependencies>
                    </project>
                """),
                encoding="utf-8",
            )
            meta = load_pom_metadata(root)
        self.assertIn("com.fasterxml.jackson.core", meta["dependency_groupids"])
        self.assertIn("org.slf4j", meta["dependency_groupids"])
        self.assertIn("com.example", meta["project_groupids"])
        self.assertIn("com.example.module", meta["project_groupids"])

    def test_dependency_management_block_counts_as_external(self) -> None:
        """``<dependencyManagement><dependencies>`` also yields groupIds."""
        from weld.strategies._java_origin import load_pom_metadata

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pom.xml").write_text(
                textwrap.dedent("""\
                    <project>
                      <groupId>com.example</groupId>
                      <artifactId>parent</artifactId>
                      <dependencyManagement>
                        <dependencies>
                          <dependency>
                            <groupId>io.netty</groupId>
                            <artifactId>netty-all</artifactId>
                            <version>4.1.0</version>
                          </dependency>
                        </dependencies>
                      </dependencyManagement>
                    </project>
                """),
                encoding="utf-8",
            )
            meta = load_pom_metadata(root)
        self.assertIn("io.netty", meta["dependency_groupids"])


class JavaOriginMissingTagTest(unittest.TestCase):
    """Java package nodes that arrive without ``props.origin``.

    Phase 7 of the origin-taxonomy plan removed the transitional
    legacy-graph derivation in ``classify_node``. A Java package node
    that reaches the classifier without an explicit origin tag (e.g.
    a graph snapshot emitted by a pre-ADR-0042 strategy version, or a
    hand-crafted fixture) now resolves to ``"unresolved"`` so the gap
    surfaces in viz / brief / ranking instead of being silently masked
    as ``"project"``. The Java strategy itself always stamps origin
    via :func:`weld.strategies._java_origin.classify_java_node`; this
    test pins the contract for foreign / legacy inputs.
    """

    def test_classify_node_returns_unresolved_for_legacy_package(self) -> None:
        """A package node with no ``origin`` field is unresolved."""
        from weld._graph_origin import classify_node

        legacy_node = {
            "id": "package:java:org.example",
            "type": "package",
            "props": {
                "name": "org.example",
                "language": "java",
                "authority": "derived",
            },
        }
        self.assertEqual(classify_node(legacy_node), "unresolved")


if __name__ == "__main__":
    unittest.main()
