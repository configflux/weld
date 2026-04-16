"""Tests for the static HTTP-client interaction extractor (project-xoq.3.2).

The ``http_client`` strategy extracts outbound HTTP call sites where both
the HTTP method and the URL/path argument are statically knowable from the
parsed AST alone. Per ADR 0018, the extractor must prefer omission over
guesswork: dynamic URLs (f-strings with variables, variable references,
concatenation) and dynamic methods (``client.request(method, ...)`` with a
non-literal method) are dropped.

Every emitted node is an ``rpc`` node stamped with:

    protocol="http", surface_kind="request_response",
    transport="http", boundary_kind="outbound",
    declared_in="<rel-path>"

and linked to the declaring module file with an ``invokes`` edge whose
``source_strategy`` is ``http_client``. When the outbound URL matches a
same-repo FastAPI route ID (``route:<METHOD>:<path>``), a direct
``invokes`` edge to that route is also emitted so retrieval can traverse
client -> server without embedding lookups.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies.http_client import extract  # noqa: E402

def _write(pkg: Path, name: str, body: str) -> None:
    (pkg / name).write_text(textwrap.dedent(body))

def _run(root: Path, glob: str = "src/**/*.py") -> tuple[dict, list]:
    result = extract(root, {"glob": glob}, {})
    return result.nodes, result.edges

class HttpClientLiteralUrlTest(unittest.TestCase):
    """httpx calls with literal URL and method produce rpc nodes."""

    def test_httpx_get_with_literal_url(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import httpx
                def fetch():
                    return httpx.get("https://example.com/api/v1/widgets")
            """)
            nodes, edges = _run(root)
            rpcs = [n for n in nodes.values() if n["type"] == "rpc"]
            self.assertEqual(len(rpcs), 1)
            node = rpcs[0]
            self.assertEqual(node["props"]["protocol"], "http")
            self.assertEqual(node["props"]["surface_kind"], "request_response")
            self.assertEqual(node["props"]["transport"], "http")
            self.assertEqual(node["props"]["boundary_kind"], "outbound")
            self.assertEqual(node["props"]["declared_in"], "src/pkg/caller.py")
            self.assertEqual(node["props"]["method"], "GET")
            self.assertEqual(
                node["props"]["url"], "https://example.com/api/v1/widgets"
            )
            self.assertEqual(node["props"]["source_strategy"], "http_client")
            self.assertEqual(node["props"]["authority"], "canonical")
            self.assertEqual(node["props"]["confidence"], "definite")

    def test_requests_post_with_literal_url(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import requests
                def send():
                    return requests.post("https://api.example.com/v2/events", json={})
            """)
            nodes, _edges = _run(root)
            rpcs = [n for n in nodes.values() if n["type"] == "rpc"]
            self.assertEqual(len(rpcs), 1)
            self.assertEqual(rpcs[0]["props"]["method"], "POST")
            self.assertEqual(
                rpcs[0]["props"]["url"], "https://api.example.com/v2/events"
            )

class HttpClientDynamicUrlTest(unittest.TestCase):
    """Dynamic URLs and methods must be dropped, not guessed."""

    def test_variable_url_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import httpx
                URL = "https://example.com/api"
                def fetch():
                    return httpx.get(URL)
            """)
            nodes, _edges = _run(root)
            rpcs = [n for n in nodes.values() if n["type"] == "rpc"]
            self.assertEqual(rpcs, [])

    def test_fstring_with_variable_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import httpx
                def fetch(host: str):
                    return httpx.get(f"https://{host}/api")
            """)
            nodes, _edges = _run(root)
            self.assertEqual(
                [n for n in nodes.values() if n["type"] == "rpc"], []
            )

    def test_dynamic_method_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import httpx
                def fetch(method: str):
                    return httpx.request(method, "https://example.com/api")
            """)
            nodes, _edges = _run(root)
            self.assertEqual(
                [n for n in nodes.values() if n["type"] == "rpc"], []
            )

    def test_non_http_function_calls_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                def get(x):
                    return x
                def fetch():
                    return get("https://example.com/api")
            """)
            nodes, _edges = _run(root)
            self.assertEqual(
                [n for n in nodes.values() if n["type"] == "rpc"], []
            )

class HttpClientLiteralFStringTest(unittest.TestCase):
    """An f-string whose only parts are string literals is still static."""

    def test_literal_only_fstring_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import httpx
                def fetch():
                    return httpx.get(f"https://example.com/api")
            """)
            nodes, _edges = _run(root)
            rpcs = [n for n in nodes.values() if n["type"] == "rpc"]
            self.assertEqual(len(rpcs), 1)
            self.assertEqual(
                rpcs[0]["props"]["url"], "https://example.com/api"
            )

class HttpClientModuleEdgeTest(unittest.TestCase):
    """Each rpc node is linked back to its declaring module file."""

    def test_invokes_edge_from_file_node(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import httpx
                def fetch():
                    return httpx.get("https://example.com/ping")
            """)
            nodes, edges = _run(root)
            rpcs = [nid for nid, n in nodes.items() if n["type"] == "rpc"]
            self.assertEqual(len(rpcs), 1)
            rpc_id = rpcs[0]
            file_id = "file:src/pkg/caller.py"
            matches = [
                e for e in edges
                if e["from"] == file_id
                and e["to"] == rpc_id
                and e["type"] == "invokes"
            ]
            self.assertEqual(len(matches), 1)
            self.assertEqual(
                matches[0]["props"]["source_strategy"], "http_client"
            )
            self.assertEqual(
                matches[0]["props"]["confidence"], "definite"
            )

class HttpClientRouteLinkTest(unittest.TestCase):
    """When the URL path matches a same-repo route id, link to it."""

    def test_path_only_url_links_to_route_node(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            # Path-only URL (starts with "/") is the common internal-call
            # idiom: httpx.Client(base_url=...) + client.get("/health").
            _write(pkg, "caller.py", """\
                import httpx
                def ping():
                    return httpx.get("/health")
            """)
            nodes, edges = _run(root)
            rpcs = [n for n in nodes.values() if n["type"] == "rpc"]
            self.assertEqual(len(rpcs), 1)
            self.assertEqual(rpcs[0]["props"]["url"], "/health")
            # The extractor emits a dangling invokes edge to the route id
            # it would match; discovery's post-processing drops the edge if
            # the route node is absent, so it is safe to emit unconditionally.
            route_edges = [
                e for e in edges
                if e["type"] == "invokes"
                and e["to"] == "route:GET:/health"
            ]
            self.assertEqual(len(route_edges), 1)
            self.assertEqual(
                route_edges[0]["props"]["confidence"], "inferred"
            )

class HttpClientFragmentValidatesTest(unittest.TestCase):
    """Strategy output must pass contract.validate_fragment."""

    def test_fragment_is_contract_valid(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src" / "pkg"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import httpx
                def fetch():
                    return httpx.get("https://example.com/api")
                def send():
                    return httpx.post("https://example.com/api", json={})
            """)
            nodes, edges = _run(root)
            fragment = {
                "nodes": nodes,
                "edges": edges,
                "discovered_from": [],
            }
            errors = validate_fragment(
                fragment,
                source_label="strategy:http_client",
                allow_dangling_edges=True,
            )
            self.assertEqual(errors, [], f"unexpected errors: {errors}")

if __name__ == "__main__":
    unittest.main()
