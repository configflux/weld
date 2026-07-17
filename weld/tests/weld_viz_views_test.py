"""Tests for the "Saved views" feature in ``wd viz`` (bd h6z0.17, ADR 0092).

This is the viz server's first write surface, so the server half is the
security-critical part and is covered thoroughly here:

* ``/api/views`` GET/POST/DELETE behaviour with the opt-in flag on AND off
  (off => the whole route is ``403`` and nothing is written).
* Input validation, caps, single-file confinement, crafted-name traversal
  attempts, oversized-body refusal, and corrupt-file tolerance.

The browser half (the topbar dropdown JS/UI) is not executable by the headless
gate, so it is pinned by lexical assertions on the packaged static assets --
mirroring ``weld_viz_export_test.py``.
"""

from __future__ import annotations

import http.client
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.contract import SCHEMA_VERSION
from weld.viz._views import (
    MAX_BODY_BYTES,
    MAX_NAME_LEN,
    MAX_VIEWS,
    ViewsError,
    load_views,
    read_capped_body,
    save_view,
    views_path,
)
from weld.viz.server import make_server

_TS = "2026-05-19T07:47:14+00:00"


def _write_graph(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": 1},
        "nodes": {"service:api": {"type": "service", "label": "api", "props": {}}},
        "edges": [],
    }
    (root / ".weld" / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def _request(base: str, path: str, method: str = "GET", body: object = None):
    """Issue an HTTP request; return ``(status, parsed_json)``."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class _ServerCase(unittest.TestCase):
    """Shared server spin-up. Subclasses set ``enable`` to opt in or not."""

    enable = False

    def _serve(self, root: Path) -> str:
        server = make_server(
            str(root), host="127.0.0.1", port=0, enable_saved_views=self.enable,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"


class VizViewsDisabledTest(_ServerCase):
    """With the flag off the whole route is refused and nothing is written."""

    enable = False

    def test_get_post_delete_all_forbidden(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)
            get_status, get_body = _request(base, "/api/views")
            post_status, _ = _request(
                base, "/api/views", "POST", {"name": "x", "hash": "#q=api"},
            )
            del_status, _ = _request(base, "/api/views?name=x", "DELETE")
            self.assertEqual(get_status, 403)
            self.assertIn("error", get_body)
            self.assertEqual(post_status, 403)
            self.assertEqual(del_status, 403)
            # The refusal must not have created the file.
            self.assertFalse(views_path(root).exists())

    def test_other_paths_still_405_not_403(self) -> None:
        # POST/DELETE to a non-views path stays method-not-allowed, so the flag
        # gate does not widen refusal semantics for the rest of the surface.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)
            status, _ = _request(base, "/api/slice", "POST", {"a": 1})
            self.assertEqual(status, 405)


class VizViewsEnabledCrudTest(_ServerCase):
    """The happy path: create, list, upsert, delete -- all persisted."""

    enable = True

    def test_full_lifecycle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)

            status, body = _request(base, "/api/views")
            self.assertEqual(status, 200)
            self.assertEqual(body, {"views": []})

            status, body = _request(
                base, "/api/views", "POST",
                {"name": "My View", "hash": "#scope=root&q=api"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["views"], [{"name": "My View", "hash": "#scope=root&q=api"}])
            # Persisted to exactly the one fixed file.
            self.assertTrue(views_path(root).exists())

            # Same name upserts in place rather than duplicating.
            status, body = _request(
                base, "/api/views", "POST", {"name": "My View", "hash": "#scope=all"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["views"], [{"name": "My View", "hash": "#scope=all"}])

            status, body = _request(base, "/api/views")
            self.assertEqual(body["views"], [{"name": "My View", "hash": "#scope=all"}])

            # Delete is by (URL-encoded) name and idempotent.
            status, body = _request(
                base, "/api/views?name=" + urllib.parse.quote("My View"), "DELETE",
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["views"], [])
            status, body = _request(
                base, "/api/views?name=" + urllib.parse.quote("My View"), "DELETE",
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["views"], [])

    def test_hash_is_opaque_roundtrip(self) -> None:
        # The server never parses the hash; a value with reserved characters
        # comes back byte-for-byte.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)
            opaque = "#scope=all&q=a%20b&node_types=service,route&pathA=x:y"
            _, body = _request(base, "/api/views", "POST", {"name": "v", "hash": opaque})
            self.assertEqual(body["views"][0]["hash"], opaque)

    def test_empty_hash_allowed(self) -> None:
        # The canonical/default view serializes to an empty hash; that must be
        # a legal saved value.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)
            status, body = _request(base, "/api/views", "POST", {"name": "home", "hash": ""})
            self.assertEqual(status, 200)
            self.assertEqual(body["views"], [{"name": "home", "hash": ""}])


class VizViewsValidationTest(_ServerCase):
    """Malformed / abusive inputs are rejected; the write stays confined."""

    enable = True

    def _post(self, base: str, body: object):
        return _request(base, "/api/views", "POST", body)

    def test_rejections(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)
            # Empty / whitespace name.
            self.assertEqual(self._post(base, {"name": "   ", "hash": "#x"})[0], 400)
            # Control characters in the name.
            self.assertEqual(self._post(base, {"name": "a\nb", "hash": "#x"})[0], 400)
            # Control characters in the hash.
            self.assertEqual(self._post(base, {"name": "ok", "hash": "a\x00b"})[0], 400)
            # Oversized name / hash.
            self.assertEqual(self._post(base, {"name": "n" * 500, "hash": "#x"})[0], 400)
            self.assertEqual(self._post(base, {"name": "ok", "hash": "#" + "a" * 5000})[0], 400)
            # Non-string fields.
            self.assertEqual(self._post(base, {"name": 5, "hash": "#x"})[0], 400)
            self.assertEqual(self._post(base, {"name": "ok", "hash": 5})[0], 400)
            # Non-object / non-JSON bodies.
            self.assertEqual(self._post(base, [1, 2, 3])[0], 400)
            # Nothing above should have created the file.
            self.assertFalse(views_path(root).exists())

    def test_missing_name_query_on_delete(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)
            status, _ = _request(base, "/api/views", "DELETE")
            self.assertEqual(status, 400)

    def test_crafted_name_cannot_traverse(self) -> None:
        # A crafted traversal name is stored *inside* the JSON (names never
        # touch a path), and no file is created outside the single fixed one.
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "proj"
            root.mkdir()
            _write_graph(root)
            base = self._serve(root)
            canary = parent / "escaped"
            status, body = self._post(base, {"name": "../../escaped", "hash": "#x"})
            self.assertEqual(status, 200)
            self.assertEqual(body["views"][0]["name"], "../../escaped")
            self.assertFalse(canary.exists())
            # Only the graph and the single views file exist under .weld -- no
            # stray temp files, no traversal target.
            names = sorted(p.name for p in (root / ".weld").iterdir())
            self.assertEqual(names, ["graph.json", "viz-views.json"])

    def test_view_count_capped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)
            for i in range(MAX_VIEWS):
                status, _ = self._post(base, {"name": f"v{i}", "hash": f"#n={i}"})
                self.assertEqual(status, 200)
            status, body = self._post(base, {"name": "one-too-many", "hash": "#x"})
            self.assertEqual(status, 409)
            self.assertIn("error", body)

    def test_oversized_body_refused_413(self) -> None:
        # Send a Content-Length over the cap with a tiny body: the server must
        # refuse on the header before reading, returning 413.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            base = self._serve(root)
            host = base[len("http://"):]
            conn = http.client.HTTPConnection(host, timeout=5)
            conn.putrequest("POST", "/api/views")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
            conn.endheaders()
            conn.send(b"{}")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 413)
            conn.close()
            self.assertFalse(views_path(root).exists())


class VizViewsModuleTest(unittest.TestCase):
    """Direct unit tests for the storage/validation helpers."""

    def test_load_tolerates_missing_and_corrupt(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(load_views(root), [])  # missing
            (root / ".weld").mkdir()
            views_path(root).write_text("{not json", encoding="utf-8")
            self.assertEqual(load_views(root), [])  # corrupt
            views_path(root).write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(load_views(root), [])  # not a list

    def test_load_drops_malformed_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            views_path(root).write_text(
                json.dumps(
                    [
                        {"name": "ok", "hash": "#x"},
                        {"name": "no-hash"},
                        {"hash": "#y"},
                        "not-a-dict",
                        {"name": 5, "hash": "#z"},
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_views(root), [{"name": "ok", "hash": "#x"}])

    def test_save_is_atomic_no_temp_leftovers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            save_view(root, "a", "#1")
            save_view(root, "b", "#2")
            leftovers = [p.name for p in (root / ".weld").iterdir()
                         if p.name.startswith(".viz-views.")]
            self.assertEqual(leftovers, [])

    def test_read_capped_body_enforces_limit(self) -> None:
        reads: list[int] = []

        def reader(n: int) -> bytes:
            reads.append(n)
            return b"x" * n

        self.assertEqual(read_capped_body("2", reader), b"xx")
        self.assertEqual(read_capped_body(None, reader), b"")
        with self.assertRaises(ViewsError) as cm:
            read_capped_body(str(MAX_BODY_BYTES + 1), reader)
        self.assertEqual(cm.exception.status, 413)
        with self.assertRaises(ViewsError):
            read_capped_body("not-a-number", reader)

    def test_name_length_boundary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            save_view(root, "n" * MAX_NAME_LEN, "#x")  # exactly at cap: ok
            with self.assertRaises(ViewsError):
                save_view(root, "n" * (MAX_NAME_LEN + 1), "#x")


class VizViewsStaticTest(unittest.TestCase):
    """Pin the (non-gate-executable) topbar UI wiring on the static assets."""

    def _read_static(self, name: str) -> str:
        return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")

    def test_index_html_carries_views_control(self) -> None:
        html = self._read_static("index.html")
        self.assertIn('id="views-wrap"', html)
        self.assertIn('id="views-button"', html)
        self.assertIn('id="views-menu"', html)
        self.assertIn('id="views-save"', html)
        # The whole wrap ships hidden; app.js reveals it only when the opt-in
        # route answers.
        wrap_start = html.index('id="views-wrap"')
        self.assertIn("hidden", html[wrap_start:wrap_start + 60])
        self.assertIn('id="icon-bookmark"', html)

    def test_app_js_wires_saved_views(self) -> None:
        js = self._read_static("app.js")
        # Enablement is discovered by probing the endpoint (not a summary flag).
        self.assertIn("initSavedViews", js)
        self.assertIn("/api/views", js)
        # CRUD helpers hit the right verbs.
        self.assertIn("saveCurrentView", js)
        self.assertIn("deleteSavedView", js)
        self.assertIn('method: "POST"', js)
        self.assertIn('method: "DELETE"', js)
        # Selecting a view reuses the h6z0.4 hash-state rehydration path.
        self.assertIn("applySavedView", js)
        self.assertIn("rehydrateFromHash", js)

    def test_app_js_renders_names_as_text_not_html(self) -> None:
        js = self._read_static("app.js")
        start = js.index("function renderViewsMenu(")
        end = js.index("\n}\n", start)
        body = js[start:end]
        # Names must be written via textContent so a crafted name is inert.
        self.assertIn("textContent", body)
        self.assertNotIn("innerHTML", body)

    def test_styles_css_defines_views_menu_chrome(self) -> None:
        css = self._read_static("styles.css")
        self.assertIn(".views-menu", css)


if __name__ == "__main__":
    unittest.main()
