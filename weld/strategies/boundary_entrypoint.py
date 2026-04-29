"""Strategy: Boundary and entrypoint detection from Python modules.

Identifies service boundaries (API surfaces, app factories) and entrypoints
(main guards, CLI entry points) so agents can understand service topology
and where execution begins.

Confidence policy:
- Entrypoint kind classification (server/cli/main_guard) relies on import
  heuristics and framework-name matching, so nodes get ``"inferred"``.
- Boundary detection (app factory AST instantiation) is structurally
  verified, so boundary nodes keep ``"definite"``.

"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

# -- Framework detection helpers -------------------------------------------

#: Import names that indicate a CLI framework.
_CLI_FRAMEWORKS: dict[str, str] = {
    "click": "click",
    "typer": "typer",
    "argparse": "argparse",
    "fire": "fire",
}

#: Import names that indicate a server/ASGI framework.
_SERVER_FRAMEWORKS: dict[str, str] = {
    "uvicorn": "uvicorn",
    "gunicorn": "gunicorn",
    "hypercorn": "hypercorn",
    "daphne": "daphne",
    "waitress": "waitress",
}

#: Import names that indicate an API/web framework (boundary).
_API_FRAMEWORKS: dict[str, str] = {
    "fastapi": "fastapi",
    "FastAPI": "fastapi",
    "flask": "flask",
    "Flask": "flask",
    "django": "django",
    "starlette": "starlette",
    "aiohttp": "aiohttp",
}

#: Top-level assignment patterns that create an app instance (boundary).
_APP_FACTORY_NAMES: frozenset[str] = frozenset([
    "FastAPI", "Flask", "Starlette", "Django", "APIRouter",
])

def _collect_imports(tree: ast.Module) -> set[str]:
    """Gather all imported module/name references from the AST."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            for alias in node.names:
                names.add(alias.name)
    return names

def _has_main_guard(tree: ast.Module) -> bool:
    """Check if module has ``if __name__ == '__main__':`` block."""
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Compare):
            if (
                isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__"
            ):
                return True
    return False

def _detect_entrypoint_kind(imports: set[str]) -> tuple[str, str]:
    """Classify an entrypoint by its imports.

    Returns ``(kind, framework)`` where kind is one of:
    ``server``, ``cli``, ``main_guard`` and framework is the
    detected framework name or empty string.
    """
    # Server frameworks take priority
    for imp, fw in _SERVER_FRAMEWORKS.items():
        if imp in imports:
            return "server", fw

    # CLI frameworks
    for imp, fw in _CLI_FRAMEWORKS.items():
        if imp in imports:
            return "cli", fw

    return "main_guard", ""

def _detect_boundary(tree: ast.Module, imports: set[str]) -> tuple[str, str] | None:
    """Detect if a module defines a service boundary (API surface).

    Returns ``(kind, framework)`` or None.
    """
    # Check for API framework imports
    detected_fw = ""
    for imp, fw in _API_FRAMEWORKS.items():
        if imp in imports:
            detected_fw = fw
            break

    if not detected_fw:
        return None

    # Verify there is an actual app instantiation or factory function
    for node in tree.body:
        # Top-level assignment: app = FastAPI()
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            func_name = ""
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            if func_name in _APP_FACTORY_NAMES:
                return "api_surface", detected_fw

        # Factory function: def create_app() -> FastAPI:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns:
                ret_name = ""
                if isinstance(node.returns, ast.Name):
                    ret_name = node.returns.id
                elif isinstance(node.returns, ast.Attribute):
                    ret_name = node.returns.attr
                if ret_name in _APP_FACTORY_NAMES:
                    return "api_surface", detected_fw

    return None

# -- Glob resolution (shared pattern with python_module) -------------------

def _resolve_glob(root: Path, pattern: str) -> tuple[list[Path], list[str]]:
    """Resolve a glob pattern that may contain ``**``.

    Returns ``(matched_files, discovered_from_dirs)``.
    """
    files: list[Path] = []
    dirs: set[str] = set()

    if "**" in pattern:
        raw = sorted(root.glob(pattern))
        for py in filter_glob_results(root, raw):
            files.append(py)
            dirs.add(str(py.parent.relative_to(root)) + "/")
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return [], []
        name_pat = Path(pattern).name
        raw = sorted(parent.glob(name_pat))
        for py in filter_glob_results(root, raw):
            files.append(py)
        dirs.add(str(parent.relative_to(root)) + "/")

    return files, sorted(dirs)

