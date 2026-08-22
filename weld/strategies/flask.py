"""Strategy: Flask routes (ADR 0064 criterion 3 / bd 778o).

Recognises two Flask route surfaces:

1. ``@app.route('/path')`` and ``@blueprint.route('/path')``
   decorators. Each decorated function becomes one
   ``route:<METHOD>:<path>`` node per method on ``methods=`` (defaulting
   to ``GET`` per Flask's runtime default).
2. ``<carrier>.add_url_rule('/path', view_func=fn, methods=[...])``
   callsites. The route node carries ``route_source='add_url_rule'`` so
   consumers can filter the two populations.

For every route the strategy also emits a minimal handler
``symbol:py:<module-dotted>:<qualname>`` node so the
``exposes`` edge survives the dangling-edge post-pass when
``python_callgraph`` is not paired with the same glob; when it is
paired, ``nodes.update`` in :func:`weld.discover._run` overwrites our
placeholder with the canonical payload.

Mirrors the C# ``csharp_aspnet_routes`` controller -> route edge so
the tier-check criterion-3 ``check_flask`` helper counts ``exposes``
edges -- see :mod:`tools._tier_check_framework_python`.

Static-only: no imports are followed, no runtime hooks run.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._provenance import file_provenance

#: Default HTTP method when a Flask decorator omits ``methods=``.
_DEFAULT_METHOD: str = "GET"

#: Method names allowed on Flask ``methods=`` kwargs. Used to drop
#: typos or non-standard method names (Flask itself accepts any string,
#: but tier-check criterion 3 only cares about canonical verbs).
_KNOWN_METHODS: frozenset[str] = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
})


def _module_dotted_path(rel_path: str) -> str:
    """Return the python-style dotted module path for ``rel_path``.

    Mirrors :func:`weld.strategies.python_callgraph._module_dotted_path`
    so handler symbol ids emitted here match the canonical id shape.
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


