"""End-to-end test for Layer C2 dockerfile/compose blast radius.

Layer C2 (ADR 0045) closes the dockerfile/compose island so a
``wd impact <copied-source-file>`` query reverse-traverses to the
Dockerfile that COPYs it and the compose service that builds from
that Dockerfile.

This test deliberately drives the **real** ``_discover_single_repo``
entrypoint -- not the strategies' ``extract()`` functions in isolation
-- so that ``_clean_and_dedup_edges`` (which prunes dangling edges) and
``close_graph`` are both exercised. Pre-fix, the dockerfile strategy
emitted a ``contains`` edge to ``file:requirements.txt`` without
emitting that file node, and the dedup pass silently dropped every
COPY edge. ``wd impact requirements.txt`` then returned "no nodes
matched target". This test pins the fix-forward: the file nodes must
exist after discovery, the edges must survive, and ``impact()`` must
reach the dockerfile and compose service.
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

from weld.discover import _discover_single_repo  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.impact import impact  # noqa: E402


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _build_fixture(root: Path) -> None:
    """Minimal fixture: requirements.txt + Dockerfile + compose."""
    _write(root / "requirements.txt", "weld\n")
    _write(root / "Dockerfile", (
        "FROM python:3.12-slim\n"
        "COPY requirements.txt /app/requirements.txt\n"
        "COPY ./src /app/src\n"
    ))
    (root / "src").mkdir(exist_ok=True)
    _write(root / "src" / "app.py", "x = 1\n")
    _write(root / "docker-compose.yml", (
        "services:\n"
        "  api:\n"
        "    build: .\n"
        "  cache:\n"
        "    image: redis:7\n"
    ))
    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n"
        "  - glob: 'Dockerfile*'\n"
        "    type: file\n"
        "    strategy: dockerfile\n"
        "  - glob: 'docker-compose.yml'\n"
        "    type: config\n"
        "    strategy: compose\n",
        encoding="utf-8",
    )


def _discover_and_load(root: Path) -> Graph:
    """Run discovery and write graph.json so ``Graph.load()`` sees it.

    ``_discover_single_repo`` returns the canonical graph dict but does
    not persist it to ``.weld/graph.json`` -- that write happens in the
    CLI layer (``weld.discover.main``). For the impact engine to work
    we mirror the CLI behaviour by writing the returned dict to disk.
    """
    graph_dict = _discover_single_repo(root, incremental=False)
    graph_path = root / ".weld" / "graph.json"
    graph_path.write_text(json.dumps(graph_dict), encoding="utf-8")
    g = Graph(root)
    g.load()
    return g


class BlastRadiusDockerfileComposeDiscoverTest(unittest.TestCase):
    """The headline ADR 0045 user scenario, end-to-end through ``discover``."""

    def test_copied_source_reaches_dockerfile_and_service_via_discover(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="c2-blast-radius-",
        ) as tmpdir:
            root = Path(tmpdir)
            _build_fixture(root)

            # Drive the real pipeline end-to-end and reload the graph
            # exactly the way the ``wd impact`` CLI would.
            graph = _discover_and_load(root)
            data = graph.dump()
            nodes = data["nodes"]

            # 1) The file: source node from the Dockerfile COPY must
            #    exist after post-processing. Without this, every COPY
            #    edge is pruned as dangling.
            self.assertIn("file:requirements.txt", nodes)
            self.assertIn("file:src", nodes)

            # 2) The dockerfile contains-edge to the source must
            #    survive _clean_and_dedup_edges.
            edges = data["edges"]
            df_to_req = [
                e for e in edges
                if e["from"] == "dockerfile:Dockerfile"
                and e["to"] == "file:requirements.txt"
                and e["type"] == "contains"
            ]
            self.assertTrue(
                df_to_req,
                "dockerfile -> contains -> file:requirements.txt must "
                "survive post-processing",
            )

            # 3) The headline scenario: edit requirements.txt and the
            #    blast radius reaches both the dockerfile and the api
            #    service that builds from it. The cache service has
            #    only an image: directive and must NOT be reached.
            result = impact(
                graph,
                target="requirements.txt",
                depth=4,
                stale_graph=False,
            )
            reached_ids = {n["id"] for n in result["direct_dependents"]}
            reached_ids |= {n["id"] for n in result["transitive_dependents"]}
            self.assertIn("dockerfile:Dockerfile", reached_ids)
            self.assertIn("service:default:api", reached_ids)
            self.assertNotIn("service:default:cache", reached_ids)

    def test_dockerfile_target_reaches_services_that_build_from_it(
        self,
    ) -> None:
        """Editing the Dockerfile reaches every compose service that
        ``build`` s from it, but not image-only services."""
        with tempfile.TemporaryDirectory(prefix="c2-blast-radius-df-") as tmpdir:
            root = Path(tmpdir)
            _write(root / "requirements.txt", "weld\n")
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY requirements.txt /app/requirements.txt\n"
            ))
            _write(root / "docker-compose.yml", (
                "services:\n"
                "  api:\n"
                "    build: .\n"
                "  worker:\n"
                "    build: .\n"
                "  cache:\n"
                "    image: redis:7\n"
            ))
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                "sources:\n"
                "  - glob: 'Dockerfile*'\n"
                "    type: file\n"
                "    strategy: dockerfile\n"
                "  - glob: 'docker-compose.yml'\n"
                "    type: config\n"
                "    strategy: compose\n",
                encoding="utf-8",
            )

            graph = _discover_and_load(root)

            result = impact(
                graph,
                target="dockerfile:Dockerfile",
                depth=2,
                stale_graph=False,
            )
            reached_ids = {n["id"] for n in result["direct_dependents"]}
            reached_ids |= {n["id"] for n in result["transitive_dependents"]}
            self.assertIn("service:default:api", reached_ids)
            self.assertIn("service:default:worker", reached_ids)
            self.assertNotIn("service:default:cache", reached_ids)


if __name__ == "__main__":
    unittest.main()
