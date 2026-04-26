"""Tests for the persisted query-state sidecar (ADR 0031).

Covers:

- write/read roundtrip preserves the inverted index, BM25 corpus, and
  structural scores;
- missing sidecar -> ``read_sidecar`` returns ``None``;
- stale sidecar (graph digest mismatch, format-version mismatch,
  weld-schema mismatch, node/edge count mismatch) -> ``read_sidecar``
  returns ``None`` without raising;
- corrupt sidecar bytes -> ``read_sidecar`` returns ``None`` without
  raising (must NOT crash ``Graph.load``);
- ``Graph.load`` consults the sidecar on hot path and rebuilds + writes
  on cold/missing/corrupt paths;
- ``_discover_single_repo`` writes the sidecar after a full discovery
  so the next cold ``Graph.load`` is fast.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _query_sidecar as sidecar  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.query_state import build_query_state  # noqa: E402


def _sample_nodes() -> dict[str, dict]:
    return {
        "service:api": {
            "type": "service",
            "label": "api",
            "props": {
                "file": "services/api.py",
                "exports": ["create_user", "delete_user"],
                "description": "REST API entrypoint for users",
            },
        },
        "service:worker": {
            "type": "service",
            "label": "worker",
            "props": {
                "file": "services/worker.py",
                "exports": ["enqueue_job"],
                "description": "background job processor",
            },
        },
        "package:core": {
            "type": "package",
            "label": "core",
            "props": {"file": "core/__init__.py"},
        },
    }


def _sample_edges() -> list[dict]:
    return [
        {"from": "service:api", "to": "package:core", "type": "imports", "props": {}},
        {"from": "service:worker", "to": "package:core", "type": "imports", "props": {}},
    ]


def _write_graph_json(root: Path, nodes: dict, edges: list) -> Path:
    """Write a minimal valid graph.json under root/.weld/."""
    from weld.serializer import dumps_graph

    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    graph_path.write_text(
        dumps_graph(
            {
                "meta": {"version": 1, "schema_version": 1},
                "nodes": nodes,
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )
    return graph_path


# ---------------------------------------------------------------------------
# Unit tests: sidecar I/O contract
# ---------------------------------------------------------------------------


class SidecarRoundtripTest(unittest.TestCase):
    def test_write_then_read_returns_equivalent_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)
            loaded = sidecar.read_sidecar(graph_path, nodes, edges)
            self.assertIsNotNone(loaded)
            assert loaded is not None  # for type-checker
            self.assertEqual(
                loaded.inverted_index, state.inverted_index,
                "roundtripped inverted index must equal the in-memory one",
            )
            self.assertEqual(
                loaded.structural_scores, state.structural_scores,
            )
            # BM25Corpus has internal state but shares scoring API; sample one.
            self.assertEqual(
                loaded.bm25.doc_count, state.bm25.doc_count,
            )

    def test_sidecar_path_lives_beside_graph_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)
            self.assertTrue(
                (root / ".weld" / "query_state.bin").is_file(),
                "sidecar must be written next to graph.json as query_state.bin",
            )


class SidecarMissingTest(unittest.TestCase):
    def test_missing_sidecar_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            self.assertIsNone(sidecar.read_sidecar(graph_path, nodes, edges))


class SidecarStaleTest(unittest.TestCase):
    def test_stale_by_graph_digest_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)
            # Mutate the graph bytes -> digest changes -> sidecar is stale.
            graph_path.write_text(
                graph_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            self.assertIsNone(sidecar.read_sidecar(graph_path, nodes, edges))

    def test_stale_by_format_version_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)
            # Read with a future format version -> treat as absent.
            with patch.object(sidecar, "_FORMAT_VERSION", sidecar._FORMAT_VERSION + 1):
                self.assertIsNone(sidecar.read_sidecar(graph_path, nodes, edges))

    def test_stale_by_weld_schema_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)
            with patch.object(sidecar, "_weld_schema_version", lambda: SCHEMA_VERSION + 1):
                self.assertIsNone(sidecar.read_sidecar(graph_path, nodes, edges))

    def test_stale_by_node_edge_count_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)
            # Pretend caller has a different node set without rewriting graph.
            shrunk = {"service:api": nodes["service:api"]}
            self.assertIsNone(sidecar.read_sidecar(graph_path, shrunk, []))


class SidecarCorruptTest(unittest.TestCase):
    def test_corrupt_pickle_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            (root / ".weld" / "query_state.bin").write_bytes(b"this is not a pickle")
            # Must NOT raise -- corrupt sidecar is treated as absent.
            self.assertIsNone(sidecar.read_sidecar(graph_path, nodes, edges))

    def test_truncated_pickle_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)
            sidecar_path = root / ".weld" / "query_state.bin"
            data = sidecar_path.read_bytes()
            sidecar_path.write_bytes(data[: max(1, len(data) // 2)])
            self.assertIsNone(sidecar.read_sidecar(graph_path, nodes, edges))

    def test_missing_envelope_keys_returns_none(self) -> None:
        import pickle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            (root / ".weld" / "query_state.bin").write_bytes(
                pickle.dumps({"unrelated": "object"}),
            )
            self.assertIsNone(sidecar.read_sidecar(graph_path, nodes, edges))


# ---------------------------------------------------------------------------
# Integration: Graph.load wiring
# ---------------------------------------------------------------------------


class GraphLoadSidecarTest(unittest.TestCase):
    def test_graph_load_uses_sidecar_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)

            # When the sidecar is fresh, build_query_state must NOT be
            # invoked during Graph.load.
            with patch(
                "weld.graph._build_query_state",
                wraps=build_query_state,
            ) as build_spy:
                g = Graph(root)
                g.load()
                self.assertEqual(
                    build_spy.call_count, 0,
                    "Graph.load must not rebuild query state when sidecar is fresh",
                )
            # And the graph remains queryable.
            self.assertEqual(g._query_state_counts, (len(nodes), len(edges)))

    def test_graph_load_rebuilds_and_writes_when_sidecar_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            _write_graph_json(root, nodes, edges)
            self.assertFalse((root / ".weld" / "query_state.bin").exists())
            g = Graph(root)
            g.load()
            self.assertTrue(
                (root / ".weld" / "query_state.bin").is_file(),
                "Graph.load must write a sidecar after a cold rebuild",
            )

    def test_graph_load_rebuilds_when_sidecar_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            _write_graph_json(root, nodes, edges)
            (root / ".weld" / "query_state.bin").write_bytes(b"not-a-pickle")
            # Must not raise; must rebuild.
            g = Graph(root)
            g.load()  # would raise if corrupt sidecar leaked an exception
            self.assertEqual(g._query_state_counts, (len(nodes), len(edges)))
            # And it should have rewritten a valid sidecar.
            sidecar_path = root / ".weld" / "query_state.bin"
            self.assertTrue(sidecar_path.is_file())
            self.assertNotEqual(
                sidecar_path.read_bytes(), b"not-a-pickle",
                "Graph.load must overwrite a corrupt sidecar with a fresh one",
            )

    def test_graph_load_query_returns_results_via_sidecar_path(self) -> None:
        """End-to-end: load via sidecar and run a query."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes, edges = _sample_nodes(), _sample_edges()
            graph_path = _write_graph_json(root, nodes, edges)
            state = build_query_state(nodes, edges)
            sidecar.write_sidecar(graph_path, nodes, edges, state)
            g = Graph(root)
            g.load()
            res = g.query("api", limit=5)
            self.assertTrue(res["matches"], "query via sidecar must find matches")


