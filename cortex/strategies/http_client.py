"""Strategy: static HTTP client interactions (project-xoq.3.2).

Extracts outbound HTTP call sites where both the HTTP method and the
target URL (or path template) are statically knowable from the parsed
AST alone. Per ADR 0018's static-truth policy, the extractor prefers
omission over guesswork: dynamic URLs (variables, f-strings with
substitutions, concatenation) and dynamic methods are silently dropped.

Supported shapes:

- Module-level function calls on known HTTP client libraries
  (``httpx.get(url)``, ``requests.post(url, ...)``, etc.).
- Attribute calls on identifiers named after known client types
  (``httpx.Client(...).get(url)`` and follow-up ``client.get(url)``
  through an assignment is *not* followed -- only direct attribute
  chains rooted at the known library name are treated as authoritative).

Out of scope (by design):

- Following assignments, imports, or class attributes to resolve the
  receiver of a call. That requires data-flow analysis which ADR 0018
  rules out for Phase 7.
- Resolving ``base_url=`` plus path-only arguments to a full URL. We
  record the path literal as-is; when it matches a same-repo FastAPI
  ``route:<METHOD>:<path>`` id, discovery's dangling-edge sweep picks
  up the direct ``invokes`` link.
- Non-Python HTTP clients. A follow-up task can add TS/JS ``fetch`` and
  ``axios`` extraction under the same vocabulary.

Every emitted ``rpc`` node is stamped with protocol metadata per
ADR 0018 and project-xoq.1.2:

    protocol="http", surface_kind="request_response",
    transport="http", boundary_kind="outbound",
    declared_in="<rel-path>"

and an ``invokes`` edge links the declaring ``file:<rel-path>`` node to
the rpc. When the URL is a path literal that starts with ``/``, a
second (dangling-by-design) ``invokes`` edge targets the matching
``route:<METHOD>:<path>`` id so retrieval can traverse client -> server
without any runtime or embedding layer.
"""

from __future__ import annotations

import ast
from pathlib import Path

from cortex.strategies._helpers import StrategyResult, filter_glob_results

# ---------------------------------------------------------------------------
# Known HTTP client roots.
#
# We only extract calls whose receiver is a direct attribute on one of
# these identifiers (e.g. ``httpx.get(...)``). Anything more indirect --
# an assigned client instance, a method on ``self._client`` -- is left
# alone, because resolving it statically requires data-flow work that
# ADR 0018 explicitly rules out for this phase.
# ---------------------------------------------------------------------------
_HTTP_LIBRARY_ROOTS: frozenset[str] = frozenset(["httpx", "requests"])

#: Attribute names on the library root that name an HTTP method. Anything
#: else on the library root (``httpx.Client``, ``requests.Session``) is
#: a constructor/config call, not a request, and is ignored.
_HTTP_METHOD_ATTRS: frozenset[str] = frozenset(
    ["get", "post", "put", "delete", "patch", "head", "options"]
)

def _literal_url(node: ast.AST) -> str | None:
    """Return the statically-knowable string form of *node*, else None.

    Accepts plain string constants and f-strings whose parts are *all*
    string constants (literal-only f-strings). Any ``FormattedValue``
    part indicates a runtime substitution and disqualifies the url.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None

def _method_from_call(call: ast.Call) -> str | None:
    """Extract the HTTP method name from a call, or None if not static.

    Only calls shaped ``<library>.<method>(...)`` where ``<library>`` is
    a known HTTP client root and ``<method>`` is a known verb attribute
    produce a method name. This guarantees both halves -- receiver and
    verb -- are structurally clear in the source text.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    if func.value.id not in _HTTP_LIBRARY_ROOTS:
        return None
    if func.attr not in _HTTP_METHOD_ATTRS:
        return None
    return func.attr.upper()

def _url_from_call(call: ast.Call) -> str | None:
    """Return the literal URL argument from a call, or None.

    Both positional-first and ``url=`` keyword shapes are accepted;
    anything else (no args, dynamic first arg, missing keyword) is
    treated as not statically knowable.
    """
    if call.args:
        literal = _literal_url(call.args[0])
        if literal is not None:
            return literal
        return None
    for kw in call.keywords:
        if kw.arg == "url":
            return _literal_url(kw.value)
    return None

