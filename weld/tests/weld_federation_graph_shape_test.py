"""Federation graph-shape validation coverage (bd 5038-1c7o).

``weld.federation_support.load_graph_bytes`` validated UTF-8 decode / JSON
parse / top-level dict / ``meta.schema_version``, but not that the payload's
``nodes`` is a dict or ``edges`` is a list.
``weld.federation_child_loader.load_child_from_json`` calls
``load_graph_bytes`` inside a try/except that classifies a validation
failure as a :class:`~weld.federation_support.CorruptChild` sentinel -- but
a syntactically valid JSON object missing (or with the wrong type for)
``nodes``/``edges`` passed ``load_graph_bytes`` unrejected and then hit
``Graph._build_inverted_index()`` unguarded, raising an uncaught
``KeyError`` instead of classifying like every other malformed-child case.

``weld.federation_child_probe.probe_child_status`` (bd sk3c) deliberately
calls the same ``load_graph_bytes`` for its own classification, so it
inherits whatever validation depth ``load_graph_bytes`` has --
:class:`ProbeChildStatusMatchesLoadChildEquivalenceTest` pins that the two
stay in lockstep: the same malformed fixture must classify identically
through both call paths, so a future change that bypasses
``load_graph_bytes`` in one path (instead of deepening the one shared
:func:`weld._graph_schema.validate_graph_shape`) fails a test here.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._graph_schema import GraphShapeError
from weld.federation_child_loader import load_child_from_json
from weld.federation_child_probe import probe_child_status
from weld.federation_support import ChildGraphCache, CorruptChild, load_graph_bytes
from weld.graph import Graph
from weld.workspace import ChildEntry


def _make_child(root: Path, name: str, payload_text: str) -> Path:
    """Write *payload_text* as ``<name>/.weld/graph.json`` under a fake-git child.

    ``maybe_sentinel`` only checks ``(child_root / ".git").exists()`` -- it
    never shells out to git -- so a bare directory is sufficient and keeps
    these tests fast.
    """
    child_root = root / name
    (child_root / ".git").mkdir(parents=True)
    weld_dir = child_root / ".weld"
    weld_dir.mkdir()
    graph_path = weld_dir / "graph.json"
    graph_path.write_text(payload_text, encoding="utf-8")
    return graph_path


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


class LoadGraphBytesShapeValidationTest(unittest.TestCase):
    """Unit coverage for ``load_graph_bytes``'s ``nodes``/``edges`` shape gate."""

    def test_rejects_missing_nodes_key(self) -> None:
        raw = json.dumps({"meta": {"schema_version": 1}}).encode("utf-8")
        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_bytes(raw, graph_path=Path("graph.json"))
        self.assertIn("nodes", str(ctx.exception))

    def test_rejects_wrong_type_nodes(self) -> None:
        raw = json.dumps(
            {"meta": {"schema_version": 1}, "nodes": [], "edges": []}
        ).encode("utf-8")
        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_bytes(raw, graph_path=Path("graph.json"))
        self.assertIn("nodes", str(ctx.exception))

    def test_rejects_missing_edges_key(self) -> None:
        raw = json.dumps({"meta": {"schema_version": 1}, "nodes": {}}).encode("utf-8")
        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_bytes(raw, graph_path=Path("graph.json"))
        self.assertIn("edges", str(ctx.exception))

    def test_rejects_wrong_type_edges(self) -> None:
        raw = json.dumps(
            {"meta": {"schema_version": 1}, "nodes": {}, "edges": {}}
        ).encode("utf-8")
        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_bytes(raw, graph_path=Path("graph.json"))
        self.assertIn("edges", str(ctx.exception))

    def test_valid_payload_still_loads_unchanged(self) -> None:
        """No behavior change for a valid child graph (regression guard)."""
        payload = {
            "meta": {"schema_version": 1},
            "nodes": {"file:a.py": {"type": "file", "label": "a", "props": {}}},
            "edges": [],
        }
        raw = json.dumps(payload).encode("utf-8")
        data = load_graph_bytes(raw, graph_path=Path("graph.json"))
        self.assertEqual(data["nodes"], payload["nodes"])
        self.assertEqual(data["edges"], [])

    def test_graph_shape_error_is_a_value_error(self) -> None:
        # load_child_from_json / probe_child_status both catch ValueError;
        # GraphShapeError must stay a subclass so neither except tuple
        # needs to change to pick up the deepened validation.
        raw = json.dumps({"meta": {"schema_version": 1}}).encode("utf-8")
        with self.assertRaises(ValueError):
            load_graph_bytes(raw, graph_path=Path("graph.json"))


