"""Tests for ``weld.export`` -- graph visualization export (Mermaid, DOT, D2)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402

_FIXTURE_NODES: dict[str, dict] = {
    "entity:Store": {"type": "entity", "label": "Store",
        "props": {"file": "domain/store.py", "exports": ["Store"]}},
    "entity:Offer": {"type": "entity", "label": "Offer",
        "props": {"file": "domain/offer.py", "exports": ["Offer"]}},
    "route:GET:/api/v1/stores": {"type": "route", "label": "list_stores",
        "props": {"file": "routes/stores.py", "exports": ["list_stores"]}},
    "doc:adr/0015-weld-mcp": {"type": "doc", "label": "ADR 0015 Weld MCP",
        "props": {"file": "docs/adrs/0015.md", "doc_kind": "adr"}},
}
_FIXTURE_EDGES: list[dict] = [
    {"from": "entity:Offer", "to": "entity:Store", "type": "depends_on", "props": {}},
    {"from": "route:GET:/api/v1/stores", "to": "entity:Store",
     "type": "responds_with", "props": {}},
]

def _make_graph_root() -> Path:
    tmp = Path(tempfile.mkdtemp())
    (tmp / ".weld").mkdir(parents=True, exist_ok=True)
    (tmp / ".weld" / "graph.json").write_text(json.dumps({
        "meta": {"version": SCHEMA_VERSION, "git_sha": "deadbeef",
                 "updated_at": "2026-04-06T00:00:00+00:00"},
        "nodes": _FIXTURE_NODES, "edges": _FIXTURE_EDGES,
    }), encoding="utf-8")
    return tmp

def _load_fixture_graph() -> Graph:
    g = Graph(_make_graph_root())
    g.load()
    return g

class MermaidExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _load_fixture_graph()

    def test_full_graph_starts_with_flowchart_header(self) -> None:
        from weld.export import to_mermaid
        self.assertTrue(to_mermaid(self.graph).startswith("flowchart LR"))

    def test_full_graph_contains_all_nodes(self) -> None:
        from weld.export import to_mermaid
        output = to_mermaid(self.graph)
        for nid in _FIXTURE_NODES:
            self.assertIn(_safe_id(nid), output)

    def test_full_graph_contains_edges(self) -> None:
        from weld.export import to_mermaid
        output = to_mermaid(self.graph)
        self.assertIn("depends_on", output)
        self.assertIn("responds_with", output)

    def test_node_labels_present(self) -> None:
        from weld.export import to_mermaid
        output = to_mermaid(self.graph)
        self.assertIn("Store", output)
        self.assertIn("Offer", output)

    def test_returns_string(self) -> None:
        from weld.export import to_mermaid
        self.assertIsInstance(to_mermaid(self.graph), str)

class DotExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _load_fixture_graph()

    def test_full_graph_starts_with_digraph(self) -> None:
        from weld.export import to_dot
        self.assertTrue(to_dot(self.graph).strip().startswith("digraph"))

    def test_full_graph_contains_all_nodes(self) -> None:
        from weld.export import to_dot
        output = to_dot(self.graph)
        for nid in _FIXTURE_NODES:
            self.assertIn(_safe_id(nid), output)

    def test_full_graph_contains_edges(self) -> None:
        from weld.export import to_dot
        output = to_dot(self.graph)
        self.assertIn("->", output)
        self.assertIn("depends_on", output)

    def test_dot_ends_with_closing_brace(self) -> None:
        from weld.export import to_dot
        self.assertTrue(to_dot(self.graph).strip().endswith("}"))

class D2ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _load_fixture_graph()

    def test_full_graph_contains_node_definitions(self) -> None:
        from weld.export import to_d2
        output = to_d2(self.graph)
        self.assertIn("Store", output)
        self.assertIn("Offer", output)

    def test_full_graph_contains_edges(self) -> None:
        from weld.export import to_d2
        output = to_d2(self.graph)
        self.assertIn("->", output)
        self.assertIn("depends_on", output)

    def test_returns_string(self) -> None:
        from weld.export import to_d2
        self.assertIsInstance(to_d2(self.graph), str)


class SubgraphExtractionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _load_fixture_graph()

    def test_subgraph_depth_1_returns_immediate_neighbors(self) -> None:
        from weld.export import extract_subgraph
        nodes, edges = extract_subgraph(self.graph, "entity:Store", depth=1)
        node_ids = set(nodes.keys())
        self.assertIn("entity:Store", node_ids)
        self.assertIn("entity:Offer", node_ids)
        self.assertIn("route:GET:/api/v1/stores", node_ids)
        self.assertNotIn("doc:adr/0015-weld-mcp", node_ids)

    def test_subgraph_depth_0_returns_only_root(self) -> None:
        from weld.export import extract_subgraph
        nodes, edges = extract_subgraph(self.graph, "entity:Store", depth=0)
        self.assertEqual(set(nodes.keys()), {"entity:Store"})
        self.assertEqual(edges, [])

    def test_subgraph_nonexistent_node_returns_empty(self) -> None:
        from weld.export import extract_subgraph
        nodes, edges = extract_subgraph(self.graph, "entity:DoesNotExist", depth=2)
        self.assertEqual(nodes, {})
        self.assertEqual(edges, [])

    def test_subgraph_with_mermaid_format(self) -> None:
        from weld.export import to_mermaid, extract_subgraph
        nodes, edges = extract_subgraph(self.graph, "entity:Store", depth=1)
        output = to_mermaid(self.graph, nodes=nodes, edges=edges)
        self.assertIn(_safe_id("entity:Store"), output)
        self.assertNotIn(_safe_id("doc:adr/0015-weld-mcp"), output)

    def test_subgraph_depth_2_reaches_transitive(self) -> None:
        from weld.export import extract_subgraph
        nodes, _ = extract_subgraph(self.graph, "route:GET:/api/v1/stores", depth=2)
        node_ids = set(nodes.keys())
        self.assertIn("route:GET:/api/v1/stores", node_ids)
        self.assertIn("entity:Store", node_ids)
        self.assertIn("entity:Offer", node_ids)

class ExportDispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_graph_root()

    def test_export_mermaid(self) -> None:
        from weld.export import export
        self.assertIn("flowchart LR", export("mermaid", root=self.root))

    def test_export_dot(self) -> None:
        from weld.export import export
        self.assertIn("digraph", export("dot", root=self.root))

    def test_export_d2(self) -> None:
        from weld.export import export
        self.assertIn("->", export("d2", root=self.root))

    def test_export_with_node_and_depth(self) -> None:
        from weld.export import export
        result = export("mermaid", node_id="entity:Store", depth=1, root=self.root)
        self.assertIn(_safe_id("entity:Store"), result)
        self.assertNotIn(_safe_id("doc:adr/0015-weld-mcp"), result)

    def test_export_unknown_format_raises(self) -> None:
        from weld.export import export
        with self.assertRaises(ValueError):
            export("svg", root=self.root)


class ExportPreloadedGraphTest(unittest.TestCase):
    """bd 6vq7: a caller-supplied ``graph=`` must not trigger a second
    ``graph.json`` read+parse, and must produce byte-identical output to
    the ``root=``-only path it replaces (ADR 0083 parity)."""

    def setUp(self) -> None:
        self.root = _make_graph_root()

    def test_root_only_still_loads_exactly_once(self) -> None:
        # Pins the unchanged default: no graph= means export() constructs
        # and loads its own Graph, same as before this fix.
        from weld.export import export
        import weld.graph as graph_mod

        with patch(
            "weld.graph.load_graph_file", wraps=graph_mod.load_graph_file,
        ) as mock_load:
            export("mermaid", root=self.root)
        mock_load.assert_called_once()

    def test_graph_param_never_touches_disk_for_the_graph_body(self) -> None:
        # The core regression proof: once a Graph is already loaded, handing
        # it to export() via graph= must not read graph.json again.
        from weld.export import export
        import weld.graph as graph_mod

        g = Graph(self.root)
        g.load()  # simulates the CLI's own load_graph_or_exit() probe load

        with patch(
            "weld.graph.load_graph_file", wraps=graph_mod.load_graph_file,
        ) as mock_load:
            export("mermaid", root=self.root, graph=g)
        mock_load.assert_not_called()

    def test_graph_param_wiki_format_never_touches_disk_either(self) -> None:
        # The multi-file branch takes the same graph= shortcut: the CLI
        # calls load_graph_or_exit() before every --format, wiki included.
        from weld.export import export
        import weld.graph as graph_mod

        g = Graph(self.root)
        g.load()
        out_dir = self.root / "wiki-out"

        with patch(
            "weld.graph.load_graph_file", wraps=graph_mod.load_graph_file,
        ) as mock_load:
            export("wiki", root=self.root, output=out_dir, graph=g)
        mock_load.assert_not_called()
        self.assertTrue(out_dir.exists())

    def test_graph_param_output_matches_root_param(self) -> None:
        from weld.export import export

        for fmt in ("mermaid", "dot", "d2"):
            with self.subTest(fmt=fmt):
                via_root = export(fmt, root=self.root)
                g = Graph(self.root)
                g.load()
                via_graph = export(fmt, root=self.root, graph=g)
                self.assertEqual(via_root, via_graph)

    def test_graph_param_with_node_and_depth_matches(self) -> None:
        from weld.export import export

        via_root = export(
            "mermaid", node_id="entity:Store", depth=1, root=self.root,
        )
        g = Graph(self.root)
        g.load()
        via_graph = export(
            "mermaid", node_id="entity:Store", depth=1, root=self.root, graph=g,
        )
        self.assertEqual(via_root, via_graph)

    def test_graph_param_wiki_output_matches_root_param(self) -> None:
        from weld.export import export

        root_out = self.root / "wiki-via-root"
        export("wiki", root=self.root, output=root_out)

        g = Graph(self.root)
        g.load()
        graph_out = self.root / "wiki-via-graph"
        export("wiki", root=self.root, output=graph_out, graph=g)

        root_files = sorted(p.relative_to(root_out) for p in root_out.rglob("*") if p.is_file())
        graph_files = sorted(p.relative_to(graph_out) for p in graph_out.rglob("*") if p.is_file())
        self.assertEqual(root_files, graph_files)
        for rel in root_files:
            self.assertEqual(
                (root_out / rel).read_bytes(), (graph_out / rel).read_bytes(),
            )


class McpExportToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_graph_root()

    def test_mcp_tool_registered(self) -> None:
        from weld import mcp_server
        names = {t.name for t in mcp_server.build_tools()}
        self.assertIn("weld_export", names)

    def test_mcp_dispatch_mermaid(self) -> None:
        from weld import mcp_server
        result = mcp_server.dispatch("weld_export", {"format": "mermaid"}, root=self.root)
        self.assertIn("output", result)
        self.assertIn("flowchart LR", result["output"])
        self.assertEqual(result["format"], "mermaid")

    def test_mcp_dispatch_with_node(self) -> None:
        from weld import mcp_server
        result = mcp_server.dispatch(
            "weld_export", {"format": "dot", "node_id": "entity:Store", "depth": 1},
            root=self.root)
        self.assertIn("output", result)
        self.assertIn("digraph", result["output"])

    def test_mcp_dispatch_invalid_format(self) -> None:
        from weld import mcp_server
        result = mcp_server.dispatch("weld_export", {"format": "invalid"}, root=self.root)
        self.assertIn("error", result)

    def test_mcp_tool_schema(self) -> None:
        from weld import mcp_server
        by_name = {t.name: t for t in mcp_server.build_tools()}
        schema = by_name["weld_export"].input_schema
        self.assertEqual(schema["required"], ["format"])
        for key in ("format", "node_id", "depth"):
            self.assertIn(key, schema["properties"])

class CliExportTest(unittest.TestCase):
    def test_export_in_help_text(self) -> None:
        from weld.cli import _HELP
        self.assertIn("export", _HELP)

def _safe_id(node_id: str) -> str:
    return node_id.replace(":", "_").replace("/", "_").replace("-", "_")

if __name__ == "__main__":
    unittest.main()
