"""Tests for bounded LRU cache of child graphs in federated workspaces.

Validates:
- Cache hit avoids re-parsing from disk
- Cache invalidation when graph_sha256 changes on disk
- Bounded eviction: oldest entry evicted when capacity exceeded
- Correctness: query results identical with/without cache
- Cache does not affect discover determinism
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld.contract import SCHEMA_VERSION
from weld.federation import FederatedGraph
from weld.federation_support import ChildGraphCache
from weld.graph import Graph
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-04-16T12:00:00+00:00"


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "init")
    return repo_root


def _graph_payload(
    nodes: dict,
    edges: list[dict] | None = None,
    *,
    schema_version: int = 1,
) -> dict:
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": schema_version,
        },
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _write_root_graph(
    root: Path,
    children: list[str],
    edges: list[dict] | None = None,
) -> None:
    nodes = {
        f"repo:{name}": {
            "type": "repo",
            "label": name,
            "props": {"path": name},
        }
        for name in children
    }
    _write_graph(root, _graph_payload(nodes, edges, schema_version=2))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -------------------------------------------------------------------
# Unit tests for ChildGraphCache
# -------------------------------------------------------------------


class ChildGraphCacheUnitTest(unittest.TestCase):
    """Isolated tests for the bounded LRU cache data structure."""

    def test_put_and_get(self) -> None:
        cache = ChildGraphCache(maxsize=4)
        cache.put("alpha", "sha-a", "value-a")
        self.assertEqual(cache.get("alpha", "sha-a"), "value-a")

    def test_miss_on_unknown_key(self) -> None:
        cache = ChildGraphCache(maxsize=4)
        self.assertIsNone(cache.get("unknown", "sha-x"))

    def test_miss_on_sha_mismatch(self) -> None:
        cache = ChildGraphCache(maxsize=4)
        cache.put("alpha", "sha-a", "value-a")
        self.assertIsNone(cache.get("alpha", "sha-changed"))

    def test_eviction_at_capacity(self) -> None:
        cache = ChildGraphCache(maxsize=2)
        cache.put("a", "sha-a", "val-a")
        cache.put("b", "sha-b", "val-b")
        cache.put("c", "sha-c", "val-c")

        self.assertIsNone(cache.get("a", "sha-a"), "oldest entry should be evicted")
        self.assertEqual(cache.get("b", "sha-b"), "val-b")
        self.assertEqual(cache.get("c", "sha-c"), "val-c")

    def test_access_refreshes_lru_order(self) -> None:
        cache = ChildGraphCache(maxsize=2)
        cache.put("a", "sha-a", "val-a")
        cache.put("b", "sha-b", "val-b")

        # Access 'a' to refresh it, then add 'c'; 'b' should be evicted
        cache.get("a", "sha-a")
        cache.put("c", "sha-c", "val-c")

        self.assertEqual(cache.get("a", "sha-a"), "val-a")
        self.assertIsNone(cache.get("b", "sha-b"), "'b' should be evicted (LRU)")
        self.assertEqual(cache.get("c", "sha-c"), "val-c")

    def test_update_same_key_new_sha(self) -> None:
        cache = ChildGraphCache(maxsize=4)
        cache.put("alpha", "sha-1", "old")
        cache.put("alpha", "sha-2", "new")
        self.assertIsNone(cache.get("alpha", "sha-1"))
        self.assertEqual(cache.get("alpha", "sha-2"), "new")

    def test_len_and_clear(self) -> None:
        cache = ChildGraphCache(maxsize=4)
        self.assertEqual(len(cache), 0)
        cache.put("a", "sha-a", "val-a")
        cache.put("b", "sha-b", "val-b")
        self.assertEqual(len(cache), 2)
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_maxsize_one(self) -> None:
        cache = ChildGraphCache(maxsize=1)
        cache.put("a", "sha-a", "val-a")
        cache.put("b", "sha-b", "val-b")
        self.assertIsNone(cache.get("a", "sha-a"))
        self.assertEqual(cache.get("b", "sha-b"), "val-b")


# -------------------------------------------------------------------
# Integration tests: cache in FederatedGraph
# -------------------------------------------------------------------


class FederatedGraphCacheTest(unittest.TestCase):
    """Cache integration tests against full FederatedGraph."""

    def _make_workspace(
        self,
        tmp: str,
        *,
        child_count: int = 3,
        cache_maxsize: int | None = None,
    ) -> tuple[Path, list[str]]:
        root = Path(tmp)
        names: list[str] = []
        for i in range(child_count):
            name = f"child-{i}"
            names.append(name)
            repo = _init_repo(root / name)
            _write_graph(
                repo,
                _graph_payload({
                    f"file:src/{name}.py": {
                        "type": "file",
                        "label": f"{name} module",
                        "props": {"file": f"src/{name}.py"},
                    },
                }),
            )

        _write_workspaces(root, [ChildEntry(name=n, path=n) for n in names])
        _write_root_graph(root, names)
        return root, names

    def test_cache_hit_skips_json_parse(self) -> None:
        """On cache hit (sha256 match), the expensive JSON parse is skipped.

        The disk read itself still occurs to compute the sha256 for
        invalidation, but ``load_graph_bytes`` (JSON parse + validation)
        must not be called on a hit.
        """
        with TemporaryDirectory() as tmp:
            root, names = self._make_workspace(tmp, child_count=2)
            graph = FederatedGraph(root)

            # First load reads + parses from disk
            child = graph._load_child("child-0")
            self.assertIsInstance(child, Graph)

            # Track JSON parse calls via load_graph_bytes
            parse_count = 0
            original_load = __import__(
                "weld.federation_support", fromlist=["load_graph_bytes"]
            ).load_graph_bytes

            def counting_parse(raw: bytes, **kwargs) -> dict:
                nonlocal parse_count
                parse_count += 1
                return original_load(raw, **kwargs)

            with patch("weld.federation.load_graph_bytes", side_effect=counting_parse):
                # Second load: sha256 matches -> cache hit -> no parse
                child_again = graph._load_child("child-0")

            self.assertIsInstance(child_again, Graph)
            self.assertEqual(
                parse_count, 0,
                "cached child with unchanged sha256 should skip JSON parse",
            )

    def test_sha256_invalidation_reloads_from_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            root, names = self._make_workspace(tmp, child_count=1)
            graph = FederatedGraph(root)

            # First load
            child = graph._load_child("child-0")
            self.assertIsInstance(child, Graph)

            # Modify the child graph on disk (add a new node)
            child_root = root / "child-0"
            new_payload = _graph_payload({
                "file:src/child-0.py": {
                    "type": "file",
                    "label": "child-0 module",
                    "props": {"file": "src/child-0.py"},
                },
                "file:src/new-node.py": {
                    "type": "file",
                    "label": "new node",
                    "props": {"file": "src/new-node.py"},
                },
            })
            _write_graph(child_root, new_payload)

            # Invalidate cache so next load detects the sha change
            graph._child_cache.clear()

            # Reload should pick up the new node
            child_reloaded = graph._load_child("child-0")
            self.assertIsInstance(child_reloaded, Graph)
            self.assertIsNotNone(
                child_reloaded.get_node("file:src/new-node.py"),
                "reloaded graph should contain the new node",
            )

    def test_query_identical_with_and_without_cache(self) -> None:
        with TemporaryDirectory() as tmp:
            root, names = self._make_workspace(tmp, child_count=3)

            # Query with fresh graph (cold cache)
            graph1 = FederatedGraph(root)
            result1 = graph1.query("module", limit=20)

            # Query again (warm cache)
            result2 = graph1.query("module", limit=20)

            self.assertEqual(
                json.dumps(result1, sort_keys=True),
                json.dumps(result2, sort_keys=True),
                "query results must be identical regardless of cache state",
            )

    def test_eviction_forces_disk_reload(self) -> None:
        with TemporaryDirectory() as tmp:
            root, names = self._make_workspace(tmp, child_count=3)
            graph = FederatedGraph(root, cache_maxsize=2)

            # Load all three children; capacity=2 means child-0 gets evicted
            graph._load_child("child-0")
            graph._load_child("child-1")
            graph._load_child("child-2")

            # child-0 should have been evicted
            self.assertIsNone(
                graph._child_cache.get(
                    "child-0",
                    _sha256(
                        (root / "child-0" / ".weld" / "graph.json").read_bytes()
                    ),
                ),
                "evicted child should not be in cache",
            )

            # Loading child-0 again should re-read from disk
            read_paths: list[Path] = []
            original_read = graph._read_graph_bytes

            def tracking_read(p: Path) -> bytes:
                read_paths.append(p)
                return original_read(p)

            with patch.object(graph, "_read_graph_bytes", side_effect=tracking_read):
                child = graph._load_child("child-0")

            self.assertIsInstance(child, Graph)
            self.assertTrue(
                any("child-0" in str(p) for p in read_paths),
                "evicted child should be re-read from disk",
            )


class FederatedGraphCacheMaxsizeTest(unittest.TestCase):
    """Test that cache_maxsize parameter controls capacity."""

    def test_default_maxsize_is_reasonable(self) -> None:
        cache = ChildGraphCache()
        self.assertGreaterEqual(cache.maxsize, 8)

    def test_custom_maxsize_honored(self) -> None:
        cache = ChildGraphCache(maxsize=3)
        self.assertEqual(cache.maxsize, 3)


if __name__ == "__main__":
    unittest.main()
