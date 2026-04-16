"""Tests for normalized metadata on edges, topology overlays, and contract validation.

Verifies that:
- Edge props from strategies include ``source_strategy`` and ``confidence``
- Topology overlay nodes and edges carry manual provenance metadata
- No strategy fabricates metadata it cannot justify (no fake completeness)
- All strategy-produced nodes pass contract validation
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_node  # noqa: E402

# ---------------------------------------------------------------------------
# Edge metadata tests
# ---------------------------------------------------------------------------

class EdgeMetadataTest(unittest.TestCase):
    """Edges produced by strategies should carry source_strategy and confidence."""

    def test_fastapi_response_edge_metadata(self) -> None:
        from weld.strategies.fastapi import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "routers"
            pkg.mkdir()
            (pkg / "health.py").write_text(textwrap.dedent("""\
                from fastapi import APIRouter
                router = APIRouter(prefix="/health", tags=["health"])
                @router.get("/", response_model=HealthResponse)
                def health_check():
                    return {"ok": True}
            """))
            result = extract(root, {"glob": "routers/*.py"}, {})
            self.assertTrue(result.edges, "should produce at least one edge")
            for edge in result.edges:
                self.assertIn("source_strategy", edge["props"])
                self.assertEqual(edge["props"]["source_strategy"], "fastapi")
                self.assertIn("confidence", edge["props"])

    def test_dockerfile_builds_edge_metadata(self) -> None:
        from weld.strategies.dockerfile import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docker = root / "docker"
            docker.mkdir()
            (docker / "api.Dockerfile").write_text("FROM python:3.12\nCMD [\"python\"]")
            result = extract(root, {"glob": "docker/*.Dockerfile"}, {})
            for edge in result.edges:
                self.assertIn("source_strategy", edge["props"])
                self.assertEqual(edge["props"]["source_strategy"], "dockerfile")
                self.assertIn("confidence", edge["props"])

    def test_compose_orchestrates_edge_metadata(self) -> None:
        from weld.strategies.compose import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docker-compose.yml").write_text(
                "services:\n  api:\n    build: .\n"
            )
            result = extract(root, {"glob": "docker-compose*.yml"}, {})
            for edge in result.edges:
                self.assertIn("source_strategy", edge["props"])
                self.assertEqual(edge["props"]["source_strategy"], "compose")
                self.assertIn("confidence", edge["props"])

# ---------------------------------------------------------------------------
# Topology overlay metadata tests
# ---------------------------------------------------------------------------

class TopologyOverlayMetadataTest(unittest.TestCase):
    """Topology overlay nodes and edges should carry manual provenance metadata."""

    def test_topology_nodes_have_metadata(self) -> None:
        from weld.discover import discover
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(textwrap.dedent("""\
                sources: []
                topology:
                  nodes:
                    - id: "service:api"
                      type: service
                      label: API
                      props: {}
                  edges: []
            """))
            graph = discover(root)
            node = graph["nodes"].get("service:api")
            self.assertIsNotNone(node, "topology node should exist")
            props = node["props"]
            self.assertEqual(props.get("source_strategy"), "topology")
            self.assertEqual(props.get("authority"), "manual")
            self.assertEqual(props.get("confidence"), "definite")

    def test_topology_edges_have_metadata(self) -> None:
        from weld.discover import discover
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(textwrap.dedent("""\
                sources: []
                topology:
                  nodes:
                    - id: "service:api"
                      type: service
                      label: API
                      props: {}
                    - id: "package:domain"
                      type: package
                      label: Domain
                      props: {}
                  edges:
                    - from: "service:api"
                      to: "package:domain"
                      type: depends_on
            """))
            graph = discover(root)
            self.assertTrue(graph["edges"], "should have at least one edge")
            for edge in graph["edges"]:
                self.assertIn("source_strategy", edge["props"])
                self.assertEqual(edge["props"]["source_strategy"], "topology")
                self.assertIn("confidence", edge["props"])
                self.assertEqual(edge["props"]["confidence"], "definite")

    def test_entity_package_containment_edges_have_metadata(self) -> None:
        """Entity-package containment edges from topology should carry metadata."""
        from weld.discover import discover
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            domain = root / "domain"
            domain.mkdir()
            (domain / "user.py").write_text(textwrap.dedent("""\
                from .base import Base
                class AppUser(Base):
                    __tablename__ = "app_users"
            """))
            (weld_dir / "discover.yaml").write_text(textwrap.dedent("""\
                sources:
                  - glob: "domain/*.py"
                    type: entity
                    strategy: sqlalchemy
                topology:
                  nodes:
                    - id: "package:domain"
                      type: package
                      label: Domain
                      props: {}
                  edges: []
                  entity_packages:
                    - package: "package:domain"
                      modules: [user]
            """))
            graph = discover(root)
            containment_edges = [
                e for e in graph["edges"] if e["type"] == "contains"
            ]
            self.assertTrue(containment_edges, "should have containment edges")
            for edge in containment_edges:
                self.assertIn("source_strategy", edge["props"])
                self.assertEqual(edge["props"]["source_strategy"], "topology")
                self.assertIn("confidence", edge["props"])

# ---------------------------------------------------------------------------
# No-fake-completeness tests
# ---------------------------------------------------------------------------

class NoFakeCompletenessTest(unittest.TestCase):
    """Strategies must not fabricate metadata they cannot justify."""

    def test_python_module_no_fake_authority(self) -> None:
        """python_module derives from file listing, not from source of truth --
        so authority should be 'derived', not 'canonical'."""
        from weld.strategies.python_module import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            src.mkdir()
            (src / "utils.py").write_text("def helper(): pass\n")
            result = extract(root, {"glob": "src/*.py"}, {})
            for nid, node in result.nodes.items():
                self.assertEqual(
                    node["props"]["authority"], "derived",
                    "python_module should use 'derived' authority, not 'canonical'",
                )

    def test_typescript_no_fake_authority(self) -> None:
        from weld.strategies.typescript_exports import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            src.mkdir()
            (src / "lib.ts").write_text("export function foo() {}")
            result = extract(root, {"glob": "src/*.ts"}, {})
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["authority"], "derived")

# ---------------------------------------------------------------------------
# Contract validation integration test
# ---------------------------------------------------------------------------

class ContractValidationIntegrationTest(unittest.TestCase):
    """All strategy-produced nodes and edges with metadata must pass contract validation."""

    def test_sqlalchemy_nodes_pass_validation(self) -> None:
        from weld.strategies.sqlalchemy import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "domain"
            pkg.mkdir()
            (pkg / "user.py").write_text(textwrap.dedent("""\
                import sqlalchemy as sa
                from .base import Base
                class AppUser(Base):
                    __tablename__ = "app_users"
                    id = sa.Column(sa.Integer, primary_key=True)
            """))
            result = extract(root, {"glob": "domain/*.py"}, {})
            for nid, node in result.nodes.items():
                errors = validate_node(nid, node)
                self.assertEqual(errors, [], f"validation errors for {nid}: {errors}")

    def test_topology_graph_passes_full_validation(self) -> None:
        from weld.discover import discover
        from weld.contract import validate_graph
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(textwrap.dedent("""\
                sources: []
                topology:
                  nodes:
                    - id: "service:api"
                      type: service
                      label: API
                      props: {}
                    - id: "package:domain"
                      type: package
                      label: Domain
                      props: {}
                  edges:
                    - from: "service:api"
                      to: "package:domain"
                      type: depends_on
            """))
            graph = discover(root)
            errors = validate_graph(graph)
            self.assertEqual(errors, [], f"validation errors: {errors}")

if __name__ == "__main__":
    unittest.main()
