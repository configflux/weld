"""Tests for the wiki / agent-readable markdown export (ADR 0053).

Covers protocol presence, safe-id mapping, file layout, frontmatter,
wikilinks, edge attribution, determinism, incremental rebuild, and the
CLI dispatch path.
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

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.tests._wiki_export_fixtures import (  # noqa: E402
    FIXTURE_EDGES,
    FIXTURE_NODES,
    load_fixture_graph,
    load_id_map,
    make_graph_root,
)


class MultiFileExporterProtocolTest(unittest.TestCase):
    def test_protocol_exposed_from_export_module(self) -> None:
        from weld import export
        self.assertTrue(hasattr(export, "MultiFileExporter"))

    def test_existing_string_exporters_unchanged(self) -> None:
        from weld.export import to_mermaid, to_dot, to_d2
        graph = load_fixture_graph()
        self.assertIsInstance(to_mermaid(graph), str)
        self.assertIsInstance(to_dot(graph), str)
        self.assertIsInstance(to_d2(graph), str)


class SafeIdMappingTest(unittest.TestCase):
    def test_safe_id_is_deterministic(self) -> None:
        from weld._wiki_export import wiki_safe_id
        self.assertEqual(
            wiki_safe_id("symbol:weld.cli.main"),
            wiki_safe_id("symbol:weld.cli.main"),
        )

    def test_safe_id_escapes_illegal_chars(self) -> None:
        from weld._wiki_export import wiki_safe_id
        sid = wiki_safe_id("route:GET:/api/v1/stores")
        for bad in (":", "/", "\\", "?", "*", "|", "<", ">", '"'):
            self.assertNotIn(bad, sid)
        self.assertTrue(sid.isascii())

    def test_safe_id_collision_free_under_distinct_inputs(self) -> None:
        from weld._wiki_export import wiki_safe_id
        sid_a = wiki_safe_id("entity:Store")
        sid_b = wiki_safe_id("entity_Store")
        sid_c = wiki_safe_id("entity-Store")
        self.assertEqual(len({sid_a, sid_b, sid_c}), 3)

    def test_safe_id_carries_sha1_prefix(self) -> None:
        from weld._wiki_export import wiki_safe_id
        sid = wiki_safe_id("entity:Store")
        head = sid.split("-", 1)[0]
        self.assertEqual(len(head), 8)
        self.assertTrue(all(c in "0123456789abcdef" for c in head))


class WikiExportLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_graph_root()
        self.out = Path(tempfile.mkdtemp()) / "wiki"

    def _run(self) -> None:
        from weld.export import export
        export("wiki", output=self.out, root=self.root)

    def test_creates_top_level_index(self) -> None:
        self._run()
        self.assertTrue((self.out / "index.md").is_file())

    def test_creates_by_type_pages(self) -> None:
        self._run()
        by_type = self.out / "by-type"
        self.assertTrue(by_type.is_dir())
        names = {p.name for p in by_type.iterdir()}
        for expected in ("file.md", "symbol.md", "entity.md", "package.md"):
            self.assertIn(expected, names)

    def test_creates_by_community_pages(self) -> None:
        self._run()
        by_comm = self.out / "by-community"
        self.assertTrue(by_comm.is_dir())
        self.assertGreater(len(list(by_comm.iterdir())), 0)

    def test_creates_node_pages(self) -> None:
        self._run()
        nodes_dir = self.out / "nodes"
        self.assertTrue(nodes_dir.is_dir())
        self.assertEqual(len(list(nodes_dir.iterdir())), 5)

    def test_writes_id_map(self) -> None:
        self._run()
        id_map_path = self.out / ".id-map.json"
        self.assertTrue(id_map_path.is_file())
        payload = json.loads(id_map_path.read_text(encoding="utf-8"))
        self.assertIn("ids", payload)
        self.assertIn("entity:Store", payload["ids"])


class NodePageContentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_graph_root()
        self.out = Path(tempfile.mkdtemp()) / "wiki"
        from weld.export import export
        export("wiki", output=self.out, root=self.root)
        self.id_map = load_id_map(self.out)

    def _page(self, node_id: str) -> str:
        sid = self.id_map[node_id]
        return (self.out / "nodes" / f"{sid}.md").read_text(encoding="utf-8")

    def test_frontmatter_contains_required_fields(self) -> None:
        body = self._page("file:weld/cli.py")
        self.assertTrue(body.startswith("---\n"))
        head, _, _rest = body[4:].partition("\n---\n")
        for needle in (
            "id: file:weld/cli.py", "type: file", "origin: project", "confidence:",
        ):
            self.assertIn(needle, head)

    def test_renders_description(self) -> None:
        self.assertIn("CLI dispatcher for wd.", self._page("file:weld/cli.py"))

    def test_outgoing_edges_section_present(self) -> None:
        body = self._page("file:weld/cli.py")
        self.assertIn("Outgoing edges", body)
        self.assertIn("[[symbol:weld.cli.main]]", body)

    def test_incoming_edges_section_present(self) -> None:
        body = self._page("symbol:weld.cli.main")
        self.assertIn("Incoming edges", body)
        self.assertIn("[[file:weld/cli.py]]", body)

    def test_edge_confidence_rendered_inline(self) -> None:
        body = self._page("file:weld/cli.py")
        self.assertIn("definite", body)
        self.assertIn("python_ast", body)

    def test_speculative_edge_rendered(self) -> None:
        body = self._page("symbol:weld.cli.main")
        self.assertIn("speculative", body)
        self.assertIn("anthropic_enrichment", body)


class ByTypeAndIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_graph_root()
        self.out = Path(tempfile.mkdtemp()) / "wiki"
        from weld.export import export
        export("wiki", output=self.out, root=self.root)

    def test_by_type_page_lists_member_nodes_alphabetically(self) -> None:
        page = (self.out / "by-type" / "entity.md").read_text(encoding="utf-8")
        self.assertIn("[[entity:Offer]]", page)
        self.assertIn("[[entity:Store]]", page)
        self.assertLess(page.index("entity:Offer"), page.index("entity:Store"))

    def test_index_lists_type_counts(self) -> None:
        idx = (self.out / "index.md").read_text(encoding="utf-8")
        for t in ("file", "symbol", "entity", "package"):
            self.assertIn(t, idx)

    def test_index_links_to_community_pages(self) -> None:
        idx = (self.out / "index.md").read_text(encoding="utf-8")
        self.assertIn("by-community", idx)


class DeterminismTest(unittest.TestCase):
    def test_two_exports_byte_identical(self) -> None:
        from weld.export import export
        root_a = make_graph_root()
        out_a = Path(tempfile.mkdtemp()) / "wiki_a"
        out_b = Path(tempfile.mkdtemp()) / "wiki_b"
        export("wiki", output=out_a, root=root_a)
        export("wiki", output=out_b, root=root_a)
        files_a = sorted(p for p in out_a.rglob("*") if p.is_file())
        files_b = sorted(p for p in out_b.rglob("*") if p.is_file())
        self.assertEqual(
            [p.relative_to(out_a) for p in files_a],
            [p.relative_to(out_b) for p in files_b],
        )
        for fa, fb in zip(files_a, files_b):
            self.assertEqual(fa.read_bytes(), fb.read_bytes(), f"{fa.name}")

    def test_edges_sorted_by_type_then_target(self) -> None:
        from weld.export import export
        nodes = {
            "n:a": {"type": "node", "label": "a", "props": {"origin": "project"}},
            "n:b": {"type": "node", "label": "b", "props": {"origin": "project"}},
            "n:c": {"type": "node", "label": "c", "props": {"origin": "project"}},
        }
        edges = [
            {"from": "n:a", "to": "n:c", "type": "calls",
             "props": {"confidence": "definite", "source_strategy": "s"}},
            {"from": "n:a", "to": "n:b", "type": "calls",
             "props": {"confidence": "definite", "source_strategy": "s"}},
            {"from": "n:a", "to": "n:b", "type": "contains",
             "props": {"confidence": "definite", "source_strategy": "s"}},
        ]
        root = make_graph_root(nodes=nodes, edges=edges)
        out = Path(tempfile.mkdtemp()) / "wiki"
        export("wiki", output=out, root=root)
        id_map = load_id_map(out)
        page = (out / "nodes" / f"{id_map['n:a']}.md").read_text(encoding="utf-8")
        i_b = page.index("calls -> [[n:b]]")
        i_c = page.index("calls -> [[n:c]]")
        i_contains = page.index("contains -> [[n:b]]")
        self.assertLess(i_b, i_c)
        self.assertLess(i_c, i_contains)


class IncrementalRebuildTest(unittest.TestCase):
    def test_unchanged_files_not_rewritten(self) -> None:
        # Stamp every node page with a sentinel suffix; if the
        # incremental path correctly skips unchanged pages, the sentinel
        # survives the second export.
        from weld.export import export
        root = make_graph_root()
        out = Path(tempfile.mkdtemp()) / "wiki"
        export("wiki", output=out, root=root)
        sentinel = b"\n<!-- sentinel: unchanged -->\n"
        for p in (out / "nodes").iterdir():
            with p.open("ab") as fh:
                fh.write(sentinel)
        export("wiki", output=out, root=root)
        for p in (out / "nodes").iterdir():
            self.assertTrue(
                p.read_bytes().endswith(sentinel),
                f"page {p.name} was rewritten on incremental export",
            )

    def test_changed_node_rewrites_only_that_page(self) -> None:
        # Mutate one node; verify the mutated page changes and every
        # other page is left intact (via sentinel-survival check).
        from weld.export import export
        root = make_graph_root()
        out = Path(tempfile.mkdtemp()) / "wiki"
        export("wiki", output=out, root=root)
        id_map = load_id_map(out)
        changed_name = f"{id_map['file:weld/cli.py']}.md"
        sentinel = b"\n<!-- sentinel: unchanged -->\n"
        for p in (out / "nodes").iterdir():
            if p.name == changed_name:
                continue
            with p.open("ab") as fh:
                fh.write(sentinel)

        mutated = {
            **FIXTURE_NODES,
            "file:weld/cli.py": {
                **FIXTURE_NODES["file:weld/cli.py"],
                "props": {
                    **FIXTURE_NODES["file:weld/cli.py"]["props"],
                    "description": "Updated description.",
                },
            },
        }
        (root / ".weld" / "graph.json").write_text(
            json.dumps({
                "meta": {
                    "version": SCHEMA_VERSION, "git_sha": "deadbeef",
                    "updated_at": "2026-05-10T00:00:00+00:00",
                },
                "nodes": mutated, "edges": FIXTURE_EDGES,
            }),
            encoding="utf-8",
        )
        export("wiki", output=out, root=root)
        # Mutated page must reflect the new description; sentinel must
        # be absent from it (proving it was rewritten).
        changed_body = (out / "nodes" / changed_name).read_bytes()
        self.assertIn(b"Updated description.", changed_body)
        self.assertNotIn(sentinel, changed_body)
        # Every other page must still carry the sentinel.
        for p in (out / "nodes").iterdir():
            if p.name == changed_name:
                continue
            self.assertTrue(
                p.read_bytes().endswith(sentinel),
                f"unchanged page {p.name} was rewritten",
            )


class CliDispatchTest(unittest.TestCase):
    def test_cli_accepts_wiki_format_with_output(self) -> None:
        from weld._export_cli import run_export
        root = make_graph_root()
        out = Path(tempfile.mkdtemp()) / "wiki"
        rc = run_export([
            "--format=wiki", f"--root={root}", f"--output={out}",
        ])
        self.assertEqual(rc, 0)
        self.assertTrue((out / "index.md").is_file())

    def test_cli_rejects_wiki_without_output(self) -> None:
        from weld._export_cli import run_export
        root = make_graph_root()
        with self.assertRaises(SystemExit):
            run_export(["--format=wiki", f"--root={root}"])

    def test_cli_format_choices_include_wiki(self) -> None:
        from weld._export_cli import run_export
        root = make_graph_root()
        self.assertEqual(run_export(["--format=mermaid", f"--root={root}"]), 0)


if __name__ == "__main__":
    unittest.main()
