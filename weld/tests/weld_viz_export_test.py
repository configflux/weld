"""Tests for the "Export view" feature in ``wd viz`` (bd h6z0.14).

Two surfaces are covered here:

* The ``/api/export`` HTTP route, which wraps :func:`weld.export.export`
  with an allowlist (mermaid/dot/d2) and ships a ``Content-Disposition``
  filename including the focused node id.
* The static UI -- the topbar Export menu, the JS dispatcher, and the
  CSS chrome -- read directly from the packaged static assets so the
  wiring is verified without a browser.

Split out of ``weld_viz_test.py`` / ``weld_viz_static_test.py`` to keep
both under the 400-line cap (CLAUDE.md "Line-Count Policy").
"""

from __future__ import annotations

import json
import threading
import unittest
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import urlopen

from weld.contract import SCHEMA_VERSION
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
    nodes = {
        "service:api": {"type": "service", "label": "api", "props": {"file": "src/api.py"}},
        "route:GET:/stores": {"type": "route", "label": "GET /stores", "props": {}},
        "entity:Store": {"type": "entity", "label": "Store", "props": {}},
        "symbol:helper": {"type": "symbol", "label": "helper", "props": {}},
    }
    edges = [
        {"from": "service:api", "to": "route:GET:/stores", "type": "exposes", "props": {}},
        {"from": "route:GET:/stores", "to": "entity:Store", "type": "responds_with", "props": {}},
        {"from": "symbol:helper", "to": "entity:Store", "type": "calls", "props": {}},
    ]
    _write_graph(root, _graph_payload(nodes, edges))
    return tmp


class VizExportApiTest(unittest.TestCase):
    """The ``/api/export`` HTTP route returns the right artefact per format."""

    def _with_server(self, root: Path):
        server = make_server(str(root), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_mermaid_returns_text_with_filename(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            with urlopen(f"{base}/api/export?format=mermaid", timeout=5) as resp:
                body = resp.read().decode("utf-8")
                ctype = resp.headers.get("Content-Type", "")
                disposition = resp.headers.get("Content-Disposition", "")
        self.assertTrue(body.startswith("flowchart LR"))
        self.assertIn("text/plain", ctype)
        # Filename uses the .mmd extension for mermaid (the conventional
        # mermaid extension) so editors pick the right syntax.
        self.assertIn("weld-graph", disposition)
        self.assertIn(".mmd", disposition)

    def test_dot_returns_text_with_dot_extension(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            with urlopen(f"{base}/api/export?format=dot", timeout=5) as resp:
                body = resp.read().decode("utf-8")
                disposition = resp.headers.get("Content-Disposition", "")
        self.assertTrue(body.startswith("digraph"))
        self.assertIn(".dot", disposition)

    def test_d2_returns_text_with_d2_extension(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            with urlopen(f"{base}/api/export?format=d2", timeout=5) as resp:
                body = resp.read().decode("utf-8")
                disposition = resp.headers.get("Content-Disposition", "")
        # D2 has no header line, just identifier rows.
        self.assertIn(":", body)
        self.assertIn(".d2", disposition)

    def test_filename_includes_focused_node_id(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            url = (
                f"{base}/api/export?format=mermaid"
                "&node_id=service%3Aapi&depth=1"
            )
            with urlopen(url, timeout=5) as resp:
                disposition = resp.headers.get("Content-Disposition", "")
                body = resp.read().decode("utf-8")
        # The node id is sanitized but recognizable in the filename so
        # the user can tell which slice the file represents.
        self.assertIn("service", disposition)
        self.assertIn("api", disposition)
        # Body is the subgraph -- contains the focused id's safe form.
        self.assertIn("service_api", body)

    def test_unknown_format_returns_400(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            with self.assertRaises(HTTPError) as cm:
                urlopen(f"{base}/api/export?format=svg", timeout=5)
        self.assertEqual(cm.exception.code, 400)
        # Body is a JSON error -- the allowlist rejects unknown formats
        # *before* delegating to weld.export so the error message is
        # consistent regardless of weld.export internals.
        payload = json.loads(cm.exception.read())
        self.assertIn("error", payload)

    def test_missing_format_returns_400(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            with self.assertRaises(HTTPError) as cm:
                urlopen(f"{base}/api/export", timeout=5)
        self.assertEqual(cm.exception.code, 400)

    def test_wiki_format_rejected_even_though_export_supports_it(self) -> None:
        # weld.export accepts a "wiki" multi-file format but it requires
        # an output directory and is not appropriate for the HTTP
        # endpoint. The allowlist must reject it.
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            with self.assertRaises(HTTPError) as cm:
                urlopen(f"{base}/api/export?format=wiki", timeout=5)
        self.assertEqual(cm.exception.code, 400)


class VizExportMenuStaticTest(unittest.TestCase):
    """The topbar Export menu UI ships the right skeleton + JS wiring."""

    def _read_static(self, name: str) -> str:
        return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")

    def test_index_html_carries_export_menu_skeleton(self) -> None:
        html = self._read_static("index.html")
        # The Export button + menu live inside the topbar toolbar so they
        # sit next to fit/layout/clear (existing toolbar chrome).
        toolbar_start = html.index('class="toolbar"')
        toolbar_end = html.index("</div>\n      </header>", toolbar_start)
        toolbar_block = html[toolbar_start:toolbar_end]
        # Trigger button reveals the menu.
        self.assertIn('id="export-button"', toolbar_block)
        # Menu container holds the five items (mermaid / dot / d2 /
        # png / json). Hidden by default so the menu collapses unless
        # explicitly opened.
        self.assertIn('id="export-menu"', toolbar_block)
        # Each format ships its own button with a data-format attribute
        # so the delegated handler can resolve which item was picked.
        for fmt in ("mermaid", "dot", "d2", "png", "json"):
            self.assertIn(f'data-format="{fmt}"', toolbar_block)

    def test_app_js_wires_export_menu_with_node_id_in_filename(self) -> None:
        js = self._read_static("app.js")
        # The delegated handler resolves clicks on .export-menu items
        # by reading their data-format attribute and dispatching.
        self.assertIn("export-menu", js)
        self.assertIn("data-format", js)
        # Server-side formats hit /api/export with node_id + depth so
        # the artefact matches the focused slice on screen.
        self.assertIn("/api/export", js)
        # PNG uses cytoscape's built-in raster export.
        self.assertIn("cy.png(", js)
        # JSON dumps the last slice payload so the user gets the exact
        # graph the canvas is showing (filters, depth, scope applied).
        self.assertIn("state.lastSlice", js)
        # Filenames include the focused node id so a user with multiple
        # exports can tell them apart. The helper that builds the
        # filename is a single dispatch point.
        self.assertIn("exportFilename", js)

    def test_app_js_routes_each_format_through_one_dispatcher(self) -> None:
        js = self._read_static("app.js")
        # All five formats are handled by a single dispatcher (the
        # delegated menu click handler) so adding a new format only
        # touches one place.
        start = js.index("function handleExport(")
        end = js.index("\n}\n", start)
        body = js[start:end]
        # Each format appears in the dispatcher (case branch / lookup).
        for fmt in ("mermaid", "dot", "d2", "png", "json"):
            self.assertIn(fmt, body)

    def test_styles_css_defines_export_menu_chrome(self) -> None:
        css = self._read_static("styles.css")
        # Dropdown chrome anchors below the export button.
        self.assertIn(".export-menu", css)


if __name__ == "__main__":
    unittest.main()