def _make_node_id(node_type: str, rel_path: str) -> str:
    """Build node ID like ``entrypoint:services/api/main``."""
    p = Path(rel_path)
    # Use full relative path without extension
    path_no_ext = str(p.with_suffix(""))
    return f"{node_type}:{path_no_ext}"

def _owning_service_id(rel_path: str) -> str | None:
    """Return ``service:<name>`` for conventional ``services/<name>/`` paths."""
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0] == "services":
        return f"service:{parts[1]}"
    return None

def _service_node(service_id: str) -> dict:
    """Build a derived service node that anchors runtime/startup surfaces."""
    service_name = service_id.split(":", 1)[1]
    return {
        "type": "service",
        "label": f"{service_name} service",
        "props": {
            "source_strategy": "boundary_entrypoint",
            "authority": "derived",
            "confidence": "inferred",
            "roles": ["implementation"],
            "description": (
                "Runtime service containing startup entrypoints, execution "
                "flow, and application boundaries."
            ),
        },
    }

def _contains_edge(source_id: str, target_id: str) -> dict:
    """Build a conservative contains edge for startup neighborhood tracing."""
    return {
        "from": source_id,
        "to": target_id,
        "type": "contains",
        "props": {
            "source_strategy": "boundary_entrypoint",
            "confidence": "inferred",
        },
    }

# -- Strategy entry point --------------------------------------------------

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract boundary and entrypoint nodes from Python modules.

    Scans Python files for:
    - **Entrypoints**: modules with ``if __name__ == '__main__':`` guards,
      classified by framework (uvicorn, click, argparse, etc.)
    - **Boundaries**: modules that instantiate API frameworks (FastAPI, Flask)
      indicating a service edge / API surface.

    Emits ``exposes`` edges from boundary nodes to entrypoint nodes in the
    same directory subtree when both are found in the same file.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])

    matched, dirs = _resolve_glob(root, pattern)
    discovered_from.extend(dirs)

    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    for py in matched:
        if should_skip(py, excludes, root=root):
            continue
        if py.name.startswith("_") and py.name != "__init__.py":
            continue
        try:
            source_text = py.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=str(py))
        except (SyntaxError, OSError):
            continue

        rel_path = str(py.relative_to(root))
        imports = _collect_imports(tree)
        service_id = _owning_service_id(rel_path)

        entrypoint_nid = None
        boundary_nid = None

        # Detect entrypoint
        if _has_main_guard(tree):
            kind, framework = _detect_entrypoint_kind(imports)
            entrypoint_nid = _make_node_id("entrypoint", rel_path)
            props: dict = {
                "file": rel_path,
                "kind": kind,
                "source_strategy": "boundary_entrypoint",
                "authority": "canonical",
                "confidence": "inferred",
                "roles": ["implementation"],
                "description": (
                    "Runtime startup entrypoint for application execution flow."
                ),
            }
            if framework:
                props["framework"] = framework
            nodes[entrypoint_nid] = {
                "type": "entrypoint",
                "label": py.stem,
                "props": props,
            }

        # Detect boundary
        boundary_info = _detect_boundary(tree, imports)
        if boundary_info is not None:
            kind, framework = boundary_info
            boundary_nid = _make_node_id("boundary", rel_path)
            nodes[boundary_nid] = {
                "type": "boundary",
                "label": py.stem,
                "props": {
                    "file": rel_path,
                    "kind": kind,
                    "framework": framework,
                    "source_strategy": "boundary_entrypoint",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["implementation"],
                    "description": (
                        "Application runtime boundary reached from startup "
                        "entrypoints and service wiring."
                    ),
                },
            }

        if service_id and (entrypoint_nid or boundary_nid):
            nodes.setdefault(service_id, _service_node(service_id))
            if entrypoint_nid:
                edges.append(_contains_edge(service_id, entrypoint_nid))
            if boundary_nid:
                edges.append(_contains_edge(service_id, boundary_nid))

        # Link boundary -> entrypoint if both exist in same file
        if boundary_nid and entrypoint_nid:
            edges.append({
                "from": boundary_nid,
                "to": entrypoint_nid,
                "type": "exposes",
                "props": {
                    "source_strategy": "boundary_entrypoint",
                    "confidence": "inferred",
                },
            })

    return StrategyResult(nodes, edges, discovered_from)
