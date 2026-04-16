"""Strategy: FastAPI routes.

Extracts ``route`` nodes from FastAPI ``APIRouter`` declarations and links
each route to:

- the owning ``service:<name>`` topology node, when the router file lives
  under ``services/<name>/`` (conservative ``contains`` edge; dropped by
  discovery post-processing if no such service is declared);
- the declaring ``boundary:<path>`` node for the file that sits next to the
  routers directory and statically instantiates a FastAPI app (the same id
  scheme used by ``weld/strategies/boundary_entrypoint.py``), so agents can
  navigate from a boundary surface to every route it mounts;
- declared ``contract:<Name>`` nodes for primary ``response_model=`` targets,
  for entries in the decorator's ``responses={status: {"model": ...}}`` dict,
  and (inferred) for Pydantic-shaped handler parameter annotations.

Protocol metadata (``protocol``, ``surface_kind``, ``transport``,
``boundary_kind``, ``declared_in``) is stamped on every route node per
ADR 0018 and project-xoq.1.2, so cross-protocol retrieval can treat FastAPI
routes as full interaction surfaces rather than bare URL stubs.

The extractor stays strictly static: no imports are followed, no runtime
hooks are run, and any edge whose target cannot be discovered is dropped
by discovery's existing dangling-edge sweep.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._helpers import (
    StrategyResult,
    extract_router_info,
    extract_routes,
    filter_glob_results,
)

def _owning_service_id(rel_path: str) -> str | None:
    """Return ``service:<name>`` if ``rel_path`` sits under ``services/<name>/``.

    The topology layer in ``.weld/discover.yaml`` declares the canonical
    service ids using exactly this scheme; edges that miss (e.g. in
    fixture repos without a ``services/`` layout) are dropped during
    post-processing, so this is safe to emit unconditionally.
    """
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0] == "services":
        return f"service:{parts[1]}"
    return None

def _detect_boundary_file(parent_dir: Path, root: Path) -> str | None:
    """Return the ``boundary:<rel-path-no-ext>`` id for a FastAPI app file.

    Scans the parent of the routers directory for a Python module that
    either (a) contains a top-level ``FastAPI(...)`` call or (b) defines a
    function returning ``FastAPI``. This mirrors the ``_APP_FACTORY_NAMES``
    check in ``weld/strategies/boundary_entrypoint.py`` so the two strategies
    agree on the boundary node id without importing each other.

    Returns ``None`` when no suitable file is found.
    """
    if not parent_dir.is_dir():
        return None
    candidates = sorted(parent_dir.glob("*.py"))
    candidates = filter_glob_results(root, candidates)
    for py in candidates:
        if py.name.startswith("_"):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (SyntaxError, OSError):
            continue
        if not _declares_fastapi_app(tree):
            continue
        rel = py.relative_to(root).with_suffix("")
        return f"boundary:{rel}"
    return None

def _declares_fastapi_app(tree: ast.Module) -> bool:
    """Check whether a module statically instantiates or returns ``FastAPI``."""
    for node in tree.body:
        # Top-level ``app = FastAPI(...)`` assignment.
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "FastAPI":
                return True
        # ``def create_app() -> FastAPI:`` factory function.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ret = node.returns
            ret_name = ""
            if isinstance(ret, ast.Name):
                ret_name = ret.id
            elif isinstance(ret, ast.Attribute):
                ret_name = ret.attr
            if ret_name == "FastAPI":
                return True
    return False

def _edge(
    src: str, dst: str, edge_type: str, *, confidence: str
) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": edge_type,
        "props": {
            "source_strategy": "fastapi",
            "confidence": confidence,
        },
    }

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract FastAPI routes and their service/boundary/contract links."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    routers_dir = (root / pattern).parent
    if not routers_dir.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(routers_dir.relative_to(root)) + "/")

    # Resolve boundary file once per routers directory: every route under
    # the same directory shares the same declaring boundary.
    boundary_id = _detect_boundary_file(routers_dir.parent, root)

    for py in filter_glob_results(root, sorted(routers_dir.glob(Path(pattern).name))):
        if py.name.startswith("_"):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        rel_path = str(py.relative_to(root))
        router_info = extract_router_info(tree)
        if not router_info:
            continue
        router_name = py.stem
        service_id = _owning_service_id(rel_path)
        routes = extract_routes(tree, router_info["var"])
        for route in routes:
            full_path = router_info["prefix"] + route["path"]
            nid = f"route:{route['method']}:{full_path}"
            nodes[nid] = {
                "type": "route",
                "label": f"{route['method']} {full_path}",
                "props": {
                    "file": rel_path,
                    "function": route["function"],
                    "router": router_name,
                    "tags": router_info["tags"],
                    "source_strategy": "fastapi",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["implementation"],
                    # Interaction-surface metadata (ADR 0018, project-xoq.1.2).
                    # Every FastAPI route is an inbound HTTP request/response
                    # surface over TCP/HTTP; the router file is the static
                    # declaration site.
                    "protocol": "http",
                    "surface_kind": "request_response",
                    "transport": "http",
                    "boundary_kind": "inbound",
                    "declared_in": rel_path,
                },
            }

            # --- Service ownership edge (inferred from file path) ----
            # Discovery post-processing drops edges whose target does not
            # exist, so it is safe to emit this unconditionally whenever
            # the router file sits under ``services/<name>/``.
            if service_id is not None:
                edges.append(
                    _edge(service_id, nid, "contains", confidence="inferred")
                )

            # --- Boundary declaration edge ---------------------------
            # Same directory-scoped heuristic as boundary_entrypoint: the
            # declaring app.py/main.py is the one closest to the routers/
            # directory that statically instantiates ``FastAPI``.
            if boundary_id is not None:
                edges.append(
                    _edge(boundary_id, nid, "exposes", confidence="inferred")
                )

            # --- Primary response_model (bare or attribute target) ---
            if route.get("response_model"):
                edges.append(
                    _edge(
                        nid,
                        f"contract:{route['response_model']}",
                        "responds_with",
                        confidence="definite",
                    )
                )

            # --- Extra responses={...} entries -----------------------
            for extra in route.get("response_models", []) or []:
                edges.append(
                    _edge(
                        nid,
                        f"contract:{extra}",
                        "responds_with",
                        confidence="definite",
                    )
                )

            # --- Request body (inferred from parameter annotations) --
            for body_model in route.get("request_body_models", []) or []:
                edges.append(
                    _edge(
                        nid,
                        f"contract:{body_model}",
                        "accepts",
                        confidence="inferred",
                    )
                )

    return StrategyResult(nodes, edges, discovered_from)
