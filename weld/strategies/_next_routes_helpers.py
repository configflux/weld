"""Path derivation and node payloads for the Next.js app-router strategy.

Split out of :mod:`weld.strategies.next` so the file-convention scanner and
the URL derivation live apart, mirroring the
:mod:`weld.strategies._express_routes_helpers` precedent -- and because the
derivation is the part with the interesting rules, so it is the part worth
testing without going through a filesystem walk.

**The whole convention in one sentence:** a handler's URL is the chain of
directories between the app directory and the file itself, so
``apps/web/src/app/api/orders/route.ts`` serves ``/api/orders``.

Everything else is the exceptions Next.js layers on that chain, and each one
is a rule here:

* **The app directory is the last path segment named exactly ``app``.** Both
  layouts ``create-next-app`` writes (``app/`` and ``src/app/``) are the
  innermost ``app`` on the path, and taking the last one is what keeps a
  monorepo package *called* ``app`` (``packages/app/src/app/api/...``) from
  being mistaken for it. The cost is a literal URL segment named ``app``
  inside the app directory (``app/app/page.tsx`` reads as ``/`` rather than
  ``/app``), which is the rarer shape by a wide margin.
* **Route groups do not appear in the URL.** ``app/(marketing)/about/page.tsx``
  serves ``/about``: a parenthesised segment organises files, not URLs. The
  same rule covers the parenthesised intercepting-route markers (``(.)``,
  ``(..)``) well enough that they do not mint a segment nobody typed.
* **Parallel-route slots do not appear either.** ``@modal`` names a slot
  rendered *into* a layout, not a path the browser asks for.
* **A private folder is not routable at all.** Next excludes ``_folder`` and
  everything under it from routing, so a ``route.ts`` beneath one is not a
  route and this module returns ``None`` for it rather than inventing a URL.
* **Dynamic segments keep their source spelling.** ``[id]``, ``[...slug]`` and
  ``[[...slug]]`` stay verbatim in the path, exactly as express keeps ``:id``
  and axum keeps ``{id}``: the route node records the shape the framework
  declares, not a normalisation of it.

What is deliberately *not* here (ADR 0142 "what we deliberately do not do"):
the pages router, middleware matchers and server actions. Route handlers and
pages first.

Layering: stdlib and :mod:`weld.strategies._ts_route_helpers` (pure id and
payload helpers) only. The ``authority`` / ``confidence`` literals are plain
strings rather than :mod:`weld.runtime` enums -- that import is a gate-pinned
layering violation, and every route strategy in the tree stamps them the same
way.
"""

from __future__ import annotations

from typing import NamedTuple

#: ``source_strategy`` value stamped on every node and edge this strategy
#: emits. Distinct from ``express`` on purpose: the two run over overlapping
#: globs in the same Node repo, and a consumer asking "which routes did the
#: app router contribute" must be able to tell them apart.
NEXT_SOURCE_STRATEGY: str = "next"

#: The HTTP methods Next.js recognises as route-handler exports. Verbatim
#: from the app-router contract, which is why there is no ``ALL`` pseudo-verb
#: here as there is for express: a Next handler module exports one function
#: per method it serves and has no catch-all registration.
NEXT_VERBS: tuple[str, ...] = (
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS",
)

#: ``props.route_source`` for a route minted from an exported HTTP-verb
#: function in a ``route.*`` handler module.
ROUTE_SOURCE_HANDLER: str = "route_handler"

#: ``props.route_source`` for the implicit ``GET`` a ``page.*`` serves. A page
#: registers no handler -- the framework serves its default-exported component
#: over ``GET`` -- so consumers that want only hand-written handlers filter on
#: this rather than having to know which files are pages.
ROUTE_SOURCE_PAGE: str = "page"

#: The app-directory segment name. Named rather than inlined because two
#: independent rules read it (finding the anchor, and reporting what was not
#: found).
APP_DIR_SEGMENT: str = "app"


class NextRoute(NamedTuple):
    """One app-router route: ``(verb, path, source)``.

    ``verb`` is the HTTP method -- the exported function's name for a route
    handler, the implicit ``GET`` for a page. ``path`` is the derived URL.
    ``source`` is :data:`ROUTE_SOURCE_HANDLER` or :data:`ROUTE_SOURCE_PAGE`
    and reaches the node as ``props.route_source``, the same prop express
    uses to record which callsite grammar produced a route.
    """

    verb: str
    path: str
    source: str


def _is_private_segment(segment: str) -> bool:
    """``_folder`` -- opted out of routing, along with everything under it."""
    return segment.startswith("_")


def _is_urlless_segment(segment: str) -> bool:
    """A directory that organises files without contributing a URL segment.

    Route groups (``(marketing)``, and the parenthesised intercepting-route
    markers) and parallel-route slots (``@modal``).
    """
    return (
        segment.startswith("(") and segment.endswith(")")
    ) or segment.startswith("@")


def route_path(rel_path: str) -> str | None:
    """Return the URL *rel_path* serves, or ``None`` if it serves none.

    ``None`` -- rather than a guessed path -- when the file is not under an
    app directory at all, or when a private ``_folder`` on the chain opts it
    out of routing. Both are "this is not a route", and a route node minted
    for either would be a URL no request can reach.

    The path always starts with ``/``; a handler directly in the app
    directory serves ``/``.
    """
    parts = rel_path.split("/")
    directories = parts[:-1]
    if APP_DIR_SEGMENT not in directories:
        return None
    anchor = len(directories) - 1 - directories[::-1].index(APP_DIR_SEGMENT)
    below = directories[anchor + 1:]
    if any(_is_private_segment(segment) for segment in below):
        return None
    segments = [
        segment for segment in below if not _is_urlless_segment(segment)
    ]
    return "/" + "/".join(segments)


def route_node(*, verb: str, path: str, rel_path: str, source: str) -> dict:
    """Build an app-router route-node payload (ADR 0086 inbound HTTP surface).

    The same prop set the express / axum / gin / flask route nodes carry, so
    a cross-language "what does this expose" query sees one shape. The node
    language is ``typescript`` for a ``.js`` route handler too, mirroring
    express: the strategy is keyed to the TypeScript / JS Tier-1 ladder, and
    the file's own dialect is on the file node.
    """
    return {
        "type": "route",
        "label": f"{verb.upper()} {path}",
        "props": {
            "file": rel_path,
            "method": verb.upper(),
            "path": path,
            "source_strategy": NEXT_SOURCE_STRATEGY,
            "language": "typescript",
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


__all__ = [
    "APP_DIR_SEGMENT",
    "NEXT_SOURCE_STRATEGY",
    "NEXT_VERBS",
    "NextRoute",
    "ROUTE_SOURCE_HANDLER",
    "ROUTE_SOURCE_PAGE",
    "route_node",
    "route_path",
]
