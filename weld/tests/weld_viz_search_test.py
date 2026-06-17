"""Tests for the search-that-does-not-lie improvements (bd h6z0.8).

Covers three sub-improvements that ship as one task:

1. Substring fallback in ``/api/slice`` when ``graph.query`` yields zero
   matches.
2. ``/api/search-suggest`` returning ``[{id, label, type}]`` for the
   dropdown under the search input.
3. Empty-state hint (top-N most-connected nodes) returned by the same
   endpoint when ``q`` is empty.

Split out of ``weld_viz_test.py`` to keep that file under the 400-line
default cap (see CLAUDE.md "Line-Count Policy"). Responsibility split
is intentional: search-suggest is a cohesive new feature with its own
helpers (``weld/viz/_search.py``) and its own HTTP surface.
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import urlopen

from weld.contract import SCHEMA_VERSION
from weld.viz.api import VizApi
from weld.viz.server import make_server

_TS = "2026-04-16T19:30:00+00:00"


def _graph_payload(nodes: dict, edges: list[dict] | None = None) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": 1},
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_graph(root: Path, payload: dict) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _simple_root() -> TemporaryDirectory:
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    # Every node is origin:project so the empty-state hint (which now
    # applies the overview's project-only origin filter, bd 123p) keeps
    # them. Without an origin tag they would classify as 'unresolved'
    # and be dropped, defeating these fixtures.
    nodes = {
        "service:api": {"type": "service", "label": "api", "props": {"file": "src/api.py", "origin": "project"}},
        "route:GET:/stores": {"type": "route", "label": "GET /stores", "props": {"origin": "project"}},
        "entity:Store": {"type": "entity", "label": "Store", "props": {"origin": "project"}},
        "symbol:helper": {"type": "symbol", "label": "helper", "props": {"origin": "project"}},
    }
    edges = [
        {"from": "service:api", "to": "route:GET:/stores", "type": "exposes", "props": {}},
        {"from": "route:GET:/stores", "to": "entity:Store", "type": "responds_with", "props": {}},
        {"from": "symbol:helper", "to": "entity:Store", "type": "calls", "props": {}},
    ]
    _write_graph(root, _graph_payload(nodes, edges))
    return tmp


class SubstringFallbackTest(unittest.TestCase):
    """``/api/slice`` substring fallback when tokenized query misses."""

    def test_fallback_fires_when_tokenizer_misses(self) -> None:
        # The tokenizer already does substring matching across many
        # fields; the fallback is a safety net for the edge cases
        # where strict-AND can't reconcile every token. To exercise
        # the fallback path deterministically we monkeypatch
        # Graph.query to return zero matches, simulating the
        # production failure mode.
        from weld.graph import Graph
        with _simple_root() as tmp:
            api = VizApi(tmp)
            with patch.object(
                Graph,
                "query",
                return_value={"query": "x", "matches": [], "neighbors": [], "edges": []},
            ):
                payload = api.slice({"q": "stores", "max_nodes": 10})
            ids = {node["data"]["id"] for node in payload["elements"]["nodes"]}
            # "stores" is a substring of "route:GET:/stores" (id). The
            # substring fallback finds it via id/label substring scan
            # even though Graph.query was stubbed to return zero.
            self.assertIn("route:GET:/stores", ids)
            warnings = " ".join(payload.get("warnings", []) or [])
            self.assertIn("substring", warnings.lower())

    def test_fallback_empty_when_no_match(self) -> None:
        with _simple_root() as tmp:
            payload = VizApi(tmp).slice({"q": "nonexistent-zzzz", "max_nodes": 10})
        # Empty result is still a valid response (not an error).
        self.assertEqual(payload["elements"]["nodes"], [])


class SearchSuggestApiTest(unittest.TestCase):
    """``VizApi.search_suggest`` behavior."""

    def test_substring_orders_label_hits_above_id_only(self) -> None:
        # /api/search-suggest powers the dropdown under the search
        # input. Label matches outrank id-only matches; within each
        # band shorter labels rank higher.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({
                "symbol:py:weld.viz:VizApi": {"type": "symbol", "label": "VizApi", "props": {}},
                "symbol:py:weld.viz:VizApiAdapter": {"type": "symbol", "label": "VizApiAdapter", "props": {}},
                "symbol:py:weld.viz:OtherThing": {"type": "symbol", "label": "OtherThing", "props": {}},
            }))
            payload = VizApi(root).search_suggest({"q": "viz", "limit": 10})
            suggestions = payload["suggestions"]
            self.assertEqual(
                [s["id"] for s in suggestions],
                [
                    "symbol:py:weld.viz:VizApi",
                    "symbol:py:weld.viz:VizApiAdapter",
                    "symbol:py:weld.viz:OtherThing",
                ],
            )
            self.assertEqual(set(suggestions[0].keys()), {"id", "label", "type"})

    def test_empty_query_returns_project_surfaces_by_overview_key(self) -> None:
        # When q is empty, suggest returns the empty-state hint: the
        # overview's default origin filter (project-only) applied, then
        # ranked by the same overview key the graph view uses
        # (node-type priority, then descending degree, then id). In
        # _simple_root every node is origin:project so all four survive
        # the filter; service:api (priority 1) outranks route (4),
        # entity (5) and symbol (12) -- the project-surface preference,
        # NOT raw degree (entity:Store has the highest degree but is a
        # lower-priority surface than the service).
        with _simple_root() as tmp:
            payload = VizApi(tmp).search_suggest({"q": "", "limit": 5})
        suggestions = payload["suggestions"]
        self.assertTrue(suggestions, "expected at least one suggestion")
        self.assertEqual(
            [s["id"] for s in suggestions],
            ["service:api", "route:GET:/stores", "entity:Store", "symbol:helper"],
        )

    def test_empty_query_drops_stdlib_and_unresolved_hubs(self) -> None:
        # Regression for bd 123p: the empty-state / q='' hint must apply
        # the overview's default origin filter (drop unresolved / stdlib
        # / external) and prefer real project surfaces, even when the
        # noisy hubs have the highest raw degree. Before the fix the top
        # suggestions for weld's own graph were pathlib:Path (stdlib),
        # get / assertEqual / append (unresolved test-assertion hubs),
        # and __future__ (stdlib) -- the worst possible orientation.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            nodes = {
                # Noisy, HIGH-degree non-project hubs (must be excluded).
                "symbol:py:pathlib:Path": {
                    "type": "symbol", "label": "Path",
                    "props": {"origin": "stdlib"},
                },
                "symbol:unresolved:assertEqual": {
                    "type": "symbol", "label": "assertEqual",
                    "props": {"origin": "unresolved"},
                },
                "package:python:__future__": {
                    "type": "package", "label": "__future__",
                    "props": {"origin": "external"},
                },
                # Real project surfaces (must be preferred).
                "command:wd:discover": {
                    "type": "command", "label": "wd discover",
                    "props": {"origin": "project", "file": "weld/cli.py"},
                },
                "package:python:weld.viz": {
                    "type": "package", "label": "weld.viz",
                    "props": {"origin": "project", "file": "weld/viz/__init__.py"},
                },
                # A LOW-priority project symbol that nonetheless has the
                # single highest raw degree -- it must still rank BELOW
                # the project package/command (project-surface preference,
                # not raw degree).
                "symbol:py:weld.viz:helper": {
                    "type": "symbol", "label": "helper",
                    "props": {"origin": "project", "file": "weld/viz/_x.py"},
                },
            }
            # Wire degree so the EXCLUDED hubs and the project symbol all
            # out-rank the project package/command on raw degree. The
            # symbol gets the most edges of all.
            edges = [
                {"from": "symbol:py:pathlib:Path", "to": "symbol:py:weld.viz:helper", "type": "calls", "props": {}},
                {"from": "symbol:unresolved:assertEqual", "to": "symbol:py:weld.viz:helper", "type": "calls", "props": {}},
                {"from": "package:python:__future__", "to": "symbol:py:weld.viz:helper", "type": "imports", "props": {}},
                {"from": "command:wd:discover", "to": "symbol:py:weld.viz:helper", "type": "calls", "props": {}},
                {"from": "package:python:weld.viz", "to": "symbol:py:weld.viz:helper", "type": "contains", "props": {}},
            ]
            _write_graph(root, _graph_payload(nodes, edges))
            payload = VizApi(root).search_suggest({"q": "", "limit": 5})
            ids = [s["id"] for s in payload["suggestions"]]

        # AC1 + AC2: no stdlib / external / unresolved node appears.
        self.assertNotIn("symbol:py:pathlib:Path", ids)
        self.assertNotIn("symbol:unresolved:assertEqual", ids)
        self.assertNotIn("package:python:__future__", ids)
        # The project package and command (real surfaces) are suggested.
        self.assertIn("package:python:weld.viz", ids)
        self.assertIn("command:wd:discover", ids)
        # Project-surface preference: the package/command outrank the
        # higher-degree project symbol despite its larger raw degree.
        self.assertLess(ids.index("package:python:weld.viz"), ids.index("symbol:py:weld.viz:helper"))
        self.assertLess(ids.index("command:wd:discover"), ids.index("symbol:py:weld.viz:helper"))

    def test_limit_is_clamped(self) -> None:
        with _simple_root() as tmp:
            api = VizApi(tmp)
            payload = api.search_suggest({"q": "store", "limit": 0})
            # limit=0 falls back to the default; payload should not raise.
            self.assertIn("suggestions", payload)
            payload = api.search_suggest({"q": "store", "limit": 999})
            # Excessive limit is bounded by the hard cap (50).
            self.assertLessEqual(len(payload["suggestions"]), 50)


class SearchSuggestHttpTest(unittest.TestCase):
    """``/api/search-suggest`` HTTP wiring."""

    def _with_server(self, root: Path):
        server = make_server(str(root), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_endpoint_returns_documented_shape(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            payload = json.loads(
                urlopen(f"{base}/api/search-suggest?q=store&limit=10", timeout=5).read()
            )
            self.assertIn("suggestions", payload)
            ids = {item["id"] for item in payload["suggestions"]}
            self.assertIn("entity:Store", ids)
            # Empty query path also works over HTTP.
            empty = json.loads(
                urlopen(f"{base}/api/search-suggest?q=&limit=5", timeout=5).read()
            )
            self.assertIn("suggestions", empty)
            self.assertTrue(empty["suggestions"])


if __name__ == "__main__":
    unittest.main()
