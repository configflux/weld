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
ADR 0086 and tracked project, so cross-protocol retrieval can treat FastAPI
routes as full interaction surfaces rather than bare URL stubs.

The extractor stays strictly static: no imports are followed, no runtime
hooks are run, and any edge whose target cannot be discovered is dropped
by discovery's existing dangling-edge sweep.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import (
    StrategyResult,
    extract_router_info,
    extract_routes,
    filter_glob_results,
)
from weld.strategies._strategy_failure import note_strategy_failure

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
        except (OSError, UnicodeDecodeError, SyntaxError):
            # bd o642: ``UnicodeDecodeError`` is a ``ValueError``, not an
            # ``OSError``, so a latin-1 module sorting ahead of the real app
            # file aborted the run here even though the read errors beside it
            # were already caught. Nothing is recorded: a boundary candidate is
            # not this strategy's input -- it never enters ``discovered_from``
            # -- so ``python_module``, which owns that file, is the strategy
            # that reports it (bd hch4). A file weld cannot read declares no
            # app, which is the true answer to the only question asked here.
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

def _module_dotted_path(rel_path: str) -> str:
    """Return the python-style dotted module path for ``rel_path``.

    Mirrors :func:`weld.strategies.python_callgraph._module_dotted_path`
    so the route-handler symbol ids emitted here match the symbol ids
    ``python_callgraph`` emits for the same handler functions when both
    strategies run against the same glob. Sharing the id keeps the
    dangling-edge sweep happy and lets the canonical python_callgraph
    node win the ``nodes.update`` race in :func:`weld.discover._run`.
    """
    p = Path(rel_path)
    parts = list(p.parts)
    if not parts:
        return ""
    last = parts[-1]
    if last == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = Path(last).stem
    return ".".join(parts)

def _handler_symbol_node(qualname: str, rel_path: str, module_path: str) -> dict:
    """Build a minimal handler symbol node payload.

    Emitted so the criterion-3 ``symbol:py -> exposes -> route:`` edge
    survives :func:`weld._discover_postprocess._clean_and_dedup_edges`
    even when ``python_callgraph`` is not configured for the same glob.
    On real corpora ``python_callgraph`` overwrites this node with a
    richer payload (call graph metadata, ``kind``), which is fine: the
    edge target id is unchanged.
    """
    return {
        "type": "symbol",
        "label": qualname,
        "props": {
            "file": rel_path,
            "module": module_path,
            "qualname": qualname,
            "language": "python",
            "kind": "function",
            "source_strategy": "fastapi",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
        },
    }

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract FastAPI routes and their service/boundary/contract links."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])

    # bd b9xgd: this used to resolve its own glob -- ``(root / pattern).parent``,
    # an ``is_dir()`` early-return, then one directory's worth of ``glob()``.
    # That is the copy ADR 0112 says is gone, kept here because this strategy
    # was never migrated. It made any ``**`` pattern *and* any wildcard in a
    # directory segment (``api/*/routers/*.py``) resolve to nothing at all,
    # since both give a parent that is a literal path and never a directory.
    #
    # The boundary lookup below is the one thing the old parent was legitimately
    # used for -- a *label*, not a resolve (the same split ``sqlalchemy`` keeps
    # for ``domain_dir``). It moves onto the matched file, memoised per routers
    # directory: under a wildcard segment there is no single routers directory,
    # and computing one for the whole glob would attribute every route to
    # whichever directory the pattern's literal prefix happened to name. It is
    # still not provenance -- ``discovered_from`` stays per file, because a
    # directory-derived entry degenerates to ``"./"`` at the repo root and
    # marks the whole tree as tracked source (bd 8ia5).
    boundary_by_dir: dict[Path, str | None] = {}

    for py in resolve_glob(root, pattern, excludes):
        if py.name.startswith("_"):
            continue
        routers_dir = py.parent
        if routers_dir not in boundary_by_dir:
            boundary_by_dir[routers_dir] = _detect_boundary_file(
                routers_dir.parent, root,
            )
        boundary_id = boundary_by_dir[routers_dir]
        rel_path = rel_to_root(py, root)
        # Recorded before the parse: a file that declares no router today
        # must still be re-read once someone adds one (see StrategyResult).
        discovered_from.append(rel_path)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            # bd o642, applying bd pt38's fix here: ``read_text`` raises
            # ``OSError`` -- never ``SyntaxError`` -- so guarding the parse
            # alone let a file that vanished between the listing above and this
            # read abort the entire run. ``UnicodeDecodeError`` is a
            # ``ValueError``, so widening to ``OSError`` alone would still
            # abort on non-UTF-8 bytes. Recorded as a *failure*, not as this
            # strategy deciding the file declares no router: a decision is
            # keyed on the path and exempts the file from the ADR 0008 per-file
            # repair for good, so one that came back unchanged would never be
            # re-read (bd hch4).
            note_strategy_failure(context, [rel_path])
            continue
        router_info = extract_router_info(tree)
        if not router_info:
            continue
        router_name = py.stem
        service_id = _owning_service_id(rel_path)
        module_path = _module_dotted_path(rel_path)
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
                    # Interaction-surface metadata (ADR 0086, tracked project).
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

            # --- Route-handler symbol exposes route ------------------
            # Mirror the C# ``csharp_aspnet_routes`` controller -> route
            # edge: the handler function symbol exposes its declared
            # route. The id matches the canonical
            # ``symbol:py:<module-dotted>:<qualname>`` shape that
            # ``python_callgraph`` emits, so when the two strategies
            # run against the same glob the canonical node wins the
            # ``nodes.update`` race and our minimal node is upgraded.
            # When python_callgraph isn't paired, our minimal node
            # keeps the edge from being swept by the dangling-edge
            # post-processor. Tier-check criterion 3 reads this edge
            # via :func:`tools._tier_check_framework_python.check_fastapi`.
            if module_path:
                handler_id = f"symbol:py:{module_path}:{route['function']}"
                nodes.setdefault(
                    handler_id,
                    _handler_symbol_node(
                        route["function"], rel_path, module_path,
                    ),
                )
                edges.append(
                    _edge(handler_id, nid, "exposes", confidence="definite")
                )

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
