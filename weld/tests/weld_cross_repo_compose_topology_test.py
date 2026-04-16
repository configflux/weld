"""Tests for the compose_topology cross-repo resolver.

Covers: depends_on edge emission, determinism, unmatched/missing child
handling, compose file variants, image ref stripping, map-form depends_on,
and edge props.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from weld.cross_repo.base import CrossRepoEdge, ResolverContext, resolver_names
from weld.cross_repo import compose_topology as _mod  # noqa: F401
from weld.cross_repo.compose_topology import ComposeTopologyResolver
from weld.workspace import UNIT_SEPARATOR

# Shared minimal compose content used across many tests.
_BASIC_COMPOSE = """\
services:
  api:
    image: repo-a
    depends_on:
      - auth
  auth:
    image: repo-b
"""


class _FakeGraph:
    """Minimal stand-in for a loaded child graph."""

    def __init__(self) -> None:
        self._data: dict = {"nodes": [], "edges": []}


def _ctx(root: str, children: dict[str, object] | None = None) -> ResolverContext:
    children = children or {}
    hashes = {n: ResolverContext.hash_bytes(b"{}") for n in children}
    return ResolverContext(
        workspace_root=root,
        cross_repo_strategies=["compose_topology"],
        children=children,
        child_hashes=hashes,
    )


def _write(root: str, content: str, name: str = "docker-compose.yml") -> str:
    p = os.path.join(root, name)
    with open(p, "w") as f:
        f.write(content)
    return p


def _children(*names: str) -> dict[str, _FakeGraph]:
    return {n: _FakeGraph() for n in names}


def _resolve(root: str) -> list[CrossRepoEdge]:
    return ComposeTopologyResolver().resolve(
        _ctx(root, _children("repo-a", "repo-b"))
    )


class RegistrationTest(unittest.TestCase):
    def test_registered_name(self) -> None:
        self.assertIn("compose_topology", resolver_names())

    def test_class_name_attribute(self) -> None:
        self.assertEqual(ComposeTopologyResolver.name, "compose_topology")


class BasicEdgesTest(unittest.TestCase):
    """depends_on wiring emits directional edges."""

    def test_depends_on_emits_edge(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, _BASIC_COMPOSE)
            edges = _resolve(root)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].type, "depends_on")
            self.assertTrue(edges[0].from_id.startswith(f"repo-a{UNIT_SEPARATOR}"))
            self.assertTrue(edges[0].to_id.startswith(f"repo-b{UNIT_SEPARATOR}"))

    def test_multiple_depends_on(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, """\
services:
  frontend:
    image: repo-a
    depends_on:
      - api
      - cache
  api:
    image: repo-b
  cache:
    image: repo-c
""")
            ctx = _ctx(root, _children("repo-a", "repo-b", "repo-c"))
            edges = ComposeTopologyResolver().resolve(ctx)
            self.assertEqual(len(edges), 2)
            to_repos = sorted(e.to_id.split(UNIT_SEPARATOR)[0] for e in edges)
            self.assertEqual(to_repos, ["repo-b", "repo-c"])


class DeterminismTest(unittest.TestCase):
    def test_idempotent_output(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, _BASIC_COMPOSE)
            r = ComposeTopologyResolver()
            ctx = _ctx(root, _children("repo-a", "repo-b"))
            e1, e2 = r.resolve(ctx), r.resolve(ctx)
            self.assertEqual([e.to_dict() for e in e1], [e.to_dict() for e in e2])


class UnmatchedServiceTest(unittest.TestCase):
    def test_unmatched_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, """\
services:
  api:
    image: repo-a
    depends_on:
      - redis
  redis:
    image: redis:latest
""")
            ctx = _ctx(root, _children("repo-a"))
            self.assertEqual(ComposeTopologyResolver().resolve(ctx), [])

    def test_partial_match(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, """\
services:
  api:
    image: repo-a
    depends_on:
      - auth
      - redis
  auth:
    image: repo-b
  redis:
    image: redis:latest
""")
            edges = _resolve(root)
            self.assertEqual(len(edges), 1)
            self.assertTrue(edges[0].to_id.startswith(f"repo-b{UNIT_SEPARATOR}"))


class MissingChildTest(unittest.TestCase):
    def test_target_missing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, _BASIC_COMPOSE)
            ctx = _ctx(root, _children("repo-a"))  # repo-b absent
            self.assertEqual(ComposeTopologyResolver().resolve(ctx), [])

    def test_source_missing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, _BASIC_COMPOSE)
            ctx = _ctx(root, _children("repo-b"))  # repo-a absent
            self.assertEqual(ComposeTopologyResolver().resolve(ctx), [])


class EdgeRemovalTest(unittest.TestCase):
    def test_no_depends_on_no_edges(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, """\
services:
  api:
    image: repo-a
  auth:
    image: repo-b
""")
            self.assertEqual(_resolve(root), [])


class FileVariantsTest(unittest.TestCase):
    def _check(self, filename: str) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, _BASIC_COMPOSE, name=filename)
            self.assertEqual(len(_resolve(root)), 1)

    def test_compose_yaml(self) -> None:
        self._check("compose.yaml")

    def test_compose_yml(self) -> None:
        self._check("compose.yml")

    def test_docker_compose_yml(self) -> None:
        self._check("docker-compose.yml")


class NoFileTest(unittest.TestCase):
    def test_no_compose_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(_resolve(root), [])


class ServiceNameMatchTest(unittest.TestCase):
    def test_service_name_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, """\
services:
  repo-a:
    build: ./repo-a
    depends_on:
      - repo-b
  repo-b:
    build: ./repo-b
""")
            edges = _resolve(root)
            self.assertEqual(len(edges), 1)
            self.assertTrue(edges[0].from_id.startswith(f"repo-a{UNIT_SEPARATOR}"))
            self.assertTrue(edges[0].to_id.startswith(f"repo-b{UNIT_SEPARATOR}"))

    def test_image_tag_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, """\
services:
  api:
    image: repo-a:latest
    depends_on:
      - auth
  auth:
    image: repo-b:v1.2.3
""")
            self.assertEqual(len(_resolve(root)), 1)

    def test_registry_prefix_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, """\
services:
  api:
    image: ghcr.io/org/repo-a:latest
    depends_on:
      - auth
  auth:
    image: docker.io/org/repo-b
""")
            self.assertEqual(len(_resolve(root)), 1)


class EdgePropsTest(unittest.TestCase):
    def test_edge_carries_service_names(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, _BASIC_COMPOSE)
            edges = _resolve(root)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].props.get("from_service"), "api")
            self.assertEqual(edges[0].props.get("to_service"), "auth")
            self.assertIn("source_file", edges[0].props)


class MapFormDependsOnTest(unittest.TestCase):
    def test_map_form(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(root, """\
services:
  api:
    image: repo-a
    depends_on:
      auth:
        condition: service_healthy
  auth:
    image: repo-b
""")
            edges = _resolve(root)
            self.assertEqual(len(edges), 1)
            self.assertTrue(edges[0].from_id.startswith(f"repo-a{UNIT_SEPARATOR}"))
            self.assertTrue(edges[0].to_id.startswith(f"repo-b{UNIT_SEPARATOR}"))


if __name__ == "__main__":
    unittest.main()