def _file_has_http_library_import(tree: ast.Module) -> bool:
    """Cheap pre-filter: only walk files that import a known HTTP library."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _HTTP_LIBRARY_ROOTS:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _HTTP_LIBRARY_ROOTS:
                return True
    return False

def _collect_calls(tree: ast.Module) -> list[tuple[str, str]]:
    """Walk *tree* and return ``(method, url)`` pairs for static call sites."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method = _method_from_call(node)
        if method is None:
            continue
        url = _url_from_call(node)
        if url is None or url == "":
            continue
        found.append((method, url))
    return found

def _rpc_id(method: str, url: str) -> str:
    """Build the rpc node id for a static outbound HTTP call.

    The id is keyed on ``(method, url)`` so that duplicate calls to the
    same endpoint collapse into a single node, mirroring how FastAPI
    route nodes key on ``(method, path)``.
    """
    return f"rpc:http:out:{method}:{url}"

def _route_id_for_path(method: str, url: str) -> str | None:
    """Return the FastAPI route id this call could target, or None.

    Only path-only URLs (``/health``, ``/v1/items``) are eligible: a
    full ``https://...`` URL would need host resolution to pick a
    specific in-repo service, which the static-truth policy rules out.
    """
    if not url.startswith("/"):
        return None
    return f"route:{method}:{url}"

def _edge(src: str, dst: str, *, confidence: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": "invokes",
        "props": {
            "source_strategy": "http_client",
            "confidence": confidence,
        },
    }

def _iter_sources(root: Path, pattern: str) -> list[Path]:
    """Expand *pattern* against *root* and apply the repo-boundary filter."""
    # ``Path.glob`` handles both ``src/*.py`` and ``src/**/*.py``.
    matches = sorted(root.glob(pattern))
    return filter_glob_results(root, matches)

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract static HTTP client call sites into rpc nodes and edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    for py in _iter_sources(root, pattern):
        if not py.is_file() or py.suffix != ".py":
            continue
        if py.name.startswith("_"):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text, filename=str(py))
        except SyntaxError:
            continue
        if not _file_has_http_library_import(tree):
            continue

        rel_path = str(py.relative_to(root))
        calls = _collect_calls(tree)
        if not calls:
            continue

        discovered_from.append(rel_path)
        file_id = f"file:{rel_path}"

        for method, url in calls:
            nid = _rpc_id(method, url)
            # Node is idempotent per (method, url) pair. Last writer wins
            # on ``declared_in``; since retrieval deduplicates by id this
            # is harmless -- the edge from the declaring file is what
            # actually preserves provenance.
            nodes[nid] = {
                "type": "rpc",
                "label": f"{method} {url}",
                "props": {
                    "method": method,
                    "url": url,
                    "source_strategy": "http_client",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["implementation"],
                    # Interaction-surface metadata (ADR 0018,
                    # project-xoq.1.2). Every entry is a client-side
                    # request/response over HTTP; boundary_kind is
                    # outbound because the call leaves the module.
                    "protocol": "http",
                    "surface_kind": "request_response",
                    "transport": "http",
                    "boundary_kind": "outbound",
                    "declared_in": rel_path,
                },
            }

            # Edge from the declaring file. ``file:<rel>`` is the id
            # scheme python_module uses for file nodes; the edge is
            # dropped by discovery's dangling-edge sweep when the file
            # node itself is not in the graph (e.g. fixture repos that
            # do not run python_module). Either way this is conservative.
            edges.append(_edge(file_id, nid, confidence="definite"))

            # Optional client -> server link: only when the URL is a
            # path-only literal that could match a FastAPI route id.
            route_id = _route_id_for_path(method, url)
            if route_id is not None:
                edges.append(_edge(nid, route_id, confidence="inferred"))

    return StrategyResult(nodes, edges, discovered_from)