def _has_flask_import(tree: ast.Module) -> bool:
    """Return True iff *tree* imports ``flask`` at module scope.

    Filters out unrelated objects that incidentally define a ``.route``
    attribute by gating route extraction on a real flask import.
    """
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "flask" or alias.name.startswith("flask."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "flask" or mod.startswith("flask."):
                return True
    return False


def _scan_flask_carriers(tree: ast.Module) -> set[str]:
    """Return local variable names bound to ``Flask(...)`` / ``Blueprint(...)``.

    Recognises both ``app = Flask(__name__)`` / ``bp = Blueprint(...)``
    and the qualified forms ``flask.Flask(...)`` / ``flask.Blueprint(...)``.
    """
    carriers: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in ("Flask", "Blueprint"):
            carriers.add(node.targets[0].id)
    return carriers


def _extract_methods_kwarg(call: ast.Call) -> list[str]:
    """Pull ``methods=['GET', ...]`` literal entries from *call*.

    Returns an empty list when ``methods=`` is missing or non-literal;
    callers apply :data:`_DEFAULT_METHOD` in that case.
    """
    for kw in call.keywords:
        if kw.arg != "methods":
            continue
        if not isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
            continue
        out: list[str] = []
        for elt in kw.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                method = elt.value.upper()
                if method in _KNOWN_METHODS and method not in out:
                    out.append(method)
        return out
    return []


def _extract_decorator_path(dec: ast.Call) -> str | None:
    """Return the literal path arg of a ``@x.route(...)`` decorator.

    Non-literal / missing args return ``None`` and the decorator is
    skipped (conservative, mirrors :func:`extract_routes`).
    """
    if not dec.args:
        return None
    first = dec.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _iter_route_decorators(
    tree: ast.Module, carriers: set[str],
):
    """Yield ``(func_node, decorator_call)`` per ``@<carrier>.route(...)``.

    Walks module-level functions in source order so per-method
    explosion below produces deterministic node ids.
    """
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "route":
                continue
            base = func.value
            if not (isinstance(base, ast.Name) and base.id in carriers):
                continue
            yield node, dec


def _iter_add_url_rule_calls(
    tree: ast.Module, carriers: set[str],
):
    """Yield ``(call, view_name)`` per ``<carrier>.add_url_rule(...)``.

    Returns the view function's ``ast.Name`` when supplied via
    ``view_func=`` kwarg. Calls without a static view function still
    emit a route, just without the handler ``exposes`` edge.
    """
    for stmt in tree.body:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            continue
        call = stmt.value
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_url_rule":
            continue
        base = func.value
        if not (isinstance(base, ast.Name) and base.id in carriers):
            continue
        view_name = None
        for kw in call.keywords:
            if kw.arg == "view_func" and isinstance(kw.value, ast.Name):
                view_name = kw.value.id
                break
        yield call, view_name


def _handler_symbol_node(
    qualname: str, rel_path: str, module_path: str,
) -> dict:
    """Build a minimal handler symbol node payload.

    Emitted so the ``symbol:py -> exposes -> route:`` edge survives
    the dangling-edge post-pass when ``python_callgraph`` is not
    paired with the same glob. When it is, ``nodes.update`` in
    :func:`weld.discover._run` upgrades us to the canonical payload.
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
            "source_strategy": "flask",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
        },
    }


def _route_node(
    method: str, path: str, *, rel_path: str, function: str | None,
    carrier: str, source: str,
) -> dict:
    """Build a Flask route node payload (ADR 0086 inbound HTTP surface)."""
    return {
        "type": "route",
        "label": f"{method} {path}",
        "props": {
            "file": rel_path,
            "function": function,
            "carrier": carrier,
            "source_strategy": "flask",
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["implementation"],
            "protocol": "http",
            "surface_kind": "request_response",
            "transport": "http",
            "boundary_kind": "inbound",
            "declared_in": rel_path,
            "route_source": source,
        },
    }


def _exposes_edge(src: str, dst: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": "exposes",
        "props": {
            "source_strategy": "flask",
            "confidence": "definite",
        },
    }


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Flask routes, handler symbols, and exposes edges.

    Glob handling mirrors the other Python strategies: ``**``-patterns
    walk from *root*; otherwise we glob inside the pattern's parent.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    candidates = resolve_glob(root, pattern, excludes)

    for py in candidates:
        if not py.is_file():
            continue
        if py.name.startswith("_") and py.name != "__init__.py":
            continue
        # Provenance is this file, recorded before the read (bd od2a). The
        # parent directory it used to record degenerated to ``"./"`` for a
        # repo-root match -- the marker that makes every path in the
        # repository count as tracked source. Recording before the read also
        # closes the hole the old ``if emitted_here`` placement left: a
        # module with no route today was not provenance, so adding the first
        # ``@app.route`` to it never marked the graph stale.
        discovered_from.extend(file_provenance(root, [py]))
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        if not _has_flask_import(tree):
            continue
        carriers = _scan_flask_carriers(tree)
        if not carriers:
            continue

        rel_path = rel_to_root(py, root)
        module_path = _module_dotted_path(rel_path)
        _emit_for_module(
            tree=tree,
            carriers=carriers,
            rel_path=rel_path,
            module_path=module_path,
            nodes=nodes,
            edges=edges,
        )

    return StrategyResult(nodes, edges, discovered_from)


def _emit_for_module(
    *, tree: ast.Module, carriers: set[str], rel_path: str,
    module_path: str, nodes: dict[str, dict], edges: list[dict],
) -> bool:
    """Run decorator + add_url_rule scans for one module.

    Returns ``True`` when at least one route was emitted. The caller no
    longer branches on it for provenance -- every module it reads is
    recorded, emitting or not (bd od2a) -- but the flag stays part of the
    scan's answer for callers and tests that ask what a module produced.
    """
    any_emitted = False

    for fn_node, dec in _iter_route_decorators(tree, carriers):
        path = _extract_decorator_path(dec)
        if path is None:
            continue
        methods = _extract_methods_kwarg(dec) or [_DEFAULT_METHOD]
        # ``dec.func`` is always an ``ast.Attribute`` whose ``value`` is
        # the carrier (per :func:`_iter_route_decorators` filter), so we
        # can read the carrier directly off the matching decorator
        # without re-walking ``fn_node.decorator_list``.
        carrier_name = ""
        if isinstance(dec.func, ast.Attribute) and isinstance(
            dec.func.value, ast.Name,
        ):
            carrier_name = dec.func.value.id
        for method in methods:
            rid = f"route:{method}:{path}"
            nodes[rid] = _route_node(
                method, path,
                rel_path=rel_path,
                function=fn_node.name,
                carrier=carrier_name,
                source="decorator",
            )
            handler_id = f"symbol:py:{module_path}:{fn_node.name}"
            nodes.setdefault(
                handler_id,
                _handler_symbol_node(fn_node.name, rel_path, module_path),
            )
            edges.append(_exposes_edge(handler_id, rid))
            any_emitted = True

    for call, view_name in _iter_add_url_rule_calls(tree, carriers):
        path = _extract_decorator_path(call)
        if path is None:
            continue
        methods = _extract_methods_kwarg(call) or [_DEFAULT_METHOD]
        for method in methods:
            rid = f"route:{method}:{path}"
            carrier_name = ""
            if isinstance(call.func, ast.Attribute):
                inner = call.func.value
                if isinstance(inner, ast.Name):
                    carrier_name = inner.id
            nodes[rid] = _route_node(
                method, path,
                rel_path=rel_path,
                function=view_name,
                carrier=carrier_name,
                source="add_url_rule",
            )
            if view_name:
                handler_id = f"symbol:py:{module_path}:{view_name}"
                nodes.setdefault(
                    handler_id,
                    _handler_symbol_node(view_name, rel_path, module_path),
                )
                edges.append(_exposes_edge(handler_id, rid))
            any_emitted = True

    return any_emitted


__all__ = ["extract"]
