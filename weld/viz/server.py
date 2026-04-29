"""Local read-only HTTP server for ``wd viz``."""

from __future__ import annotations

import argparse
import ipaddress
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

_LOOPBACK_HOSTNAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})


def make_server(
    root: str,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    graph_kind: str = "code",
) -> ThreadingHTTPServer:
    """Create a configured visualizer HTTP server."""
    api = _api_for(root, graph_kind)
    ensure_available = getattr(api, "ensure_available", None)
    if ensure_available is not None:
        ensure_available()
    handler_cls = _handler_for(api)
    return ThreadingHTTPServer((host, port), handler_cls)


def serve(
    root: str = ".",
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    graph_kind: str = "code",
) -> int:
    """Serve the visualizer until interrupted."""
    httpd = make_server(root, host=host, port=port, graph_kind=graph_kind)
    actual_host, actual_port = httpd.server_address
    url = f"http://{actual_host}:{actual_port}/"
    label = "Weld Agent Graph" if graph_kind == "agent" else "Weld graph"
    print(f"{label} visualizer: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nWeld graph visualizer stopped.", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None, *, graph_kind: str = "code") -> int:
    prog = "wd agents viz" if graph_kind == "agent" else "wd viz"
    description = (
        "Serve a local read-only browser visualizer for .weld/agent-graph.json."
        if graph_kind == "agent"
        else "Serve a local read-only browser visualizer for .weld/graph.json."
    )
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
    )
    add_server_arguments(parser)
    args = parser.parse_args(argv)
    if not args.allow_remote and not _is_loopback_host(args.host):
        print(
            f"error: --host={args.host!r} is not a loopback address; "
            "refusing to bind. Pass --allow-remote to acknowledge the exposure risk.",
            file=sys.stderr,
        )
        return 2
    try:
        return serve(
            args.root,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
            graph_kind=graph_kind,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def add_server_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the shared local visualizer server flags to *parser*."""
    parser.add_argument("--root", default=".", help="Project root directory")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (must be loopback unless --allow-remote is set)",
    )
    parser.add_argument("--port", type=int, default=0, help="Bind port; 0 chooses a free port")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding to a non-loopback host (exposes the visualizer beyond this machine)",
    )


def _api_for(root: str, graph_kind: str) -> object:
    if graph_kind == "code":
        return VizApi(root)
    if graph_kind == "agent":
        from weld.viz.agent_api import AgentVizApi

        return AgentVizApi(root)
    raise ValueError(f"unknown graph kind: {graph_kind!r}")


def _is_loopback_host(host: str) -> bool:
    """Return True only if ``host`` is guaranteed to bind to loopback.

    Recognizes all IPv4 addresses in 127.0.0.0/8, the IPv6 ``::1`` loopback,
    and the ``localhost`` hostname family. Anything else -- including
    ``0.0.0.0``, ``::``, public IPs, and unrecognized hostnames -- returns
    False so the CLI refuses to bind without ``--allow-remote``.
    """
    if not host:
        return False
    if host.lower() in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
