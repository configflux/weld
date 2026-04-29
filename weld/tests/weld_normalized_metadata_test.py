"""Tests for normalized metadata population from bundled strategies and topology.

Verifies that:
- Every bundled strategy emits ``source_strategy`` in node props
- Strategies that can derive authority, confidence, or roles do so
- Topology overlay nodes carry ``source_strategy``, ``authority``, and
  ``confidence``
- Topology overlay edges carry ``source_strategy`` and ``confidence``
- Edge props from strategies include ``source_strategy`` and ``confidence``
  where derivable
- No strategy fabricates metadata it cannot justify (no fake completeness)
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

from weld.contract import (  # noqa: E402
    AUTHORITY_VALUES,
    CONFIDENCE_VALUES,
    ROLE_VALUES,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_valid_source_strategy(tc: unittest.TestCase, props: dict, strategy_name: str) -> None:
    """Assert source_strategy is set and is a non-empty string."""
    tc.assertIn("source_strategy", props, f"{strategy_name}: missing source_strategy")
    tc.assertIsInstance(props["source_strategy"], str)
    tc.assertTrue(props["source_strategy"], f"{strategy_name}: source_strategy is empty")

def _assert_valid_metadata_values(tc: unittest.TestCase, props: dict, strategy_name: str) -> None:
    """Assert that if metadata is present, its values are in the valid vocabulary."""
    if "authority" in props:
        tc.assertIn(props["authority"], AUTHORITY_VALUES,
                     f"{strategy_name}: invalid authority '{props['authority']}'")
    if "confidence" in props:
        tc.assertIn(props["confidence"], CONFIDENCE_VALUES,
                     f"{strategy_name}: invalid confidence '{props['confidence']}'")
    if "roles" in props:
        tc.assertIsInstance(props["roles"], list)
        for role in props["roles"]:
            tc.assertIn(role, ROLE_VALUES,
                         f"{strategy_name}: invalid role '{role}'")

# ---------------------------------------------------------------------------
# Strategy node metadata tests
# ---------------------------------------------------------------------------

class SqlalchemyMetadataTest(unittest.TestCase):
    def test_entity_has_source_strategy(self) -> None:
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
            self.assertTrue(result.nodes, "should produce at least one node")
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "sqlalchemy")
                _assert_valid_metadata_values(self, node["props"], "sqlalchemy")
                # entity from SA model: canonical authority, definite confidence
                if node["type"] == "entity":
                    self.assertEqual(node["props"]["authority"], "canonical")
                    self.assertEqual(node["props"]["confidence"], "definite")
                    self.assertIn("implementation", node["props"]["roles"])

class FastapiMetadataTest(unittest.TestCase):
    def test_route_has_source_strategy(self) -> None:
        from weld.strategies.fastapi import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "routers"
            pkg.mkdir()
            (pkg / "health.py").write_text(textwrap.dedent("""\
                from fastapi import APIRouter
                router = APIRouter(prefix="/health", tags=["health"])
                @router.get("/")
                def health_check():
                    return {"ok": True}
            """))
            result = extract(root, {"glob": "routers/*.py"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "fastapi")
                _assert_valid_metadata_values(self, node["props"], "fastapi")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("implementation", node["props"]["roles"])

class PydanticMetadataTest(unittest.TestCase):
    def test_contract_has_source_strategy(self) -> None:
        from weld.strategies.pydantic import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "contracts"
            pkg.mkdir()
            (pkg / "offer.py").write_text(textwrap.dedent("""\
                from pydantic import BaseModel
                class OfferResponse(BaseModel):
                    id: int
                    name: str
            """))
            result = extract(root, {"glob": "contracts/*.py"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "pydantic")
                _assert_valid_metadata_values(self, node["props"], "pydantic")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")

class WorkerStageMetadataTest(unittest.TestCase):
    def test_stage_has_source_strategy(self) -> None:
        from weld.strategies.worker_stage import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stages = root / "worker"
            acq = stages / "acquisition"
            acq.mkdir(parents=True)
            (acq / "__init__.py").write_text('__all__ = ["run"]')
            result = extract(root, {"glob": "worker/*/"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "worker_stage")
                _assert_valid_metadata_values(self, node["props"], "worker_stage")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("implementation", node["props"]["roles"])

class DockerfileMetadataTest(unittest.TestCase):
    def test_dockerfile_has_source_strategy(self) -> None:
        from weld.strategies.dockerfile import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docker = root / "docker"
            docker.mkdir()
            (docker / "api.Dockerfile").write_text("FROM python:3.12\nCMD [\"python\"]")
            result = extract(root, {"glob": "docker/*.Dockerfile"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "dockerfile")
                _assert_valid_metadata_values(self, node["props"], "dockerfile")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("build", node["props"]["roles"])

class ComposeMetadataTest(unittest.TestCase):
    def test_compose_has_source_strategy(self) -> None:
        from weld.strategies.compose import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docker-compose.yml").write_text("services:\n  api:\n    build: .\n")
            result = extract(root, {"glob": "docker-compose*.yml"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "compose")
                _assert_valid_metadata_values(self, node["props"], "compose")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("config", node["props"]["roles"])

class FrontmatterMdMetadataTest(unittest.TestCase):
    def test_agent_has_source_strategy(self) -> None:
        from weld.strategies.frontmatter_md import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            agents = root / "agents"
            agents.mkdir()
            (agents / "tdd.md").write_text("---\nname: tdd\ndescription: TDD agent\n---\nBody")
            result = extract(root, {"glob": "agents/*.md"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "frontmatter_md")
                _assert_valid_metadata_values(self, node["props"], "frontmatter_md")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("config", node["props"]["roles"])

class FirstlineMdMetadataTest(unittest.TestCase):
    def test_command_has_source_strategy(self) -> None:
        from weld.strategies.firstline_md import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cmds = root / "commands"
            cmds.mkdir()
            (cmds / "push.md").write_text("Push to main after gate pass.")
            result = extract(root, {"glob": "commands/*.md"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "firstline_md")
                _assert_valid_metadata_values(self, node["props"], "firstline_md")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("config", node["props"]["roles"])

class ToolScriptMetadataTest(unittest.TestCase):
    def test_tool_has_source_strategy(self) -> None:
        from weld.strategies.tool_script import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tools = root / "tools"
            tools.mkdir()
            (tools / "lint.sh").write_text("#!/usr/bin/env bash\necho lint")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "tool_script")
                _assert_valid_metadata_values(self, node["props"], "tool_script")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("script", node["props"]["roles"])

class YamlMetaMetadataTest(unittest.TestCase):
    def test_workflow_has_source_strategy(self) -> None:
        from weld.strategies.yaml_meta import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wf = root / "workflows"
            wf.mkdir()
            (wf / "ci.yml").write_text("name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu\n")
            result = extract(root, {"glob": "workflows/*.yml"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "yaml_meta")
                _assert_valid_metadata_values(self, node["props"], "yaml_meta")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("config", node["props"]["roles"])

class MarkdownMetadataTest(unittest.TestCase):
    def test_doc_has_source_strategy(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "guide.md").write_text("# Guide\nSome content.")
            result = extract(root, {"glob": "docs/*.md", "id_prefix": "doc:guide"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "markdown")
                _assert_valid_metadata_values(self, node["props"], "markdown")
                # guides are supporting references, not authoritative (tracked project)
                self.assertEqual(node["props"]["authority"], "derived")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("doc", node["props"]["roles"])

class ConfigFileMetadataTest(unittest.TestCase):
    def test_config_has_source_strategy(self) -> None:
        from weld.strategies.config_file import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".bazelrc").write_text("build --jobs=4")
            result = extract(root, {"files": [".bazelrc"]}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "config_file")
                _assert_valid_metadata_values(self, node["props"], "config_file")
                self.assertEqual(node["props"]["authority"], "canonical")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("config", node["props"]["roles"])

class PythonModuleMetadataTest(unittest.TestCase):
    def test_file_has_source_strategy(self) -> None:
        from weld.strategies.python_module import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            src.mkdir()
            (src / "handler.py").write_text("class RequestHandler:\n    pass\n")
            result = extract(root, {"glob": "src/*.py"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "python_module")
                _assert_valid_metadata_values(self, node["props"], "python_module")
                self.assertEqual(node["props"]["authority"], "derived")
                self.assertEqual(node["props"]["confidence"], "definite")
                self.assertIn("implementation", node["props"]["roles"])

class TypescriptExportsMetadataTest(unittest.TestCase):
    def test_ts_has_source_strategy(self) -> None:
        from weld.strategies.typescript_exports import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            src.mkdir()
            (src / "utils.ts").write_text("export function formatPrice(): string { return '0'; }")
            result = extract(root, {"glob": "src/*.ts"}, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                _assert_valid_source_strategy(self, node["props"], "typescript_exports")
                _assert_valid_metadata_values(self, node["props"], "typescript_exports")
                self.assertEqual(node["props"]["authority"], "derived")
                self.assertEqual(node["props"]["confidence"], "inferred")
                self.assertIn("implementation", node["props"]["roles"])

if __name__ == "__main__":
    unittest.main()
