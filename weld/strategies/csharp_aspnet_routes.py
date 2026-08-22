"""Strategy: ASP.NET Core route extraction (ADR 0056 Wave 2).

Two surface patterns are recognised:

1. **Controllers** -- a class decorated with ``[Route("path")]`` and/or
   ``[ApiController]``, containing methods decorated with HTTP-verb
   attributes (``[HttpGet("...")]``, ``[HttpPost(...)]``, etc.). Each
   verb attribute becomes one ``route:`` node; a
   ``symbol:csharp:<ns>.<class>`` controller node exposes it via an
   ``exposes`` edge.

   The class-level ``[Route]`` template is concatenated with the
   method-level template. The MVC ``[controller]`` token expands to
   the lower-cased class name minus the ``Controller`` suffix;
   ``[action]`` expands to the method name. Other replacement tokens
   are left as literal text so they can be inspected downstream rather
   than dropped.

   Attribute-based routes ship with ``confidence="definite"``.

2. **Minimal API callsites** -- ``app.MapGet("/foo", ...)``,
   ``app.MapPost(...)``, etc. These are lexically captured: the string
   literal is taken at face value and a ``route:`` node is emitted
   with no exposing controller. The declaring file is recorded in
   ``props.file``.

   Minimal API routes ship with ``confidence="inferred"`` per ADR 0056:
   string interpolation, variables, or late-bound config can
   over-include. The flag travels with the edge so downstream filters
   can opt out.

Node shape and edge vocabulary follow the conventions established by
the FastAPI strategy (``route:<METHOD>:<path>``) so a polyglot project
that mixes Python and C# services produces a uniform route subgraph.
Every edge carries an explicit ``confidence`` per ADR 0050.

Helpers for route-id construction, controller/route node payloads, and
MVC token expansion live in
:mod:`weld.strategies._csharp_routes_helpers` to keep this module
under the 400-line file-size cap.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from weld.strategies._csharp_routes_helpers import (
    Controller,
    MinimalApi,
    Route,
    controller_node,
    join_route,
    route_id,
    route_node,
    symbol_id,
)
from weld.strategies._csharp_syntax import (
    CLASS_RE,
    attribute_window_start,
    class_body_range,
    namespace_at,
    namespace_spans,
)
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._provenance import file_provenance

#: HTTP verbs the strategy understands. The list mirrors ASP.NET Core's
#: ``HttpGetAttribute`` / ``HttpPostAttribute`` family. ``Head`` and
#: ``Options`` are included for completeness even though they are
#: rarer in real codebases.
_HTTP_VERBS: tuple[str, ...] = (
    "Get", "Post", "Put", "Delete", "Patch", "Head", "Options",
)

#: Matches ``[Route("path")]``. Group 1 is the path literal.
_ROUTE_ATTR_RE = re.compile(r"\[\s*Route\s*\(\s*\"([^\"]*)\"")

#: Matches ``[HttpGet]``, ``[HttpGet("path")]``, etc. Group 1 is the
#: verb (case-sensitive); group 2 (optional) is the path literal.
_HTTP_VERB_RE = re.compile(
    r"\[\s*Http(" + "|".join(_HTTP_VERBS) + r")\s*"
    r"(?:\(\s*\"([^\"]*)\")?",
)

#: Matches a method declaration of the form
#: ``<modifiers> <return-type> <Name>(``.
_METHOD_RE = re.compile(
    r"(?:(?:public|internal|protected|private|static|async|override|"
    r"virtual|sealed|abstract|new|extern|unsafe)[\t ]+)+"
    r"[A-Za-z_][A-Za-z0-9_<>?,\[\] .]*[\t ]+"
    r"([A-Za-z_][A-Za-z0-9_]*)[\t ]*\(",
)

#: Matches a Minimal API callsite: ``app.MapGet("/foo", ...)`` and
#: friends. Group 1 is the verb, group 2 is the path literal.
_MINIMAL_API_RE = re.compile(
    r"\.Map(" + "|".join(_HTTP_VERBS) + r")\s*\(\s*\"([^\"]*)\"",
)

#: Matches ``[ApiController]`` on a class (a stronger signal that a
#: class is a controller even without an explicit ``[Route]``).
_API_CONTROLLER_RE = re.compile(r"\[\s*ApiController\b")


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract route nodes and exposes edges from C# source files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "**/*.cs")
    excludes = source.get("exclude", [])

    matched = resolve_glob(root, pattern, excludes)

    for cs_file in matched:
        if not cs_file.is_file():
            continue
        # Provenance is this file, recorded before the read (bd od2a): the
        # parent directory it replaced degenerated to ``"./"`` for a
        # repo-root match, and recording only files that emitted a route
        # meant adding the first endpoint to a module never marked it stale.
        discovered_from.extend(file_provenance(root, [cs_file]))
        try:
            source_text = cs_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = cs_file.relative_to(root).as_posix()
        _process_file(source_text, rel_path, nodes, edges)

    return StrategyResult(nodes, edges, discovered_from)


def _process_file(
    source_text: str,
    rel_path: str,
    nodes: dict[str, dict],
    edges: list[dict],
) -> bool:
    """Process one file: emit controller, route, and minimal-API nodes.

    Returns ``True`` if any node or edge was emitted (so the caller can
    record the file under ``discovered_from``).
    """
    any_emitted = False

    for controller in _scan_controllers(source_text):
        controller_id = symbol_id(controller.namespace, controller.name)
        nodes[controller_id] = controller_node(controller, rel_path)
        for route in controller.routes:
            full_path = join_route(
                controller.route_prefix,
                route.template,
                controller_name=controller.name,
                action_name=route.method_name,
            )
            rid = route_id(route.verb, full_path)
            nodes[rid] = route_node(
                verb=route.verb,
                path=full_path,
                file=rel_path,
                declared_in=controller.name,
                method_name=route.method_name,
                confidence="definite",
                source="attribute",
            )
            edges.append(
                {
                    "from": controller_id,
                    "to": rid,
                    "type": "exposes",
                    "props": {
                        "source_strategy": "csharp_aspnet_routes",
                        "confidence": "definite",
                        "method": route.method_name,
                    },
                }
            )
        any_emitted = True

    for minimal in _scan_minimal_api(source_text):
        rid = route_id(minimal.verb, minimal.path)
        # Avoid clobbering an attribute-emitted route with an inferred
        # minimal-API one. The attribute path is the authoritative
        # shape when the same path is declared twice.
        if rid in nodes:
            continue
        nodes[rid] = route_node(
            verb=minimal.verb,
            path=minimal.path,
            file=rel_path,
            declared_in=None,
            method_name=None,
            confidence="inferred",
            source="minimal_api",
        )
        any_emitted = True

    return any_emitted


def _scan_controllers(source_text: str) -> Iterator[Controller]:
    """Yield one ``Controller`` per controller class in *source_text*.

    A class is treated as a controller when (a) the class-level
    attribute window contains ``[Route(...)]`` or ``[ApiController]``,
    or (b) its name ends with ``Controller`` and the body contains at
    least one HTTP-verb attribute. Cases that fall outside both are
    skipped so the strategy does not over-emit on incidentally similar
    shapes.
    """
    namespaces = namespace_spans(source_text)
    for class_match in CLASS_RE.finditer(source_text):
        class_name = class_match.group(1)
        attrs_start = attribute_window_start(
            source_text, class_match.start(),
        )
        class_window = source_text[attrs_start:class_match.start()]
        route_match = _ROUTE_ATTR_RE.search(class_window)
        api_controller = bool(_API_CONTROLLER_RE.search(class_window))

        body_start, body_end = class_body_range(
            source_text, class_match.end(),
        )
        if body_start is None or body_end is None:
            continue

        body = source_text[body_start:body_end]
        routes = list(_scan_routes(body))

        looks_like_controller = (
            route_match is not None
            or api_controller
            or class_name.endswith("Controller")
        )
        if not looks_like_controller or not routes:
            continue

        namespace = namespace_at(class_match.start(), namespaces)
        route_prefix = route_match.group(1) if route_match else ""
        yield Controller(class_name, namespace, route_prefix, routes)


def _scan_routes(body: str) -> Iterator[Route]:
    """Yield ``Route`` per HTTP-verb attribute on a body method."""
    for method_match in _METHOD_RE.finditer(body):
        method_name = method_match.group(1)
        if method_name in {"if", "for", "while", "switch", "return"}:
            continue
        window_start = attribute_window_start(body, method_match.start())
        window = body[window_start:method_match.start()]
        for verb_match in _HTTP_VERB_RE.finditer(window):
            verb = verb_match.group(1)
            template = verb_match.group(2) or ""
            yield Route(verb, template, method_name)


def _scan_minimal_api(source_text: str) -> Iterator[MinimalApi]:
    """Yield ``MinimalApi`` per ``.MapVerb("/path", ...)`` callsite."""
    for match in _MINIMAL_API_RE.finditer(source_text):
        yield MinimalApi(match.group(1), match.group(2))


__all__ = ["extract"]
