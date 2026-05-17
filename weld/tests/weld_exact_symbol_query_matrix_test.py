"""Regression coverage for exact symbol lookup across supported languages."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld import _sqlite_writer as sqlite_writer  # noqa: E402
from weld.federation import FederatedGraph  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.serializer import dumps_graph  # noqa: E402
from weld.strategies import tree_sitter  # noqa: E402
from weld.strategies import _ts_call_graph  # noqa: E402
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml  # noqa: E402

_TS = "2026-04-15T20:30:00+00:00"

LANGUAGE_CASES = (
    ("python", "pkg/service.py", {"exports": ["ExactTarget"], "classes": []}, "ExactTarget"),
    ("typescript", "src/service.ts", {"exports": ["ExactTarget"], "classes": []}, "ExactTarget"),
    ("go", "service.go", {"exports": ["ExactTarget"], "classes": []}, "ExactTarget"),
    ("rust", "src/lib.rs", {"exports": ["exact_target"], "classes": []}, "exact_target"),
    (
        "java",
        "src/Service.java",
        {"exports": ["ExactTarget"], "classes": ["ExactTarget"]},
        "ExactTarget",
    ),
    (
        "csharp",
        "src/OrdersController.cs",
        {
            "exports": [
                "Sample.Api.Controllers",
                "OrdersController",
                "GetAsync",
                "Status",
            ],
            "classes": ["OrdersController"],
            "methods": ["GetAsync"],
            "properties": ["Status"],
            "namespaces": ["Sample.Api.Controllers"],
        },
        "GetAsync",
    ),
)


def _graph_from_tree_sitter(
    language: str,
    rel_path: str,
    symbols: dict[str, list[str]],
) -> tuple[Graph, dict]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    source = root / rel_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(_source_text(language), encoding="utf-8")
    symbols = {**symbols, "imports": list(symbols.get("imports", []))}
    with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
         mock.patch.object(tree_sitter, "_parse_file_symbols", return_value=symbols):
        result = tree_sitter.extract(
            root,
            {"glob": f"**/*{source.suffix}", "language": language},
            {},
        )
    graph = Graph(root)
    graph._data = {"meta": {}, "nodes": result.nodes, "edges": result.edges}
    graph._build_inverted_index()
    graph._tempdir = tmp  # type: ignore[attr-defined]
    return graph, {"nodes": result.nodes, "edges": result.edges}


def _source_text(language: str) -> str:
    if language == "csharp":
        return textwrap.dedent("""\
            namespace Sample.Api.Controllers;
            public class OrdersController {
                public string Status => "ok";
                public Task<OrderDto> GetAsync(int id) => null;
            }
        """)
    return "class ExactTarget {}\n"


class ExactSymbolTreeSitterMatrixTest(unittest.TestCase):
    def test_exact_definition_queries_return_symbols_for_t1_languages(self) -> None:
        for language, rel_path, symbols, query in LANGUAGE_CASES:
            with self.subTest(language=language):
                graph, data = _graph_from_tree_sitter(language, rel_path, symbols)

                matches = graph.query(query, limit=3)["matches"]

                self.assertGreater(len(matches), 0)
                self.assertEqual(matches[0]["type"], "symbol")
                self.assertEqual(matches[0]["label"], query)
                self.assertEqual(matches[0]["props"]["qualname"], query)
                self.assertTrue(_has_file_contains_edge(data, matches[0]["id"]))

    def test_csharp_exact_class_and_method_queries_beat_file_exports(self) -> None:
        graph, data = _graph_from_tree_sitter(
            "csharp",
            "src/OrdersController.cs",
            {
                "exports": [
                    "Sample.Api.Controllers",
                    "OrdersController",
                    "GetAsync",
                ],
                "classes": ["OrdersController"],
                "methods": ["GetAsync"],
                "properties": [],
                "namespaces": ["Sample.Api.Controllers"],
            },
        )

        method = graph.query("GetAsync", limit=1)["matches"][0]
        klass = graph.query("OrdersController", limit=1)["matches"][0]

        self.assertEqual(method["type"], "symbol")
        self.assertEqual(klass["type"], "symbol")
        self.assertTrue(_has_file_contains_edge(data, method["id"]))
        self.assertTrue(_has_file_contains_edge(data, klass["id"]))
        symbol_labels = {
            node["label"]
            for node in data["nodes"].values()
            if node.get("type") == "symbol"
        }
        self.assertNotIn("Sample.Api.Controllers", symbol_labels)

    def test_csharp_callgraph_definitions_skip_namespace_query(self) -> None:
        # ADR 0064 § 1: per-decl-kind buckets are mandatory for the
        # call-graph layer too -- otherwise interface/struct/record
        # definitions never mint symbol nodes for unresolved-call
        # sentinels to resolve into.
        self.assertEqual(
            _ts_call_graph._definition_query_names("csharp"),
            ("classes", "interfaces", "structs", "records", "methods", "properties"),
        )

    def test_qualified_symbol_queries_match_qualname(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = Graph(Path(tmp))
            graph._data = {
                "meta": {},
                "nodes": {
                    "symbol:csharp:opaque": {
                        "type": "symbol",
                        "label": "GetAsync",
                        "props": {
                            "qualname": "Sample.Api.Controllers.GetAsync",
                            "authority": "derived",
                            "confidence": "definite",
                        },
                    }
                },
                "edges": [],
            }
            graph._build_inverted_index()

            matches = graph.query("Controllers.GetAsync", limit=1)["matches"]

        self.assertEqual(matches[0]["id"], "symbol:csharp:opaque")


class FederatedExactSymbolQueryTest(unittest.TestCase):
    def test_json_federation_limit_one_prefers_exact_child_symbol(self) -> None:
        self._assert_child_symbol_top(with_sidecar=False)

    def test_sqlite_federation_limit_one_prefers_exact_child_symbol(self) -> None:
        self._assert_child_symbol_top(with_sidecar=True)

    def _assert_child_symbol_top(self, *, with_sidecar: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_exact_symbol_workspace(root, with_sidecar=with_sidecar)
            graph = FederatedGraph(root)
            try:
                top = graph.query("GetAsync", limit=1)["matches"][0]
                qualified = graph.query("OrdersController.GetAsync", limit=1)
            finally:
                graph.close()

        self.assertEqual(top["type"], "symbol")
        self.assertTrue(top["id"].startswith("api\x1fsymbol:csharp:"))
        self.assertEqual(
            qualified["matches"][0]["id"],
            "api\x1fsymbol:csharp:src.OrdersController:GetAsync",
        )


def _has_file_contains_edge(data: dict, symbol_id: str) -> bool:
    return any(
        edge.get("type") == "contains"
        and str(edge.get("from", "")).startswith("file:")
        and edge.get("to") == symbol_id
        for edge in data["edges"]
    )


def _make_exact_symbol_workspace(root: Path, *, with_sidecar: bool) -> None:
    child_root = _init_repo(root / "api")
    child_payload = _graph_payload(
        {
            "symbol:csharp:src.OrdersController:GetAsync": {
                "type": "symbol",
                "label": "GetAsync",
                "props": {
                    "file": "src/OrdersController.cs",
                    "qualname": "OrdersController.GetAsync",
                    "authority": "derived",
                    "confidence": "definite",
                },
            },
            "file:src/OrdersController.cs": {
                "type": "file",
                "label": "OrdersController",
                "props": {
                    "file": "src/OrdersController.cs",
                    "exports": ["GetAsync", "OrdersController"],
                },
            },
        },
        [
            {
                "from": "file:src/OrdersController.cs",
                "to": "symbol:csharp:src.OrdersController:GetAsync",
                "type": "contains",
                "props": {},
            }
        ],
    )
    _write_child_with_sidecar(child_root, child_payload, with_sidecar=with_sidecar)
    _write_workspaces(root, [ChildEntry(name="api", path="api")])
    _write_root_graph_with_weak_hits(root)


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _graph_payload(nodes: dict, edges: list[dict] | None = None) -> dict:
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": 1,
        },
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_child_with_sidecar(
    repo_root: Path, payload: dict, *, with_sidecar: bool,
) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    body = dumps_graph(payload).encode("utf-8")
    graph_path.write_bytes(body)
    if with_sidecar:
        sqlite_writer.build_sidecar_for_bytes(
            payload, body, weld_dir / "graph.db", generated_at="t",
        )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, weld_dir / "workspaces.yaml")


def _write_root_graph_with_weak_hits(root: Path) -> None:
    payload = {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": 2,
        },
        "nodes": {
            "repo:api": {"type": "repo", "label": "api", "props": {"path": "api"}},
            "file:docs/GetAsync.md": {
                "type": "file",
                "label": "GetAsync",
                "props": {"file": "docs/GetAsync.md", "exports": ["GetAsync"]},
            },
            "package:csharp:getasync": {
                "type": "package",
                "label": "GetAsync",
                "props": {"description": "GetAsync package reference"},
            },
        },
        "edges": [],
    }
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
