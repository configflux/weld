"""Tests for confidence discrimination across weld extraction strategies.

Verifies that:
- At least 3 strategies emit non-definite confidence values
- boundary_entrypoint emits inferred for entrypoints, definite for boundaries
- typescript_exports emits inferred (regex-based)
- markdown emits inferred for section-level role classification
- Higher-confidence results rank above lower-confidence ones in Graph.query()
- CONFIDENCE_RANK vocabulary matches CONFIDENCE_VALUES in contract
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph import Graph  # noqa: E402
from weld.ranking import CONFIDENCE_RANK  # noqa: E402

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph with the given nodes and edges."""
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    g._data = {
        "meta": {"version": 1, "updated_at": "2026-04-02T12:00:00+00:00"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g

# -- Strategy confidence emission tests ------------------------------------

class BoundaryEntrypointConfidenceTest(unittest.TestCase):
    """boundary_entrypoint strategy emits differentiated confidence."""

    def test_entrypoint_gets_inferred(self) -> None:
        from weld.strategies.boundary_entrypoint import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            svc = root / "svc"
            svc.mkdir()
            (svc / "main.py").write_text(
                "import uvicorn\n"
                "if __name__ == '__main__':\n"
                "    uvicorn.run()\n"
            )
            result = extract(root, {"glob": "svc/*.py"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                self.assertEqual(node["type"], "entrypoint")
                self.assertEqual(node["props"]["confidence"], "inferred")

    def test_boundary_keeps_definite(self) -> None:
        from weld.strategies.boundary_entrypoint import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            svc = root / "svc"
            svc.mkdir()
            (svc / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
            )
            result = extract(root, {"glob": "svc/*.py"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                self.assertEqual(node["type"], "boundary")
                self.assertEqual(node["props"]["confidence"], "definite")

    def test_edge_gets_inferred_when_linking_entrypoint(self) -> None:
        from weld.strategies.boundary_entrypoint import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            svc = root / "svc"
            svc.mkdir()
            (svc / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "if __name__ == '__main__':\n"
                "    import uvicorn\n"
                "    uvicorn.run(app)\n"
            )
            result = extract(root, {"glob": "svc/*.py"}, {})
            self.assertTrue(result.edges)
            for edge in result.edges:
                self.assertEqual(edge["props"]["confidence"], "inferred")

class TypescriptExportsConfidenceTest(unittest.TestCase):
    """typescript_exports strategy emits inferred confidence (regex-based)."""

    def test_nodes_get_inferred(self) -> None:
        from weld.strategies.typescript_exports import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            src.mkdir()
            (src / "utils.ts").write_text(
                "export function formatPrice(): string { return '0'; }\n"
            )
            result = extract(root, {"glob": "src/*.ts"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["confidence"], "inferred")

    def test_edges_get_inferred(self) -> None:
        from weld.strategies.typescript_exports import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            src.mkdir()
            (src / "widget.ts").write_text("export class Widget {}\n")
            result = extract(root, {"glob": "src/*.ts", "package": "pkg:web"}, {})
            self.assertTrue(result.edges)
            for edge in result.edges:
                self.assertEqual(edge["props"]["confidence"], "inferred")

class MarkdownSectionConfidenceTest(unittest.TestCase):
    """markdown strategy emits inferred for section-level nodes."""

    def test_section_nodes_inferred_doc_nodes_definite(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "guide.md").write_text(
                "# Guide\n\n"
                "## Installation\n\n"
                "Steps here.\n\n"
                "## Configuration\n\n"
                "Config here.\n"
            )
            result = extract(
                root, {"glob": "docs/*.md", "extract_sections": True}, {}
            )
            self.assertTrue(result.nodes)
            doc_count = 0
            section_count = 0
            for nid, node in result.nodes.items():
                if "#" in nid:
                    self.assertEqual(node["props"]["confidence"], "inferred")
                    section_count += 1
                else:
                    self.assertEqual(node["props"]["confidence"], "definite")
                    doc_count += 1
            self.assertGreaterEqual(doc_count, 1)
            self.assertGreaterEqual(section_count, 2)

# -- Ranking discrimination tests -----------------------------------------

class ConfidenceRankingDiscriminationTest(unittest.TestCase):
    """Higher-confidence results rank above lower-confidence in query."""

    def test_definite_before_inferred_same_authority(self) -> None:
        """Within same authority tier, definite confidence ranks higher."""
        nodes = {
            "file:ts-inferred": {
                "type": "file",
                "label": "widget",
                "props": {"authority": "derived", "confidence": "inferred",
                          "source_strategy": "typescript_exports"},
            },
            "file:py-definite": {
                "type": "file",
                "label": "widget",
                "props": {"authority": "derived", "confidence": "definite",
                          "source_strategy": "python_module"},
            },
        }
        g = _make_graph(nodes)
        result = g.query("widget")
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(ids[0], "file:py-definite")
        self.assertEqual(ids[1], "file:ts-inferred")

    def test_inferred_before_speculative(self) -> None:
        """Inferred confidence ranks above speculative."""
        nodes = {
            "entrypoint:spec": {
                "type": "entrypoint",
                "label": "runner",
                "props": {"authority": "canonical", "confidence": "speculative"},
            },
            "entrypoint:infer": {
                "type": "entrypoint",
                "label": "runner",
                "props": {"authority": "canonical", "confidence": "inferred",
                          "source_strategy": "boundary_entrypoint"},
            },
        }
        g = _make_graph(nodes)
        result = g.query("runner")
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(ids[0], "entrypoint:infer")
        self.assertEqual(ids[1], "entrypoint:spec")

    def test_three_confidence_levels_full_ordering(self) -> None:
        """All three confidence levels sort correctly within same authority."""
        nodes = {
            "doc:speculative": {
                "type": "doc",
                "label": "Guide Section",
                "props": {"authority": "derived", "confidence": "speculative"},
            },
            "doc:definite": {
                "type": "doc",
                "label": "Guide Section",
                "props": {"authority": "derived", "confidence": "definite"},
            },
            "doc:inferred": {
                "type": "doc",
                "label": "Guide Section",
                "props": {"authority": "derived", "confidence": "inferred",
                          "source_strategy": "markdown"},
            },
        }
        g = _make_graph(nodes)
        result = g.query("guide section")
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(ids, [
            "doc:definite",
            "doc:inferred",
            "doc:speculative",
        ])

class ConfidenceContractConsistencyTest(unittest.TestCase):
    """CONFIDENCE_RANK vocabulary matches CONFIDENCE_VALUES in contract."""

    def test_confidence_vocabulary_matches_contract(self) -> None:
        from weld.contract import CONFIDENCE_VALUES
        self.assertEqual(set(CONFIDENCE_RANK.keys()), CONFIDENCE_VALUES)

    def test_at_least_three_strategies_emit_non_definite(self) -> None:
        """Acceptance criterion: at least 3 strategies emit non-definite."""
        strategies_with_non_definite: list[str] = []

        # boundary_entrypoint: entrypoints get inferred
        from weld.strategies.boundary_entrypoint import extract as be_extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            svc = root / "svc"
            svc.mkdir()
            (svc / "main.py").write_text(
                "import click\n"
                "if __name__ == '__main__':\n"
                "    pass\n"
            )
            result = be_extract(root, {"glob": "svc/*.py"}, {})
            for node in result.nodes.values():
                if node["props"].get("confidence") != "definite":
                    strategies_with_non_definite.append("boundary_entrypoint")
                    break

        # typescript_exports: inferred
        from weld.strategies.typescript_exports import extract as ts_extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            src.mkdir()
            (src / "lib.ts").write_text("export function foo() {}\n")
            result = ts_extract(root, {"glob": "src/*.ts"}, {})
            for node in result.nodes.values():
                if node["props"].get("confidence") != "definite":
                    strategies_with_non_definite.append("typescript_exports")
                    break

        # markdown: sections get inferred
        from weld.strategies.markdown import extract as md_extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "g.md").write_text("# G\n\n## Setup\n\nContent.\n")
            result = md_extract(
                root, {"glob": "docs/*.md", "extract_sections": True}, {}
            )
            for node in result.nodes.values():
                if node["props"].get("confidence") != "definite":
                    strategies_with_non_definite.append("markdown")
                    break

        self.assertGreaterEqual(
            len(strategies_with_non_definite), 3,
            f"Only {len(strategies_with_non_definite)} strategies emit "
            f"non-definite: {strategies_with_non_definite}",
        )

if __name__ == "__main__":
    unittest.main()