# ---------------------------------------------------------------------------
# Integration: discover writes the sidecar
# ---------------------------------------------------------------------------


class DiscoverWritesSidecarTest(unittest.TestCase):
    def test_full_discovery_writes_sidecar_matching_graph(self) -> None:
        from weld.discover import _discover_single_repo

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            # Empty discover.yaml -> empty sources list, post_process still
            # writes a valid graph dict to disk via the caller; here we just
            # call _discover_single_repo and write the result ourselves to
            # verify the sidecar hook fires alongside the canonical write.
            (root / ".weld" / "discover.yaml").write_text(
                "sources: []\n", encoding="utf-8",
            )
            graph = _discover_single_repo(root, incremental=False)
            # Simulate what the caller does (write graph.json to .weld/).
            from weld.serializer import dumps_graph
            graph_path = root / ".weld" / "graph.json"
            graph_path.write_text(dumps_graph(graph), encoding="utf-8")
            # The discover hook must have written the sidecar already.
            sidecar_path = root / ".weld" / "query_state.bin"
            self.assertTrue(
                sidecar_path.is_file(),
                "wd discover must write the query-state sidecar",
            )
            # And it must be readable as a fresh hit on the just-written graph.
            loaded = sidecar.read_sidecar(
                graph_path, graph.get("nodes", {}), graph.get("edges", []),
            )
            self.assertIsNotNone(
                loaded,
                "sidecar written by discover must be a fresh hit for that graph",
            )


if __name__ == "__main__":
    unittest.main()
