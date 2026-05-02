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
