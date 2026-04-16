"""Tests for the deploy surface extraction strategy."""

from __future__ import annotations

import tempfile
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.deploy_surface import extract

_COMPOSE_DEPLOY = """\
version: "3.8"
services:
  api:
    image: myapp/api:latest
    deploy:
      replicas: 2
    ports:
      - "8000:8000"
  worker:
    image: myapp/worker:latest
    deploy:
      replicas: 1
"""

_CLOUD_RUN_SERVICE = """\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: api-service
spec:
  template:
    spec:
      containers:
        - image: gcr.io/project/api:latest
          ports:
            - containerPort: 8080
"""

class TestDeploySurfaceExtract:
    """Tests for deploy_surface strategy extract()."""

    def test_detects_compose_deploy_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "docker-compose.prod.yml").write_text(_COMPOSE_DEPLOY)

            source = {"glob": "docker-compose*.yml"}
            result = extract(root, source, {})

            assert isinstance(result, StrategyResult)
            # Should find a deploy node
            deploy_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] == "deploy"}
            assert len(deploy_nodes) >= 1

    def test_detects_cloud_run_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            deploy_dir = root / "deploy"
            deploy_dir.mkdir()
            (deploy_dir / "service.yaml").write_text(_CLOUD_RUN_SERVICE)

            source = {"glob": "deploy/*.yaml"}
            result = extract(root, source, {})

            deploy_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] == "deploy"}
            assert len(deploy_nodes) >= 1

    def test_normalized_metadata_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "docker-compose.prod.yml").write_text(_COMPOSE_DEPLOY)

            source = {"glob": "docker-compose*.yml"}
            result = extract(root, source, {})

            for nid, node in result.nodes.items():
                props = node["props"]
                assert props["source_strategy"] == "deploy_surface"
                assert props["authority"] == "canonical"
                assert props["confidence"] == "definite"
                assert isinstance(props["roles"], list)
                assert len(props["roles"]) > 0

    def test_edge_metadata_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "docker-compose.prod.yml").write_text(_COMPOSE_DEPLOY)

            source = {"glob": "docker-compose*.yml"}
            result = extract(root, source, {})

            for edge in result.edges:
                assert "source_strategy" in edge["props"]
                assert edge["props"]["source_strategy"] == "deploy_surface"

    def test_exclude_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "docker-compose.prod.yml").write_text(_COMPOSE_DEPLOY)

            source = {"glob": "docker-compose*.yml",
                      "exclude": ["docker-compose.prod.yml"]}
            result = extract(root, source, {})

            assert len(result.nodes) == 0

    def test_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"glob": "deploy/*.yaml"}
            result = extract(root, source, {})

            assert result.nodes == {}
            assert result.edges == []

    def test_non_deploy_yaml_skipped(self) -> None:
        """Strategy should skip YAML files that are not deploy configs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config.yaml").write_text("key: value\nother: stuff\n")

            source = {"glob": "config.yaml"}
            result = extract(root, source, {})

            # Generic YAML with no deploy/service markers -> no deploy nodes
            deploy_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] == "deploy"}
            assert len(deploy_nodes) == 0

    def test_discovered_from_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "docker-compose.prod.yml").write_text(_COMPOSE_DEPLOY)

            source = {"glob": "docker-compose*.yml"}
            result = extract(root, source, {})

            assert "docker-compose.prod.yml" in result.discovered_from

    def test_terraform_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            infra = root / "infra"
            infra.mkdir()
            (infra / "main.tf").write_text("""\
resource "google_cloud_run_service" "api" {
  name     = "api"
  location = "europe-north1"
}
""")
            source = {"glob": "infra/*.tf"}
            result = extract(root, source, {})

            deploy_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] == "deploy"}
            assert len(deploy_nodes) >= 1
