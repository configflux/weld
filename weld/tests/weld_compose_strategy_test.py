"""Tests for the Docker Compose strategy: service node + edge emission.

Layer C2 (ADR 0045) makes each declared service a first-class
``service:<stem>:<name>`` node, with:

- ``compose:<stem> --contains--> service:<stem>:<name>`` per service.
- ``service:<stem>:<name> --depends_on--> dockerfile:<stem>`` when
  ``service.build`` is set (string dir, mapping with ``context`` +
  ``dockerfile``, or direct file path).
- ``service:<stem>:<name> --contains--> file:<env-file>`` per
  ``env_file:`` entry (string or list).
- ``runtime_image`` prop on the service node when ``service.image`` is
  set and ``build`` is not (no new node type, no schema bump -- bd notes).

Existing legacy ``compose --orchestrates--> service:<bare>`` edges for
the conventional ``api`` / ``web`` / ``worker`` names are preserved for
back-compat.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.compose import extract


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


COMPOSE_BUILD_AND_IMAGE = """\
services:
  api:
    build: ./api
    env_file:
      - ./api/.env
      - ./shared/common.env
  db:
    image: postgres:16
    env_file: ./db/.env
"""


class ComposeServiceNodesTest(unittest.TestCase):
    def test_each_service_emits_first_class_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", COMPOSE_BUILD_AND_IMAGE)
            _write(root / "api" / "Dockerfile", "FROM python:3.12\n")
            _write(root / "api" / ".env", "X=1\n")
            _write(root / "shared" / "common.env", "Y=2\n")
            _write(root / "db" / ".env", "Z=3\n")

            result = extract(root, {"glob": "docker-compose.yml"}, {})
            self.assertIsInstance(result, StrategyResult)
            api_id = "service:default:api"
            db_id = "service:default:db"
            self.assertIn(api_id, result.nodes)
            self.assertIn(db_id, result.nodes)
            self.assertEqual(result.nodes[api_id]["type"], "service")
            self.assertEqual(
                result.nodes[api_id]["props"]["service_name"], "api",
            )
            self.assertEqual(
                result.nodes[api_id]["props"]["compose_stem"], "default",
            )
            self.assertEqual(
                result.nodes[db_id]["props"]["runtime_image"], "postgres:16",
            )
            # Build-bound services do not stamp runtime_image.
            self.assertNotIn(
                "runtime_image", result.nodes[api_id]["props"],
            )

    def test_compose_node_contains_each_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", COMPOSE_BUILD_AND_IMAGE)

            result = extract(root, {"glob": "docker-compose.yml"}, {})
            compose_id = "compose:default"
            contains_to = sorted(
                e["to"] for e in result.edges
                if e["from"] == compose_id and e["type"] == "contains"
            )
            self.assertIn("service:default:api", contains_to)
            self.assertIn("service:default:db", contains_to)


class ComposeBuildResolutionTest(unittest.TestCase):
    def test_build_string_dir_resolves_to_dockerfile_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", (
                "services:\n"
                "  api:\n"
                "    build: ./api\n"
            ))
            _write(root / "api" / "Dockerfile", "FROM python:3.12\n")

            result = extract(root, {"glob": "docker-compose.yml"}, {})
            api_id = "service:default:api"
            depends = [
                e for e in result.edges
                if e["from"] == api_id and e["type"] == "depends_on"
            ]
            self.assertEqual(len(depends), 1)
            self.assertEqual(depends[0]["to"], "dockerfile:Dockerfile")

    def test_build_mapping_with_explicit_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", (
                "services:\n"
                "  api:\n"
                "    build:\n"
                "      context: ./api\n"
                "      dockerfile: Containerfile\n"
            ))
            _write(root / "api" / "Containerfile", "FROM python:3.12\n")

            result = extract(root, {"glob": "docker-compose.yml"}, {})
            api_id = "service:default:api"
            depends = [
                e["to"] for e in result.edges
                if e["from"] == api_id and e["type"] == "depends_on"
            ]
            # Stem matches the dockerfile.py id derivation rules.
            self.assertEqual(depends, ["dockerfile:Containerfile"])

    def test_build_dangling_dockerfile_still_emits_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", (
                "services:\n"
                "  api:\n"
                "    build: ./api\n"
            ))
            # No api/Dockerfile on disk; graph closure must still see the edge.
            (root / "api").mkdir()

            result = extract(root, {"glob": "docker-compose.yml"}, {})
            api_id = "service:default:api"
            depends = [
                e["to"] for e in result.edges
                if e["from"] == api_id and e["type"] == "depends_on"
            ]
            # Edge still emitted (target stem assumed Dockerfile by convention).
            self.assertEqual(depends, ["dockerfile:Dockerfile"])


class ComposeEnvFileTest(unittest.TestCase):
    def test_env_file_single_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", (
                "services:\n"
                "  api:\n"
                "    image: alpine\n"
                "    env_file: ./api/.env\n"
            ))
            _write(root / "api" / ".env", "X=1\n")

            result = extract(root, {"glob": "docker-compose.yml"}, {})
            api_id = "service:default:api"
            env_edges = sorted(
                e["to"] for e in result.edges
                if e["from"] == api_id and e["type"] == "contains"
            )
            self.assertEqual(env_edges, ["file:api/.env"])
            # Layer C2 fix-forward: each env_file edge must be paired
            # with a ``file:*`` node so ``_clean_and_dedup_edges`` does
            # not prune the edge in the real ``wd discover`` pipeline.
            self.assertIn("file:api/.env", result.nodes)
            fnode = result.nodes["file:api/.env"]
            self.assertEqual(fnode["type"], "file")
            self.assertEqual(
                fnode["props"].get("source_strategy"), "compose",
            )
            self.assertIn("config", fnode["props"].get("roles", []))

    def test_env_file_list_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", (
                "services:\n"
                "  api:\n"
                "    image: alpine\n"
                "    env_file:\n"
                "      - ./api/.env\n"
                "      - ./shared/common.env\n"
            ))
            _write(root / "api" / ".env", "X=1\n")
            _write(root / "shared" / "common.env", "Y=2\n")

            result = extract(root, {"glob": "docker-compose.yml"}, {})
            api_id = "service:default:api"
            env_edges = sorted(
                e["to"] for e in result.edges
                if e["from"] == api_id and e["type"] == "contains"
            )
            self.assertEqual(
                env_edges, ["file:api/.env", "file:shared/common.env"],
            )
            # Both env_file nodes must be present; deduped by id.
            self.assertIn("file:api/.env", result.nodes)
            self.assertIn("file:shared/common.env", result.nodes)


class ComposeBackCompatTest(unittest.TestCase):
    def test_orchestrates_legacy_bare_service_edges_preserved(self) -> None:
        """Layer C2 must not break the pre-existing
        ``compose:<stem> --orchestrates--> service:<bare>`` mapping for
        the conventional ``api`` / ``web`` / ``worker`` names. The
        new typed ``compose --contains--> service:<stem>:<name>`` edges
        are additive."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", (
                "services:\n"
                "  api:\n"
                "    image: alpine\n"
                "  worker:\n"
                "    image: alpine\n"
            ))

            result = extract(root, {"glob": "docker-compose.yml"}, {})
            orch = sorted(
                e["to"] for e in result.edges
                if e["type"] == "orchestrates"
            )
            self.assertIn("service:api", orch)
            self.assertIn("service:worker", orch)


class ComposeDeterminismTest(unittest.TestCase):
    def test_services_sorted_in_compose_props(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "docker-compose.yml", (
                "services:\n"
                "  zebra:\n"
                "    image: alpine\n"
                "  apple:\n"
                "    image: alpine\n"
                "  mango:\n"
                "    image: alpine\n"
            ))
            result = extract(root, {"glob": "docker-compose.yml"}, {})
            services_prop = result.nodes["compose:default"]["props"]["services"]
            # bd notes: services in props sorted by name.
            self.assertEqual(services_prop, sorted(services_prop))


if __name__ == "__main__":
    unittest.main()
