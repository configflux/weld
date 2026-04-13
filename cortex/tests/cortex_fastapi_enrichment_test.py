"""Tests for enriched FastAPI extraction (project-xoq.3.1).

Verifies that the FastAPI strategy links routes to:

- the owning service (derived from a ``services/<name>/`` path prefix),
- the declaring boundary (the file under the routers' parent directory that
  instantiates ``FastAPI()``),
- request and response contracts declared on the handler signature, including
  attribute-style ``response_model`` targets, ``responses={...}`` dict
  entries, and Pydantic body parameters.

It also locks in the protocol metadata stamped on every ``route`` node per
ADR 0018 and project-xoq.1.2: ``protocol=http``, ``surface_kind=request_response``,
``boundary_kind=inbound``, ``transport=http``, ``declared_in=<rel-path>``.

The extraction must stay conservative and static -- no runtime hooks, no
import resolution beyond what is visible in the parsed AST.
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

from cortex.strategies.fastapi import extract  # noqa: E402

def _write(pkg: Path, name: str, body: str) -> None:
    (pkg / name).write_text(textwrap.dedent(body))

class FastapiProtocolMetadataTest(unittest.TestCase):
    """Every route node should carry ADR 0018 interaction-surface metadata."""

    def test_route_stamps_http_protocol_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "routers"
            pkg.mkdir()
            _write(pkg, "health.py", """\
                from fastapi import APIRouter
                router = APIRouter(prefix="/health", tags=["health"])
                @router.get("/")
                def health_check():
                    return {"ok": True}
            """)
            result = extract(root, {"glob": "routers/*.py"}, {})
            self.assertTrue(result.nodes)
            for _nid, node in result.nodes.items():
                if node["type"] != "route":
                    continue
                props = node["props"]
                self.assertEqual(props.get("protocol"), "http")
                self.assertEqual(props.get("surface_kind"), "request_response")
                self.assertEqual(props.get("boundary_kind"), "inbound")
                self.assertEqual(props.get("transport"), "http")
                self.assertEqual(props.get("declared_in"), "routers/health.py")

class FastapiServiceLinkTest(unittest.TestCase):
    """Routes under ``services/<name>/`` should link to the owning service."""

    def test_route_under_services_api_links_to_service_node(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "services" / "api" / "src" / "pkg" / "routers"
            pkg.mkdir(parents=True)
            _write(pkg, "public.py", """\
                from fastapi import APIRouter
                router = APIRouter(prefix="/public", tags=["public"])
                @router.get("/ping")
                def ping():
                    return {"pong": True}
            """)
            result = extract(
                root,
                {"glob": "services/api/src/pkg/routers/*.py"},
                {},
            )
            route_ids = [nid for nid, n in result.nodes.items() if n["type"] == "route"]
            self.assertEqual(len(route_ids), 1)
            route_id = route_ids[0]

            # service->route containment edge must be emitted with the
            # expected provenance; the target is the topology-declared
            # ``service:api`` id.
            service_edges = [
                e for e in result.edges
                if e["type"] == "contains"
                and e["from"] == "service:api"
                and e["to"] == route_id
            ]
            self.assertEqual(len(service_edges), 1, result.edges)
            edge = service_edges[0]
            self.assertEqual(edge["props"].get("source_strategy"), "fastapi")
            self.assertIn(edge["props"].get("confidence"), {"inferred", "definite"})

    def test_route_outside_services_does_not_emit_service_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "routers"
            pkg.mkdir()
            _write(pkg, "health.py", """\
                from fastapi import APIRouter
                router = APIRouter()
                @router.get("/health")
                def health():
                    return {}
            """)
            result = extract(root, {"glob": "routers/*.py"}, {})
            contains_edges = [
                e for e in result.edges
                if e["type"] == "contains" and e["from"].startswith("service:")
            ]
            self.assertEqual(contains_edges, [])

class FastapiBoundaryLinkTest(unittest.TestCase):
    """Routes should link to the boundary that mounts them (static)."""

    def test_boundary_file_in_parent_dir_links_to_route(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            api_dir = root / "services" / "api" / "src" / "pkg"
            routers = api_dir / "routers"
            routers.mkdir(parents=True)
            _write(routers, "public.py", """\
                from fastapi import APIRouter
                router = APIRouter(prefix="/public")
                @router.get("/ping")
                def ping():
                    return {}
            """)
            # The boundary file lives next to the routers directory.
            _write(api_dir, "app.py", """\
                from fastapi import FastAPI
                from .routers import public

                def create_app() -> FastAPI:
                    app = FastAPI()
                    app.include_router(public.router)
                    return app
            """)
            result = extract(
                root,
                {"glob": "services/api/src/pkg/routers/*.py"},
                {},
            )
            route_ids = [nid for nid, n in result.nodes.items() if n["type"] == "route"]
            self.assertEqual(len(route_ids), 1)
            route_id = route_ids[0]

            # The boundary id mirrors cortex/strategies/boundary_entrypoint.py's
            # ``boundary:<rel-path-without-ext>`` convention.
            expected_boundary = "boundary:services/api/src/pkg/app"
            exposes_edges = [
                e for e in result.edges
                if e["type"] == "exposes"
                and e["from"] == expected_boundary
                and e["to"] == route_id
            ]
            self.assertEqual(len(exposes_edges), 1, result.edges)
            edge = exposes_edges[0]
            self.assertEqual(edge["props"].get("source_strategy"), "fastapi")
            self.assertIn(edge["props"].get("confidence"), {"inferred", "definite"})

    def test_no_boundary_emitted_when_parent_dir_has_no_fastapi_app(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            routers = root / "routers"
            routers.mkdir()
            _write(routers, "health.py", """\
                from fastapi import APIRouter
                router = APIRouter()
                @router.get("/")
                def health():
                    return {}
            """)
            result = extract(root, {"glob": "routers/*.py"}, {})
            exposes_edges = [
                e for e in result.edges
                if e["type"] == "exposes" and e["from"].startswith("boundary:")
            ]
            self.assertEqual(exposes_edges, [])

class FastapiContractLinkTest(unittest.TestCase):
    """Request- and response-body contracts should be linked to routes."""

    def test_attribute_response_model_links_to_contract(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            routers = root / "routers"
            routers.mkdir()
            _write(routers, "users.py", """\
                from fastapi import APIRouter
                from . import schemas
                router = APIRouter(prefix="/users")
                @router.get("/me", response_model=schemas.UserOut)
                def me():
                    return {}
            """)
            result = extract(root, {"glob": "routers/*.py"}, {})
            responds_with = [
                e for e in result.edges
                if e["type"] == "responds_with" and e["to"] == "contract:UserOut"
            ]
            self.assertEqual(len(responds_with), 1, result.edges)

    def test_responses_dict_with_model_entries_link_to_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            routers = root / "routers"
            routers.mkdir()
            _write(routers, "auth.py", """\
                from fastapi import APIRouter
                router = APIRouter(prefix="/auth")
                @router.post(
                    "/login",
                    responses={
                        200: {"model": LoginOk},
                        401: {"model": LoginError},
                    },
                )
                def login():
                    return {}
            """)
            result = extract(root, {"glob": "routers/*.py"}, {})
            targets = {
                e["to"] for e in result.edges if e["type"] == "responds_with"
            }
            self.assertIn("contract:LoginOk", targets)
            self.assertIn("contract:LoginError", targets)

    def test_request_body_parameter_links_accepts_contract(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            routers = root / "routers"
            routers.mkdir()
            _write(routers, "auth.py", """\
                from fastapi import APIRouter
                router = APIRouter(prefix="/auth")
                @router.post("/register")
                def register(payload: RegisterRequest):
                    return {}
            """)
            result = extract(root, {"glob": "routers/*.py"}, {})
            accepts = [
                e for e in result.edges
                if e["type"] == "accepts" and e["to"] == "contract:RegisterRequest"
            ]
            self.assertEqual(len(accepts), 1, result.edges)
            self.assertEqual(
                accepts[0]["props"].get("source_strategy"), "fastapi",
            )
            # Parameter-annotation inference is structural but cannot prove
            # BaseModel inheritance from inside the router file; the edge
            # must therefore be marked as inferred, not definite.
            self.assertEqual(accepts[0]["props"].get("confidence"), "inferred")

    def test_ignores_non_model_primitive_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            routers = root / "routers"
            routers.mkdir()
            _write(routers, "things.py", """\
                from fastapi import APIRouter
                router = APIRouter(prefix="/things")
                @router.get("/{thing_id}")
                def get_thing(thing_id: int, name: str = "x"):
                    return {}
            """)
            result = extract(root, {"glob": "routers/*.py"}, {})
            accepts = [e for e in result.edges if e["type"] == "accepts"]
            self.assertEqual(accepts, [], "primitive params must not emit accepts")

if __name__ == "__main__":
    unittest.main()
