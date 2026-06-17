"""Unit tests for the volatile-meta sidecar (ADR 0065).

Covers the split (write) / merge (read) helpers, the paired writer's
atomic graph.json + graph-meta.json emission, the canonical-name guard,
and -- critically for the migration -- the backward-compatible read path
for legacy graphs that still carry the volatile keys in-graph and a fresh
checkout where the gitignored sidecar is absent.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld._graph_meta_sidecar import (  # noqa: E402
    VOLATILE_META_KEYS,
    load_graph_meta,
    merge_sidecar_meta,
    read_sidecar_meta,
    sidecar_path_for,
    split_volatile_meta,
    write_graph_with_meta,
)


def _graph() -> dict:
    return {
        "meta": {
            "version": 5,
            "updated_at": "2026-06-12T00:00:00+00:00",
            "git_sha": "deadbeef",
            "discovered_from": ["weld/"],
            "schema_version": 1,
        },
        "nodes": {"n:1": {"type": "file", "label": "L", "props": {}}},
        "edges": [],
    }


class SplitVolatileMetaTest(unittest.TestCase):
    def test_split_removes_volatile_and_keeps_rest(self) -> None:
        on_disk, volatile = split_volatile_meta(_graph())
        self.assertEqual(
            volatile, {"updated_at": "2026-06-12T00:00:00+00:00", "git_sha": "deadbeef"}
        )
        for key in VOLATILE_META_KEYS:
            self.assertNotIn(key, on_disk["meta"])
        # discovered_from is content-stable and must stay in graph.json.
        self.assertEqual(on_disk["meta"]["discovered_from"], ["weld/"])

    def test_split_does_not_mutate_input(self) -> None:
        graph = _graph()
        split_volatile_meta(graph)
        self.assertIn("git_sha", graph["meta"])
        self.assertIn("updated_at", graph["meta"])

    def test_split_omits_absent_volatile_keys(self) -> None:
        graph = {"meta": {"version": 5}, "nodes": {}, "edges": []}
        on_disk, volatile = split_volatile_meta(graph)
        self.assertEqual(volatile, {})
        self.assertEqual(on_disk["meta"], {"version": 5})


class WriteGraphWithMetaTest(unittest.TestCase):
    def test_writes_graph_without_volatile_plus_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            weld_dir = Path(d) / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            write_graph_with_meta(graph_path, _graph())

            disk_meta = json.loads(graph_path.read_text())["meta"]
            self.assertNotIn("git_sha", disk_meta)
            self.assertNotIn("updated_at", disk_meta)
            self.assertIn("discovered_from", disk_meta)

            sidecar = json.loads(sidecar_path_for(graph_path).read_text())
            self.assertEqual(
                sidecar,
                {
                    "version": 1,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-06-12T00:00:00+00:00",
                },
            )

    def test_non_canonical_target_keeps_full_meta_no_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            weld_dir = Path(d) / ".weld"
            weld_dir.mkdir()
            export = weld_dir / "export.json"
            write_graph_with_meta(export, _graph())
            meta = json.loads(export.read_text())["meta"]
            self.assertIn("git_sha", meta)
            self.assertIn("updated_at", meta)
            self.assertFalse((weld_dir / "graph-meta.json").exists())

    def test_no_sidecar_written_when_no_volatile_payload(self) -> None:
        graph = {"meta": {"version": 5}, "nodes": {}, "edges": []}
        with tempfile.TemporaryDirectory() as d:
            weld_dir = Path(d) / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            write_graph_with_meta(graph_path, graph)
            self.assertFalse(sidecar_path_for(graph_path).exists())


class MergeSidecarMetaTest(unittest.TestCase):
    def test_sidecar_value_wins(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            weld_dir = Path(d) / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            write_graph_with_meta(graph_path, _graph())
            # In-graph meta has no volatile keys; sidecar supplies them.
            disk_meta = json.loads(graph_path.read_text())["meta"]
            merged = merge_sidecar_meta(disk_meta, graph_path)
            self.assertEqual(merged["git_sha"], "deadbeef")
            self.assertEqual(merged["updated_at"], "2026-06-12T00:00:00+00:00")

    def test_legacy_in_graph_fallback_when_no_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            weld_dir = Path(d) / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            # No sidecar on disk; legacy graph carries volatile in-graph.
            legacy_meta = {
                "version": 5,
                "git_sha": "legacysha",
                "updated_at": "legacyts",
                "discovered_from": ["weld/"],
            }
            merged = merge_sidecar_meta(legacy_meta, graph_path)
            self.assertEqual(merged["git_sha"], "legacysha")
            self.assertEqual(merged["updated_at"], "legacyts")

    def test_fresh_checkout_no_sidecar_no_in_graph_leaves_absent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            weld_dir = Path(d) / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            merged = merge_sidecar_meta(
                {"version": 5, "discovered_from": ["weld/"]}, graph_path
            )
            self.assertNotIn("git_sha", merged)
            self.assertNotIn("updated_at", merged)

    def test_does_not_mutate_input(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            weld_dir = Path(d) / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            write_graph_with_meta(graph_path, _graph())
            base = {"version": 5}
            merge_sidecar_meta(base, graph_path)
            self.assertEqual(base, {"version": 5})


class ReadSidecarMetaTest(unittest.TestCase):
    def test_missing_sidecar_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(read_sidecar_meta(Path(d) / "graph.json"), {})

    def test_malformed_sidecar_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            graph_path = Path(d) / "graph.json"
            sidecar_path_for(graph_path).write_text("{not json", encoding="utf-8")
            self.assertEqual(read_sidecar_meta(graph_path), {})

    def test_only_recognised_keys_returned(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            graph_path = Path(d) / "graph.json"
            sidecar_path_for(graph_path).write_text(
                json.dumps(
                    {"version": 1, "git_sha": "s", "updated_at": "t", "junk": 9}
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                read_sidecar_meta(graph_path), {"git_sha": "s", "updated_at": "t"}
            )


class LoadGraphMetaTest(unittest.TestCase):
    def test_merges_sidecar_for_direct_readers(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            weld_dir = Path(d) / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            write_graph_with_meta(graph_path, _graph())
            meta = load_graph_meta(graph_path)
            self.assertEqual(meta["git_sha"], "deadbeef")
            self.assertEqual(meta["version"], 5)
            self.assertIn("discovered_from", meta)

    def test_missing_graph_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_graph_meta(Path(d) / "graph.json"), {})


class GraphLoadSaveRoundTripTest(unittest.TestCase):
    """Graph.save -> Graph.load preserves volatile meta via the sidecar."""

    def test_save_strips_graph_json_load_restores_meta(self) -> None:
        from weld.graph import Graph

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".weld").mkdir()
            g = Graph(root)
            g.load()
            g.add_node("n:1", "file", "L", {})
            g._data["meta"]["git_sha"] = "abc123"  # noqa: SLF001 -- test setup
            g.save()

            graph_path = root / ".weld" / "graph.json"
            on_disk = json.loads(graph_path.read_text())["meta"]
            self.assertNotIn("git_sha", on_disk)
            self.assertNotIn("updated_at", on_disk)
            self.assertTrue((root / ".weld" / "graph-meta.json").exists())

            g2 = Graph(root)
            g2.load()
            self.assertEqual(g2.dump()["meta"].get("git_sha"), "abc123")
            self.assertIn("updated_at", g2.dump()["meta"])

    def test_load_legacy_graph_without_sidecar(self) -> None:
        """A graph written by an older weld (volatile in-graph, no sidecar)
        loads with identical meta -- the migration fallback."""
        from weld.graph import Graph
        from weld.serializer import dumps_graph

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".weld").mkdir()
            legacy = {
                "meta": {
                    "version": 5,
                    "updated_at": "legacyts",
                    "git_sha": "legacysha",
                    "discovered_from": ["weld/"],
                    "schema_version": 1,
                },
                "nodes": {"n:1": {"type": "file", "label": "L", "props": {}}},
                "edges": [],
            }
            (root / ".weld" / "graph.json").write_text(
                dumps_graph(legacy), encoding="utf-8"
            )
            g = Graph(root)
            g.load()
            self.assertEqual(g.dump()["meta"]["git_sha"], "legacysha")
            self.assertEqual(g.dump()["meta"]["updated_at"], "legacyts")


if __name__ == "__main__":
    unittest.main()