class LoadChildFromJsonClassifiesMalformedShapeTest(unittest.TestCase):
    """Red-first regression: ``load_child_from_json`` must not leak a raw ``KeyError``."""

    def test_meta_only_child_classifies_as_corrupt_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path = _make_child(
                root, "child", json.dumps({"meta": {"schema_version": 1}})
            )
            entry = ChildEntry(name="child", path="child")

            result = load_child_from_json(
                name="child", entry=entry, child_root=root / "child",
                graph_path=graph_path, graph_rel="child/.weld/graph.json",
                sentinel_cache={}, child_cache=ChildGraphCache(),
                read_bytes=_read_bytes,
            )

        self.assertIsInstance(result, CorruptChild)
        self.assertEqual(result.status, "corrupt")
        self.assertIn("nodes", result.error)
        self.assertNotIn("KeyError", result.error)

    def test_valid_child_still_loads_as_graph(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "meta": {"schema_version": 1},
                "nodes": {"file:a.py": {"type": "file", "label": "a", "props": {}}},
                "edges": [],
            }
            graph_path = _make_child(root, "child", json.dumps(payload))
            entry = ChildEntry(name="child", path="child")

            result = load_child_from_json(
                name="child", entry=entry, child_root=root / "child",
                graph_path=graph_path, graph_rel="child/.weld/graph.json",
                sentinel_cache={}, child_cache=ChildGraphCache(),
                read_bytes=_read_bytes,
            )

        self.assertIsInstance(result, Graph)
        self.assertIsNotNone(result.get_node("file:a.py"))


class ProbeChildStatusMatchesLoadChildEquivalenceTest(unittest.TestCase):
    """bd sk3c equivalence pinning.

    ``probe_child_status`` must classify a shape-malformed child the same
    way ``load_child_from_json`` does, over the *same* fixture -- proving
    the validation depth cannot silently drift apart, since both go through
    the one shared ``load_graph_bytes``.
    """

    def _classify_both(self, payload_text: str) -> tuple[str, str]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path = _make_child(root, "child", payload_text)
            entry = ChildEntry(name="child", path="child")

            loaded = load_child_from_json(
                name="child", entry=entry, child_root=root / "child",
                graph_path=graph_path, graph_rel="child/.weld/graph.json",
                sentinel_cache={}, child_cache=ChildGraphCache(),
                read_bytes=_read_bytes,
            )
            probed = probe_child_status(
                name="child", entry=entry, workspace_root=root,
                sentinel_cache={}, read_bytes=_read_bytes,
            )
        load_status = "corrupt" if isinstance(loaded, CorruptChild) else "present"
        return load_status, probed.status

    def test_meta_only_child_classifies_corrupt_on_both_paths(self) -> None:
        load_status, probe_status = self._classify_both(
            json.dumps({"meta": {"schema_version": 1}})
        )
        self.assertEqual(load_status, "corrupt")
        self.assertEqual(probe_status, "corrupt")

    def test_wrong_type_nodes_classifies_corrupt_on_both_paths(self) -> None:
        load_status, probe_status = self._classify_both(
            json.dumps({"meta": {"schema_version": 1}, "nodes": [], "edges": []})
        )
        self.assertEqual(load_status, "corrupt")
        self.assertEqual(probe_status, "corrupt")

    def test_valid_child_classifies_present_on_both_paths(self) -> None:
        payload = {
            "meta": {"schema_version": 1},
            "nodes": {"file:a.py": {"type": "file", "label": "a", "props": {}}},
            "edges": [],
        }
        load_status, probe_status = self._classify_both(json.dumps(payload))
        self.assertEqual(load_status, "present")
        self.assertEqual(probe_status, "present")


if __name__ == "__main__":
    unittest.main()
