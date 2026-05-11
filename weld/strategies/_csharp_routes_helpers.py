"""Helpers for :mod:`weld.strategies.csharp_aspnet_routes` (ADR 0056 Wave 2).

Splits route-id, node-construction, and MVC token-expansion helpers
out of the public strategy module so the strategy stays under the
400-line file-size cap. Imports are unidirectional: only
``csharp_aspnet_routes`` imports this module; this module does not
import any other strategy (per the ADR 0024 convention).
"""

from __future__ import annotations

from typing import NamedTuple


class Route(NamedTuple):
    """A single ``[HttpVerb(...)]`` declaration on a controller method."""
    verb: str
    template: str
    method_name: str


class Controller(NamedTuple):
    """A parsed controller class with its routes."""
    name: str
    namespace: str
    route_prefix: str
    routes: list[Route]


class MinimalApi(NamedTuple):
    """A ``.MapVerb("/path", ...)`` callsite extracted lexically."""
    verb: str
    path: str


def symbol_id(namespace: str, name: str) -> str:
    """Return the canonical ``symbol:csharp:<ns>.<name>`` id."""
    qualified = f"{namespace}.{name}" if namespace else name
    return f"symbol:csharp:{qualified}"


def route_id(verb: str, path: str) -> str:
    """Return the canonical ``route:<VERB>:<path>`` id.

    Matches the FastAPI strategy convention so polyglot route searches
    return both Python and C# endpoints under the same prefix.
    """
    return f"route:{verb.upper()}:{path}"


def controller_node(controller: Controller, file: str) -> dict:
    """Build the controller class node payload."""
    return {
        "type": "symbol",
        "label": (
            f"{controller.namespace}.{controller.name}"
            if controller.namespace
            else controller.name
        ),
        "props": {
            "file": file,
            "name": controller.name,
            "namespace": controller.namespace,
            "kind": "controller",
            "language": "csharp",
            "route_prefix": controller.route_prefix,
            "source_strategy": "csharp_aspnet_routes",
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["implementation"],
        },
    }


def route_node(
    *,
    verb: str,
    path: str,
    file: str,
    declared_in: str | None,
    method_name: str | None,
    confidence: str,
    source: str,
) -> dict:
    """Return the node dict for an emitted ``route:`` id.

    ``confidence`` is set per call: ``definite`` for attribute-based
    routes, ``inferred`` for minimal-API string-literal scrapes.
    ``source`` is recorded under ``props.route_source`` so downstream
    consumers can filter the two populations independently.
    """
    props: dict = {
        "file": file,
        "method": verb.upper(),
        "path": path,
        "language": "csharp",
        "framework": "aspnetcore",
        "route_source": source,
        "source_strategy": "csharp_aspnet_routes",
        "authority": (
            "canonical" if confidence == "definite" else "derived"
        ),
        "confidence": confidence,
        "roles": ["implementation"],
        "protocol": "http",
        "surface_kind": "request_response",
        "transport": "http",
        "boundary_kind": "inbound",
    }
    if declared_in is not None:
        props["controller"] = declared_in
    if method_name is not None:
        props["function"] = method_name
    return {
        "type": "route",
        "label": f"{verb.upper()} {path}",
        "props": props,
    }


def join_route(
    prefix: str, template: str, *, controller_name: str, action_name: str,
) -> str:
    """Concatenate prefix+template and expand MVC replacement tokens.

    Rules:

    - ``/`` is the path separator. Both inputs are stripped of leading
      and trailing slashes before joining so ``Route("api/[controller]")``
      + ``HttpGet("{id}")`` yields ``/api/orders/{id}`` (after
      ``[controller]`` expansion).
    - The MVC ``[controller]`` token expands to the class name with
      its trailing ``Controller`` suffix removed, lower-cased.
    - The MVC ``[action]`` token expands to the method name verbatim.
    - Other ``[token]`` patterns are left intact so downstream
      consumers can still inspect them.
    - The result is prefixed with ``/`` for parity with the FastAPI
      strategy's route ids.
    """
    pieces: list[str] = []
    for raw in (prefix, template):
        if not raw:
            continue
        pieces.append(raw.strip("/"))
    joined = "/".join(p for p in pieces if p)
    joined = joined.replace(
        "[controller]",
        _strip_controller_suffix(controller_name).lower(),
    )
    joined = joined.replace("[action]", action_name)
    return "/" + joined if joined else "/"


def _strip_controller_suffix(class_name: str) -> str:
    """Return *class_name* without a trailing ``Controller`` suffix."""
    suffix = "Controller"
    if class_name.endswith(suffix) and class_name != suffix:
        return class_name[: -len(suffix)]
    return class_name


__all__ = [
    "Controller",
    "MinimalApi",
    "Route",
    "controller_node",
    "join_route",
    "route_id",
    "route_node",
    "symbol_id",
]
