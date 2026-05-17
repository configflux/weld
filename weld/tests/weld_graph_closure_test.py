"""Tests for deterministic multi-language graph closure."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.graph_closure import close_graph  # noqa: E402
from weld.serializer import dumps_graph  # noqa: E402


LANGUAGE_MATRIX = {
    "python": ("src/python/main.py", "src/python/dep.py", "./dep", "json"),
    "typescript": ("web/main.ts", "web/dep.ts", "./dep", "react"),
    "go": ("go/main.go", "go/internal/dep.go", "go/internal/dep", "net/http"),
    "rust": ("src/main.rs", "src/dep.rs", "crate::dep", "serde"),
    "csharp": ("src/csharp/Main.cs", "src/csharp/Dep.cs", "src.csharp.Dep", "System.Text"),
    "cpp": ("src/cpp/main.cpp", "src/cpp/dep.h", "dep.h", "vector"),
    "java": (
        "src/main/java/com/acme/Main.java",
        "src/main/java/com/acme/Dep.java",
        "com.acme.Dep",
        "java.util.List",
    ),
    "python_ros2": ("ros_py/node.py", "ros_py/helper.py", "./helper", "rclpy"),
    "cpp_ros2": ("ros_cpp/node.cpp", "ros_cpp/node.hpp", "node.hpp", "rclcpp"),
}


class GraphClosureLanguageMatrixTest(unittest.TestCase):
    def test_matrix_covers_every_supported_language_file(self) -> None:
        actual = {path.stem for path in (_repo_root / "weld" / "languages").glob("*.yaml")}
        self.assertEqual(actual, set(LANGUAGE_MATRIX))

    def test_closes_each_supported_language(self) -> None:
        for language in LANGUAGE_MATRIX:
            with self.subTest(language=language):
                nodes, edges = _fixture(language)
                close_graph(nodes, edges)

                file_id = f"file:{language}:main"
                dep_id = f"file:{language}:dep"
                symbol_id = f"symbol:{language}:main:Thing"
                sentinel = f"symbol:unresolved:{language}_helper"

                self.assertIn(_edge_key(file_id, symbol_id, "contains"), _edge_keys(edges))
                self.assertIn(_edge_key(file_id, dep_id, "depends_on"), _edge_keys(edges))
                self.assertIn(sentinel, nodes)
                self.assertTrue(_external_packages(nodes, language))

                call = _find_edge(edges, symbol_id, sentinel, "calls")
                self.assertEqual(call["props"]["raw"], f"{language}_helper")
                self.assertFalse(call["props"]["resolved"])
                self.assertEqual(call["props"]["resolution"], "unresolved")
                self.assertEqual(call["props"]["provenance"]["file"], LANGUAGE_MATRIX[language][0])

                for edge in edges:
                    self.assertIn(edge["from"], nodes)
                    self.assertIn(edge["to"], nodes)

    def test_ros2_surfaces_are_attached_to_source_files(self) -> None:
        for language, extra_type in (("python_ros2", "channel"), ("cpp_ros2", "rpc")):
            with self.subTest(language=language):
                nodes, edges = _fixture(language)
                close_graph(nodes, edges)
                file_id = f"file:{language}:main"
                ros_id = f"ros_node:{language}:talker"
                extra_id = f"{extra_type}:{language}:surface"
                keys = _edge_keys(edges)
                self.assertIn(_edge_key(file_id, ros_id, "contains"), keys)
                self.assertIn(_edge_key(file_id, extra_id, "contains"), keys)

    def test_creates_file_anchor_for_symbol_without_file_node(self) -> None:
        nodes = {
            "symbol:py:pkg.mod:helper": {
                "type": "symbol",
                "label": "helper",
                "props": {"file": "pkg/mod.py", "language": "python"},
            }
        }
        edges: list[dict] = []
        close_graph(nodes, edges)
        self.assertIn("file:pkg/mod", nodes)
        self.assertIn(
            _edge_key("file:pkg/mod", "symbol:py:pkg.mod:helper", "contains"),
            _edge_keys(edges),
        )

    def test_closure_is_byte_identical_across_runs(self) -> None:
        nodes1, edges1 = _all_language_fixture()
        nodes2, edges2 = copy.deepcopy(nodes1), copy.deepcopy(edges1)
        close_graph(nodes1, edges1)
        close_graph(nodes2, edges2)
        graph1 = {"meta": {}, "nodes": nodes1, "edges": edges1}
        graph2 = {"meta": {}, "nodes": nodes2, "edges": edges2}
        self.assertEqual(dumps_graph(graph1), dumps_graph(graph2))

    def test_csharp_external_packages_collapse_case_variants(self) -> None:
        """Case-variant C# imports must collapse to ONE ``package:csharp:*``.

        Regression: a discovery dogfood pass on a real C# repo surfaced
        ~130 case-variant ``package:csharp:*`` pairs (e.g.
        ``package:csharp:System`` next to ``package:csharp:system``).
        Strategy-side minters route through
        :func:`weld._node_ids.canonical_slug` (lowercased), but
        ``graph_closure._ensure_package_node`` used a local case-preserving
        slug, so when ``_link_imports`` synthesised an external package for
        a C# ``imports_from`` entry it created a duplicate node. C#
        namespaces are case-insensitive (the BCL treats ``System`` and
        ``system`` as the same namespace), so the closure path must
        canonicalise them.

        Complement of the symbol-id case decision, which preserves case for
        ``symbol:*`` IDs because most languages (including C#) treat
        ``SIZE`` and ``Size`` as legitimately distinct *members* of the
        same enclosing type.
        """
        nodes = {
            "file:src/upper": {
                "type": "file",
                "label": "upper",
                "props": {
                    "file": "src/upper.cs",
                    "language": "csharp",
                    "imports_from": ["System.Foo", "Avalonia.Controls"],
                },
            },
            "file:src/lower": {
                "type": "file",
                "label": "lower",
                "props": {
                    "file": "src/lower.cs",
                    "language": "csharp",
                    "imports_from": ["system.foo", "avalonia.controls"],
                },
            },
        }
        edges: list[dict] = []
        close_graph(nodes, edges)

        package_ids = {nid for nid in nodes if nid.startswith("package:csharp:")}
        # Both case-variants of every namespace must collapse to ONE node.
        self.assertEqual(
            package_ids,
            {"package:csharp:system.foo", "package:csharp:avalonia.controls"},
            "case-variant C# namespaces must collapse to a single canonical "
            "package node; got case-preserving duplicates instead",
        )
        # No package id may differ from another only by case.
        case_keys = [pid.casefold() for pid in package_ids]
        self.assertEqual(len(case_keys), len(set(case_keys)),
                         "found case-variant duplicate package ids")


class GraphClosurePackageOriginTest(unittest.TestCase):
    """ADR 0042: closure-synthesised package nodes must carry props.origin.

    ``_ensure_package_node`` mints a ``package:<lang>:<name>`` node every
    time ``_link_imports`` cannot resolve an ``imports_from`` entry to a
    local file or module. An earlier revision left ``props.origin``
    unset, which forced ``classify_node``'s transitional legacy fallback
    to map *every* synthesised package to ``external`` via
    ``authority=external`` -- wrong for stdlib names like ``builtins``
    and ``collections.abc``. The Phase-7 cleanup then removed that
    fallback entirely, so a regression here would now produce
    ``unresolved`` and surface in viz / brief. The strategy must
    classify each synthesised package against the language's stdlib
    list and stamp the right origin directly.
    """

    def _close(self, language: str, imports: list[str]) -> dict[str, dict]:
        ext = {
            "python": ".py",
            "go": ".go",
            "rust": ".rs",
            "java": ".java",
            "csharp": ".cs",
            "typescript": ".ts",
            "cpp": ".cpp",
        }[language]
        nodes: dict[str, dict] = {
            "file:src/main": {
                "type": "file",
                "label": "main",
                "props": {
                    "file": f"src/main{ext}",
                    "language": language,
                    "imports_from": imports,
                },
            },
        }
        edges: list[dict] = []
        close_graph(nodes, edges)
        return nodes

    def test_python_stdlib_imports_tag_origin_stdlib(self) -> None:
        # ``builtins`` is in ``dir(builtins)`` and ``collections.abc``
        # has a stdlib root segment per ``sys.stdlib_module_names``;
        # both must classify ``stdlib``, not ``external``.
        nodes = self._close("python", ["builtins", "collections.abc"])
        builtins_node = nodes["package:python:builtins"]
        collections_node = nodes["package:python:collections.abc"]
        self.assertEqual(builtins_node["props"]["origin"], "stdlib")
        self.assertEqual(collections_node["props"]["origin"], "stdlib")

    def test_python_external_imports_tag_origin_external(self) -> None:
        # ``numpy`` is not in the Python stdlib and not project-local;
        # must classify ``external``.
        nodes = self._close("python", ["numpy"])
        node = nodes["package:python:numpy"]
        self.assertEqual(node["props"]["origin"], "external")

    def test_go_stdlib_path_tags_origin_stdlib(self) -> None:
        # canonical_package_id converts ``/`` to ``-`` in the slug; the
        # raw ``props.name`` still carries the original path so the
        # closure helper can match it against the static stdlib set.
        nodes = self._close("go", ["net/http"])
        node = nodes["package:go:net-http"]
        self.assertEqual(node["props"]["name"], "net/http")
        self.assertEqual(node["props"]["origin"], "stdlib")

    def test_go_external_path_tags_origin_external(self) -> None:
        nodes = self._close("go", ["github.com/example/foo"])
        node = nodes["package:go:github.com-example-foo"]
        self.assertEqual(node["props"]["origin"], "external")

    def test_rust_std_crate_tags_origin_stdlib(self) -> None:
        # Rust ``use std::fs`` import path arriving at the closure is the
        # leading ``std`` crate segment; must classify ``stdlib``.
        nodes = self._close("rust", ["std"])
        node = nodes["package:rust:std"]
        self.assertEqual(node["props"]["origin"], "stdlib")

    def test_rust_external_crate_tags_origin_external(self) -> None:
        nodes = self._close("rust", ["serde"])
        node = nodes["package:rust:serde"]
        self.assertEqual(node["props"]["origin"], "external")

    def test_java_jdk_package_tags_origin_stdlib(self) -> None:
        # ``java.util.List`` strips to ``java.util`` (Java
        # ``_external_package_name`` removes the trailing class name);
        # first segment ``java`` is a JDK stdlib root.
        nodes = self._close("java", ["java.util.List"])
        node = nodes["package:java:java.util"]
        self.assertEqual(node["props"]["origin"], "stdlib")

    def test_java_external_package_tags_origin_external(self) -> None:
        nodes = self._close("java", ["org.springframework.web.Foo"])
        node = nodes["package:java:org.springframework.web"]
        self.assertEqual(node["props"]["origin"], "external")

    def test_csharp_system_namespace_tags_origin_stdlib(self) -> None:
        # canonical_package_id lowercases the slug; the helper must
        # classify case-insensitively so the raw ``System.Text`` input
        # still resolves to a ``System`` stdlib root.
        nodes = self._close("csharp", ["System.Text"])
        node = nodes["package:csharp:system.text"]
        self.assertEqual(node["props"]["origin"], "stdlib")

    def test_csharp_external_namespace_tags_origin_external(self) -> None:
        nodes = self._close("csharp", ["Avalonia.Controls"])
        node = nodes["package:csharp:avalonia.controls"]
        self.assertEqual(node["props"]["origin"], "external")

    def test_typescript_external_module_tags_origin_external(self) -> None:
        # No closure-side stdlib list for TS/JS at the package layer
        # (built-in globals live behind tree-sitter call-graph dispatch,
        # not import paths). Any synthesised package node falls through
        # to ``external``.
        nodes = self._close("typescript", ["react"])
        node = nodes["package:typescript:react"]
        self.assertEqual(node["props"]["origin"], "external")

    def test_cpp_external_header_tags_origin_external(self) -> None:
        # The closure layer has no system-include-root context; every
        # synthesised C++ package node falls through to ``external``.
        # Richer per-include classification ships in cpp_resolver.
        nodes = self._close("cpp", ["vector"])
        node = nodes["package:cpp:vector"]
        self.assertEqual(node["props"]["origin"], "external")

    def test_origin_does_not_displace_legacy_props(self) -> None:
        # The pre-ADR-0042 contract for synthesised package nodes was
        # ``external=True``, ``authority="external"``,
        # ``source_strategy="graph_closure"``,
        # ``confidence="inferred"``. Adding ``origin`` must not displace
        # any of those; downstream consumers (cross_repo, viz,
        # determinism gates) still rely on the pre-existing scalar
        # fields independently of the new origin tag.
        nodes = self._close("python", ["numpy"])
        props = nodes["package:python:numpy"]["props"]
        self.assertEqual(props["external"], True)
        self.assertEqual(props["authority"], "external")
        self.assertEqual(props["source_strategy"], "graph_closure")
        self.assertEqual(props["confidence"], "inferred")
        self.assertEqual(props["origin"], "external")


def _fixture(language: str) -> tuple[dict[str, dict], list[dict]]:
    source, dep, local_import, external_import = LANGUAGE_MATRIX[language]
    file_id = f"file:{language}:main"
    dep_id = f"file:{language}:dep"
    symbol_id = f"symbol:{language}:main:Thing"
    sentinel = f"symbol:unresolved:{language}_helper"
    nodes = {
        file_id: _file(source, language, [local_import, external_import]),
        dep_id: _dep_file(dep, language),
        symbol_id: _symbol(source, language),
        sentinel: {
            "type": "symbol",
            "label": f"{language}_helper",
            "props": {"language": language, "resolved": False},
        },
    }
    if language.endswith("_ros2"):
        nodes[f"ros_node:{language}:talker"] = _surface("ros_node", source, language)
        surface_type = "channel" if language == "python_ros2" else "rpc"
        nodes[f"{surface_type}:{language}:surface"] = _surface(surface_type, source, language)
    edges = [{"from": symbol_id, "to": sentinel, "type": "calls", "props": {}}]
    return nodes, edges


def _all_language_fixture() -> tuple[dict[str, dict], list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for language in LANGUAGE_MATRIX:
        lang_nodes, lang_edges = _fixture(language)
        nodes.update(lang_nodes)
        edges.extend(lang_edges)
    return nodes, edges


def _file(path: str, language: str, imports: list[str]) -> dict:
    return {
        "type": "file",
        "label": Path(path).stem,
        "props": {"file": path, "language": language, "imports_from": imports},
    }


def _dep_file(path: str, language: str) -> dict:
    props = {"file": path, "language": language}
    if language == "java":
        props.update({"packages": ["com.acme"], "exports": ["Dep"]})
    return {"type": "file", "label": Path(path).stem, "props": props}


def _symbol(path: str, language: str) -> dict:
    return {
        "type": "symbol",
        "label": "Thing",
        "props": {"file": path, "language": language, "qualname": "Thing", "line": 7},
    }


def _surface(node_type: str, path: str, language: str) -> dict:
    return {"type": node_type, "label": "surface", "props": {"file": path, "language": language}}


def _external_packages(nodes: dict[str, dict], language: str) -> list[str]:
    base = "python" if language == "python_ros2" else "cpp" if language == "cpp_ros2" else language
    return [
        node_id for node_id, node in nodes.items()
        if node_id.startswith(f"package:{base}:") and node["props"].get("external")
    ]


def _edge_key(src: str, dst: str, edge_type: str) -> tuple[str, str, str]:
    return src, dst, edge_type


def _edge_keys(edges: list[dict]) -> set[tuple[str, str, str]]:
    return {_edge_key(edge["from"], edge["to"], edge["type"]) for edge in edges}


def _find_edge(edges: list[dict], src: str, dst: str, edge_type: str) -> dict:
    for edge in edges:
        if _edge_key(edge["from"], edge["to"], edge["type"]) == _edge_key(src, dst, edge_type):
            return edge
    raise AssertionError(f"missing edge {src} {edge_type} {dst}")


if __name__ == "__main__":
    unittest.main()
