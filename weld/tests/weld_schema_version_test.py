"""Acceptance tests for ``meta.schema_version`` and the ``repo`` node type.

Covers the policy documented in ADR 0011 section 11 and ADR 0012 section 4:

* ``repo`` is a first-class node type in the contract vocabulary.
* A graph containing any ``repo:*`` node writes ``meta.schema_version = 2``
  on save; a graph with no ``repo:*`` nodes writes ``meta.schema_version = 1``.
* A loader that advertises ``max_supported_schema_version = 1`` rejects a
  graph carrying ``meta.schema_version = 2`` with a human-readable error
  that names both the literal ``schema_version`` and the word ``upgrade``.
* A child graph (no ``repo:*`` nodes) always lands at ``schema_version = 1``,
  preserving the OSS-split byte-compat contract (ADR 0011 section 13).

The tests are independent of the canonical serializer implementation and
operate on the ``Graph`` public surface (``add_node``, ``save``, ``load``)
plus the validator (``validate_graph``).
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

from weld.contract import (  # noqa: E402
    SCHEMA_VERSION,
    VALID_NODE_TYPES,
    validate_graph,
)
from weld.graph import (  # noqa: E402
    CHILD_SCHEMA_VERSION,
    ROOT_FEDERATED_SCHEMA_VERSION,
    Graph,
    SchemaVersionError,
    load_graph_file,
)


_TS = "2026-04-02T12:00:00+00:00"


def _write_graph_file(path: Path, *, schema_version: int, repo_node: bool) -> None:
    """Hand-craft a ``graph.json`` with a chosen ``schema_version``.

    When *repo_node* is ``True`` the file also contains one ``repo:demo``
    node so the file is consistent with the v2 contract. When it is
    ``False`` the file is a plain single-repo child graph.
    """
    nodes: dict[str, dict] = {}
    if repo_node:
        nodes["repo:demo"] = {
            "type": "repo",
            "label": "demo",
            "props": {"path": "services/demo"},
        }
    graph = {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": schema_version,
        },
        "nodes": nodes,
        "edges": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2), encoding="utf-8")


class RepoNodeTypeTest(unittest.TestCase):
    """The contract recognises ``repo`` as a valid node type (ADR 0011 section 4)."""

    def test_repo_is_in_valid_node_types(self) -> None:
        self.assertIn("repo", VALID_NODE_TYPES)

    def test_validate_graph_accepts_repo_node(self) -> None:
        graph = {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
            "nodes": {
                "repo:services-api": {
                    "type": "repo",
                    "label": "services-api",
                    "props": {"path": "services/api"},
                },
            },
            "edges": [],
        }
        errors = validate_graph(graph)
        self.assertEqual(errors, [], msg=f"unexpected errors: {errors}")


class SaveEmitsSchemaVersionTest(unittest.TestCase):
    """``Graph.save`` stamps ``meta.schema_version`` based on content (ADR 0012 section 4)."""

    def _make_tree(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="weld-schema-test-"))
        self.addCleanup(lambda: None)  # Garbage collection handles the tmpdir.
        return tmp

    def test_graph_with_repo_node_saves_schema_version_two(self) -> None:
        root = self._make_tree()
        g = Graph(root)
        g.load()
        g.add_node(
            "repo:services-api",
            "repo",
            "services-api",
            {"path": "services/api"},
        )
        g.save()

        data = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(
            data["meta"].get("schema_version"),
            ROOT_FEDERATED_SCHEMA_VERSION,
            msg="meta.schema_version must be 2 when the graph carries a repo:* node",
        )
        self.assertEqual(ROOT_FEDERATED_SCHEMA_VERSION, 2)

    def test_graph_without_repo_nodes_saves_schema_version_one(self) -> None:
        root = self._make_tree()
        g = Graph(root)
        g.load()
        g.add_node(
            "service:api",
            "service",
            "api",
            {"file": "services/api/main.py"},
        )
        g.save()

        data = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(
            data["meta"].get("schema_version"),
            CHILD_SCHEMA_VERSION,
            msg="meta.schema_version must be 1 on a plain single-repo graph",
        )
        self.assertEqual(CHILD_SCHEMA_VERSION, 1)

    def test_removing_only_repo_node_downgrades_schema_version(self) -> None:
        """Guard: the bump is content-driven, not sticky.

        Proves the test is not trivially green -- if ``Graph.save`` always
        wrote ``2`` the downgrade path would still claim ``2``. Removing the
        last ``repo:*`` node must return the file to ``schema_version = 1``.
        """
        root = self._make_tree()
        g = Graph(root)
        g.load()
        g.add_node(
            "repo:services-api",
            "repo",
            "services-api",
            {"path": "services/api"},
        )
        g.save()
        first = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(first["meta"]["schema_version"], 2)

        g.rm_node("repo:services-api")
        g.save()
        second = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(second["meta"]["schema_version"], 1)


class OldReaderRejectsNewerSchemaTest(unittest.TestCase):
    """An old reader must refuse a newer graph with a human-readable error (ADR 0012 section 4)."""

    def test_load_graph_file_rejects_newer_schema_version(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="weld-old-reader-"))
        path = tmp / "graph.json"
        _write_graph_file(path, schema_version=2, repo_node=True)

        with self.assertRaises(SchemaVersionError) as ctx:
            load_graph_file(path, max_supported_schema_version=1)

        message = str(ctx.exception)
        self.assertIn("schema_version", message)
        self.assertIn("upgrade", message)
        # The error must quote the observed version so operators can read the
        # mismatch off the log line without re-running with -v.
        self.assertIn("2", message)

    def test_load_graph_file_accepts_equal_or_older_schema_version(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="weld-old-reader-ok-"))
        path = tmp / "graph.json"
        _write_graph_file(path, schema_version=1, repo_node=False)

        data = load_graph_file(path, max_supported_schema_version=1)
        self.assertEqual(data["meta"]["schema_version"], 1)

    def test_load_graph_file_treats_missing_schema_version_as_one(self) -> None:
        """ADR 0012 section 4: missing ``schema_version`` reads as ``1`` for back-compat."""
        tmp = Path(tempfile.mkdtemp(prefix="weld-old-reader-missing-"))
        path = tmp / "graph.json"
        graph = {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
            "nodes": {},
            "edges": [],
        }
        path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

        data = load_graph_file(path, max_supported_schema_version=1)
        self.assertEqual(data["meta"].get("schema_version", 1), 1)

    def test_current_reader_accepts_schema_version_two(self) -> None:
        """The default ``load_graph_file`` (current build) loads v2 without error."""
        tmp = Path(tempfile.mkdtemp(prefix="weld-new-reader-ok-"))
        path = tmp / "graph.json"
        _write_graph_file(path, schema_version=2, repo_node=True)

        data = load_graph_file(path)  # default max = ROOT_FEDERATED_SCHEMA_VERSION.
        self.assertEqual(data["meta"]["schema_version"], 2)


class ChildGraphIsByteCompatibleTest(unittest.TestCase):
    """ADR 0011 section 13: a child graph (no repo nodes) remains schema_version=1."""

    def test_child_graph_written_by_graph_api_carries_schema_version_one(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="weld-child-"))
        g = Graph(root)
        g.load()
        g.add_node(
            "service:api",
            "service",
            "api",
            {"file": "services/api/main.py"},
        )
        g.add_node(
            "package:domain",
            "package",
            "Domain",
            {},
        )
        g.save()

        data = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(data["meta"].get("schema_version"), 1)
        for node in data["nodes"].values():
            self.assertNotEqual(node["type"], "repo")


class DiscoverOutputStampsSchemaVersionTest(unittest.TestCase):
    """``discover.py`` stamps ``meta.schema_version`` content-driven too.

    The acceptance criteria require a single-repo discover to produce
    ``schema_version = 1`` and a root meta-graph carrying ``repo:*`` nodes
    to produce ``schema_version = 2``. The serializer itself is decision-
    free, so the stamping lives in the producer side (``discover.py``
    today, ``Graph.save`` for the ``Graph`` API). This test exercises the
    producer logic directly via the pure helper to avoid pulling in the
    full discovery orchestrator.
    """

    def test_helper_returns_one_for_empty_graph(self) -> None:
        from weld.graph import _schema_version_for

        self.assertEqual(_schema_version_for({}), 1)

    def test_helper_returns_one_for_nodes_without_repo(self) -> None:
        from weld.graph import _schema_version_for

        nodes = {
            "service:api": {"type": "service", "label": "api", "props": {}},
            "package:domain": {"type": "package", "label": "Domain", "props": {}},
        }
        self.assertEqual(_schema_version_for(nodes), 1)

    def test_helper_returns_two_when_any_node_is_repo(self) -> None:
        from weld.graph import _schema_version_for

        nodes = {
            "service:api": {"type": "service", "label": "api", "props": {}},
            "repo:services-api": {
                "type": "repo",
                "label": "services-api",
                "props": {"path": "services/api"},
            },
        }
        self.assertEqual(_schema_version_for(nodes), 2)


if __name__ == "__main__":
    unittest.main()
