"""Local read-only HTTP server for ``wd viz``."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urlparse

from weld.viz.adapter import (
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    HARD_MAX_EDGES,
    HARD_MAX_NODES,
    clamp_limit,
)
from weld.viz.api import VizApi


def make_server(
    root: str,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    """Create a configured visualizer HTTP server."""
    api = VizApi(root)
    handler_cls = _handler_for(api)
    return ThreadingHTTPServer((host, port), handler_cls)


def serve(
    root: str = ".",
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> int:
    """Serve the visualizer until interrupted."""
    httpd = make_server(root, host=host, port=port)
    actual_host, actual_port = httpd.server_address
    url = f"http://{actual_host}:{actual_port}/"
    print(f"Weld graph visualizer: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nWeld graph visualizer stopped.", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wd viz",
        description="Serve a local read-only browser visualizer for .weld/graph.json.",
    )
    parser.add_argument("--root", default=".", help="Project root directory")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=0, help="Bind port; 0 chooses a free port")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser")
    args = parser.parse_args(argv)
    return serve(
        args.root,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
    )


def _handler_for(api: VizApi) -> type[BaseHTTPRequestHandler]:
    class VizRequestHandler(BaseHTTPRequestHandler):
        server_version = "WeldViz/1"

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle(send_body=False)

        def do_GET(self) -> None:  # noqa: N802
            self._handle(send_body=True)

        def do_POST(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _handle(self, *, send_body: bool) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if _has_path_traversal(path):
                self._send_json({"error": "path traversal rejected"}, HTTPStatus.BAD_REQUEST, send_body)
                return
            if path.startswith("/api/"):
                self._handle_api(path, parsed.query, send_body)
                return
            self._handle_static(path, send_body)

        def _handle_api(self, path: str, query: str, send_body: bool) -> None:
            params = _params(query)
            try:
                if path == "/api/summary":
                    payload = api.summary()
                elif path == "/api/slice":
                    payload = api.slice(params)
                elif path == "/api/context":
                    payload = api.context(params)
                elif path == "/api/path":
                    payload = api.path(params)
                elif path == "/api/trace":
                    payload = api.trace(params)
                else:
                    self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND, send_body)
                    return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST, send_body)
                return
            self._send_json(payload, HTTPStatus.OK, send_body)

        def _handle_static(self, path: str, send_body: bool) -> None:
            rel_path = "index.html" if path in ("", "/") else path.lstrip("/")
            resource = files("weld.viz").joinpath("static", *PurePosixPath(rel_path).parts)
            if not resource.is_file():
                self._send_bytes(b"not found\n", "text/plain; charset=utf-8",
                                 HTTPStatus.NOT_FOUND, send_body)
                return
            ctype = mimetypes.guess_type(rel_path)[0] or "application/octet-stream"
            self._send_bytes(resource.read_bytes(), ctype, HTTPStatus.OK, send_body)

        def _method_not_allowed(self) -> None:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send_json(self, payload: dict, status: HTTPStatus, send_body: bool) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
            self._send_bytes(body, "application/json; charset=utf-8", status, send_body)

        def _send_bytes(
            self,
            body: bytes,
            content_type: str,
            status: HTTPStatus,
            send_body: bool,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body) if send_body else 0))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

    return VizRequestHandler


def _params(query: str) -> dict:
    raw = {key: values[-1] for key, values in parse_qs(query).items() if values}
    raw["max_nodes"] = clamp_limit(raw.get("max_nodes"), DEFAULT_MAX_NODES, HARD_MAX_NODES)
    raw["max_edges"] = clamp_limit(raw.get("max_edges"), DEFAULT_MAX_EDGES, HARD_MAX_EDGES)
    return raw


def _has_path_traversal(path: str) -> bool:
    return ".." in PurePosixPath(path.lstrip("/")).parts


if __name__ == "__main__":
    raise SystemExit(main())
