"""``load_graph_file`` must classify a malformed ``graph.json``, not crash.

Split out of ``weld_schema_version_test.py`` (which pins the
``meta.schema_version`` / ``repo`` node type contract, a distinct concern)
so each file stays under the line-count cap. Covers two related gaps found
in the single-repo / ``Graph.load`` surface:

* bd 5038-1c7o: a syntactically valid JSON object missing ``nodes``/
  ``edges``, or holding the wrong type for either (e.g. ``{"meta": {...}}``
  alone), reached ``Graph._build_inverted_index`` and raised an uncaught
  ``KeyError`` instead of the classifiable failure every other
  malformed-graph case produces.
* bd 5038-w0r4: a bare list/scalar top-level payload (``[]``, ``42``,
  ``"oops"``) parses fine via ``json.loads`` but reached
  ``data.get("meta")`` and raised an uncaught ``AttributeError`` --
  ``load_graph_file`` had no top-level-payload-is-a-dict check, unlike
  ``weld.federation_support.load_graph_bytes``.

Both gaps are closed by the same shared validators
(:func:`weld._graph_schema.validate_dict_payload`,
:func:`weld._graph_schema.validate_graph_shape`) that
:func:`weld.federation_support.load_graph_bytes` (the federated child-load
surface) also calls, so the two surfaces cannot drift apart again.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph, GraphShapeError, load_graph_file  # noqa: E402


_TS = "2026-04-02T12:00:00+00:00"


class LoadGraphFileRejectsMalformedShapeTest(unittest.TestCase):
    """A structurally incomplete but syntactically valid ``graph.json`` must
    classify, not crash (bd 5038-1c7o, bd 5038-w0r4).

    ``load_graph_file`` validated UTF-8 decode / JSON parse /
    ``meta.schema_version`` but not that the top level is a dict, nor that
    ``nodes`` is a dict or ``edges`` is a list -- so ``{"meta": {...}}``
    alone (or a hand-edited file with the wrong type for either key)
    reached ``Graph._build_inverted_index`` and raised an uncaught
    ``KeyError`` (bd 5038-1c7o), and a bare list/scalar top level (``[]``,
    ``42``) raised an uncaught ``AttributeError`` from ``data.get("meta")``
    before ever reaching that check (bd 5038-w0r4) -- instead of the
    classifiable failure every other malformed-graph case already
    produces. This is the single-repo / ``Graph.load`` twin of the same gap
    in :func:`weld.federation_support.load_graph_bytes` (the federated
    child-load surface); both call the shared
    :func:`weld._graph_schema.validate_dict_payload` and
    :func:`weld._graph_schema.validate_graph_shape` so the two surfaces
    cannot drift apart.
    """

    def _write(self, tmp: Path, payload: dict, *, under_weld: bool = False) -> Path:
        path = (tmp / ".weld" / "graph.json") if under_weld else (tmp / "graph.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_graph_file_rejects_missing_nodes_key(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="weld-shape-"))
        path = self._write(tmp, {"meta": {"version": SCHEMA_VERSION, "updated_at": _TS}})

        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_file(path)
        self.assertIn("nodes", str(ctx.exception))

    def test_load_graph_file_rejects_wrong_type_nodes(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="weld-shape-"))
        path = self._write(tmp, {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS}, "nodes": [], "edges": [],
        })

        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_file(path)
        self.assertIn("nodes", str(ctx.exception))
        self.assertIn("list", str(ctx.exception))

    def test_load_graph_file_rejects_missing_edges_key(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="weld-shape-"))
        path = self._write(tmp, {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS}, "nodes": {},
        })

        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_file(path)
        self.assertIn("edges", str(ctx.exception))

    def test_load_graph_file_rejects_wrong_type_edges(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="weld-shape-"))
        path = self._write(tmp, {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS}, "nodes": {}, "edges": {},
        })

        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_file(path)
        self.assertIn("edges", str(ctx.exception))
        self.assertIn("dict", str(ctx.exception))

    def test_load_graph_file_rejects_bare_list_payload(self) -> None:
        """bd 5038-w0r4: a bare-list top-level payload used to crash.

        ``[]``/``[1, 2, 3]`` parses fine via ``json.loads`` but ``list`` has
        no ``.get`` -- ``data.get("meta")`` raised an uncaught
        ``AttributeError`` instead of classifying as ``graph_corrupt`` like
        every other malformed-graph.json case.
        """
        tmp = Path(tempfile.mkdtemp(prefix="weld-shape-"))
        path = tmp / "graph.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_file(path)
        self.assertIn("JSON object", str(ctx.exception))
        self.assertIn("list", str(ctx.exception))

    def test_load_graph_file_rejects_scalar_payload(self) -> None:
        """bd 5038-w0r4: a bare-scalar top-level payload used to crash.

        ``42``/``"oops"`` parses fine via ``json.loads`` but ``int``/``str``
        has no ``.get`` either -- same ``AttributeError`` crash as the
        bare-list case.
        """
        tmp = Path(tempfile.mkdtemp(prefix="weld-shape-"))
        path = tmp / "graph.json"
        path.write_text(json.dumps(42), encoding="utf-8")

        with self.assertRaises(GraphShapeError) as ctx:
            load_graph_file(path)
        self.assertIn("JSON object", str(ctx.exception))
        self.assertIn("int", str(ctx.exception))

    def test_load_graph_file_accepts_valid_shape_unchanged(self) -> None:
        """No behavior change for a normal graph (regression guard)."""
        tmp = Path(tempfile.mkdtemp(prefix="weld-shape-"))
        payload = {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
            "nodes": {"file:a.py": {"type": "file", "label": "a", "props": {}}},
            "edges": [],
        }
        path = self._write(tmp, payload)

        data = load_graph_file(path)
        self.assertEqual(data["nodes"], payload["nodes"])
        self.assertEqual(data["edges"], [])

    def test_graph_load_raises_graph_shape_error_not_key_error(self) -> None:
        """The reported bug, reproduced through the public ``Graph`` API.

        Before the fix this raised a bare ``KeyError('nodes')`` from
        ``Graph._build_inverted_index`` instead of the ``graph_corrupt``
        contract. ``GraphShapeError`` is a ``ValueError`` (never a
        ``KeyError``) precisely so the CLI/MCP structured-error guards'
        existing exception handling catches it.
        """
        tmp = Path(tempfile.mkdtemp(prefix="weld-shape-"))
        self._write(tmp, {"meta": {"version": SCHEMA_VERSION, "updated_at": _TS}}, under_weld=True)

        with self.assertRaises(GraphShapeError):
            Graph(tmp).load()


if __name__ == "__main__":
    unittest.main()
