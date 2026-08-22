"""Strategy: express HTTP routes (ADR 0064 criterion 3, TypeScript / JS).

Recognises the two express handler-registration callsite grammars that
name a literal path plus an HTTP method (see ADR 0071 for the sibling
axum / gin Rust/Go route strategies this mirrors):

1. **Direct verb call** -- ``app.get("/path", handler)`` /
   ``router.post("/path", h)`` mints one ``route:GET:/path`` node. The
   HTTP method is read from the registration method name (``get`` /
   ``post`` / ``put`` / ``delete`` / ``patch`` / ``head`` / ``options`` /
   ``all``) in :data:`EXPRESS_VERBS`. ``all`` is kept as the ``ALL``
   pseudo-verb (express genuinely routes every method through it).
2. **Route chaining** -- ``app.route("/users").get(list).post(create)``
   registers a handler per method on one path, so it explodes into one
   route node per chained verb (``GET`` and ``POST`` here). The path is
   the literal first argument of ``.route(...)``.

The path is the literal first string argument and must be
server-relative (start with ``/``); a registration whose first argument
is not a ``/``-rooted string literal is skipped. This is the
discriminator that separates a real route from the express
settings-getter (``app.get("view engine")`` returns a setting, not a
route) and from an unrelated ``.get("key")`` on a ``Map`` -- both of
which would otherwise satisfy the flat ``.<verb>(...)`` shape. Extraction
is additionally gated on a real ``express`` import / ``require`` so a
file that never touches express is skipped wholesale.

The strategy is regex-based (mirroring :mod:`weld.strategies.axum` and
:mod:`weld.strategies.gin`) rather than tree-sitter AST-based: express
registration callsites are lexically regular and a regex keeps the
strategy free of any grammar dependency, which matters here because the
TS / JS Tier-1 corpus may be pure ``.js`` (no tree-sitter TS grammar).

For every route the strategy also emits a diagnostic ``exposes`` edge
from the declaring boundary *file* node to the route. Route-node
presence -- not the edge -- is the criterion-3 gate, because the route
callsite names only the receiver + path, not a statically resolvable
router symbol.

Determinism (ADR 0012): routes are de-duplicated by id and emitted in
sorted ``(verb, path)`` order; no set iterates into output order.

Layering: imports only :mod:`weld.strategies._helpers`,
:mod:`weld.strategies._express_routes_helpers`, and stdlib. No
``weld.runtime`` import (gate-pinned layering invariant).

Static-only: no imports are followed, no runtime hooks run.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._express_routes_helpers import (
    EXPRESS_VERBS,
    ExpressRoute,
    boundary_file_id,
    boundary_file_node,
    exposes_edge,
    route_id,
    route_node,
)
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._provenance import file_provenance

#: Matches an ``express`` import / require. Covers ES-module
#: ``import express from 'express'`` / ``import { Router } from "express"``,
#: bare ``import 'express'``, and CommonJS ``require('express')`` /
#: ``require("express")``. The ``express`` token is quote- or word-bounded
#: so a longer specifier (``express-session`` / ``express-rate-limit``)
#: does not satisfy the gate -- those are distinct packages that do not
#: themselves register routes.
_EXPRESS_IMPORT_RE = re.compile(
    r"""(?x)
    (?:
        \bfrom\s*['"]express['"]          # import ... from 'express'
      | \bimport\s*['"]express['"]        # bare import 'express'
      | \brequire\s*\(\s*['"]express['"]  # require('express')
    )
    """,
)

#: Lower-cased verb set for O(1) registration-method validation.
_VERB_SET: frozenset[str] = frozenset(v.lower() for v in EXPRESS_VERBS)

#: Alternation of the recognised verbs for the direct-call regex. Sorted
#: longest-first so ``options`` is tried before ``post`` etc. (regex
#: alternation is ordered; longest-first avoids a short prefix winning).
_VERB_ALT: str = "|".join(sorted(_VERB_SET, key=len, reverse=True))

#: Matches a direct ``<receiver>.<verb>("/path"`` registration. Group 1 is
#: the verb (validated by construction against :data:`_VERB_SET`); group 2
#: is the literal path. A leading ``.`` requires a receiver so a bare
#: ``get("/x")`` call cannot match; ``\b`` before the receiver-dot is not
#: required because the dot itself bounds the method name.
_VERB_CALL_RE = re.compile(
    rf'\.\s*({_VERB_ALT})\s*\(\s*(["\'])([^"\']*)\2',
)

#: Matches the ``<receiver>.route("/path")`` opener of the chained form.
#: Group 1 is the literal path. The chained verbs that follow are scanned
#: by :func:`_verbs_in_chain` over the text up to the next statement break.
_ROUTE_CHAIN_RE = re.compile(r'\.\s*route\s*\(\s*(["\'])([^"\']*)\1\s*\)')

#: Matches a chained ``.verb(`` token in a ``.route(...).get(...)`` chain.
#: Only names in :data:`_VERB_SET` survive (validated by the caller); a
#: chained ``.all(`` is the catch-all pseudo-verb.
_CHAIN_VERB_RE = re.compile(r"\.\s*([a-z]+)\s*\(")

#: Glob fallback when ``source['glob']`` is absent. Express apps are JS or
#: TS; the default covers both plus the ``x`` (jsx/tsx) variants.
_DEFAULT_GLOB: str = "**/*.{ts,js,mjs,cjs,tsx,jsx}"


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract express route nodes + diagnostic exposes edges from TS/JS src."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", _DEFAULT_GLOB)
    excludes = source.get("exclude", [])
    candidates = resolve_glob(root, pattern, excludes)

    for src_file in candidates:
        if not src_file.is_file():
            continue
        # Provenance is this file, recorded before the read (bd od2a): the
        # parent directory it replaced degenerated to ``"./"`` for a
        # repo-root match (``index.js`` is where an Express app usually
        # lives), and recording only files that emitted a route meant
        # adding the first ``app.get`` to a module never marked it stale.
        discovered_from.extend(file_provenance(root, [src_file]))
        try:
            text = src_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _EXPRESS_IMPORT_RE.search(text):
            continue

        rel_path = src_file.relative_to(root).as_posix()
        _emit_for_file(text, rel_path, nodes, edges)

    return StrategyResult(nodes, edges, discovered_from)


def _strip_line_comments(text: str) -> str:
    """Drop the ``// ...`` tail of each line so commented-out route
    callsites do not mint routes.

    A conservative line-level strip: anything from the first ``//`` to
    end-of-line is removed. This can over-trim a ``//`` inside a string
    literal on a route line (e.g. a URL with ``//``), but express route
    paths are server-relative (``/users``) and never contain ``//``, so
    no real route is lost. Block comments (``/* ... */``) are not
    stripped; a route callsite inside one is rare and the express-import
    gate already bounds false positives.
    """
    out: list[str] = []
    for line in text.splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _verbs_in_chain(chain: str) -> list[str]:
    """Return the upper-cased express verbs named in a ``.route`` chain.

    ``chain`` is the run of chained method calls following a
    ``.route("/p")`` opener (``.get(h).post(h2)``). Every ``.verb(`` token
    is scanned; only names in :data:`EXPRESS_VERBS` survive, so a chained
    ``.all(`` catch-all is kept while a non-verb chain method
    (``.name(...)``) is dropped. Order is not significant -- the caller
    de-duplicates and sorts.
    """
    return [
        name.upper()
        for name in _CHAIN_VERB_RE.findall(chain)
        if name in _VERB_SET
    ]


def _chain_span(text: str, start: int) -> str:
    """Return the contiguous chained-call run that begins at *start*.

    Walks forward from the close paren of a ``.route("/p")`` opener and
    consumes only the *contiguous* ``.verb(...)`` chain: at each step it
    skips inter-call whitespace (newlines included, so a fluent multi-line
    chain is captured) and, if the next non-space character is a ``.``,
    consumes the following balanced-paren call; otherwise the chain has
    ended and the walk stops. This bounds the span to the chain itself --
    a following unrelated statement (``app.get(...)`` after a ``;`` or a
    newline that is *not* immediately a ``.``) is never folded in, even
    under JS automatic semicolon insertion.
    """
    i = start
    n = len(text)
    while i < n:
        j = i
        while j < n and text[j] in " \t\r\n":
            j += 1
        if j >= n or text[j] != ".":
            break
        # Consume one ``.method(...)`` call with balanced parens.
        k = j + 1
        while k < n and text[k] != "(":
            # A non-call chain member (``.foo.bar``) or a stray dot ends
            # the chain; only ``.name(`` continues it.
            if text[k] in ";\n":
                return text[start:i]
            k += 1
        if k >= n:
            break
        depth = 0
        while k < n:
            if text[k] == "(":
                depth += 1
            elif text[k] == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        i = k
    return text[start:i]


def _scan_routes(text: str) -> list[ExpressRoute]:
    """Return every express route callsite in *text*, de-duplicated + sorted.

    Two grammars contribute:

    * ``<receiver>.<verb>("/path", ...)`` -> one route, ``verb_call``.
    * ``<receiver>.route("/path").get(h).post(h)`` -> one route per chained
      verb, ``route_chain``.

    Results are collected into a set keyed by the route tuple, then sorted
    so the caller emits route nodes in a stable ``(verb, path, source)``
    order regardless of source layout (ADR 0012 determinism). ``//`` line
    comments are stripped first so a commented-out registration does not
    surface as a route.
    """
    text = _strip_line_comments(text)
    found: set[ExpressRoute] = set()
    for match in _VERB_CALL_RE.finditer(text):
        path = match.group(3)
        if not _is_route_path(path):
            continue
        verb = match.group(1).upper()
        found.add(ExpressRoute(verb, path, "verb_call"))
    for match in _ROUTE_CHAIN_RE.finditer(text):
        path = match.group(2)
        if not _is_route_path(path):
            continue
        chain = _chain_span(text, match.end())
        for verb in _verbs_in_chain(chain):
            found.add(ExpressRoute(verb, path, "route_chain"))
    return sorted(found)


def _is_route_path(path: str) -> bool:
    """Return True when *path* is a server-relative express route path.

    Express route registration paths are server-relative and start with
    ``/`` (``/users``, ``/users/:id``, ``/assets/*``). A non-``/`` string
    argument is almost always something else passing through the same flat
    ``.<verb>("...")`` shape -- the express settings *getter*
    (``app.get("view engine")``), a ``Map``/cache ``.get("key")``, or a
    feature-flag lookup -- so it is dropped to keep the route population
    free of junk. This costs the rare ``app.get("")`` root registration,
    which is not idiomatic.
    """
    return path.startswith("/")


def _emit_for_file(
    text: str, rel_path: str, nodes: dict[str, dict], edges: list[dict],
) -> bool:
    """Emit route nodes + exposes edges for one express source file.

    Returns ``True`` when at least one route was emitted so the caller
    records the parent directory under ``discovered_from``. When two
    callsites resolve the same ``route:<VERB>:<path>`` id (e.g. a path
    registered via both the direct and chained form) the first sorted
    entry wins and the duplicate is skipped, so the emitted-edge set
    stays one-per-route.
    """
    any_emitted = False
    boundary_id = boundary_file_id(rel_path)
    for route in _scan_routes(text):
        rid = route_id(route.verb, route.path)
        if rid in nodes:
            continue
        nodes[rid] = route_node(
            verb=route.verb, path=route.path, rel_path=rel_path,
            source=route.source,
        )
        # Placeholder boundary file node so the exposes edge survives the
        # dangling-edge post-pass when tree_sitter is not paired; a real
        # tree_sitter file node overwrites it via ``nodes.update`` when it
        # is. ``setdefault`` never clobbers an already-richer node.
        nodes.setdefault(boundary_id, boundary_file_node(rel_path))
        edges.append(exposes_edge(boundary_id, rid))
        any_emitted = True
    return any_emitted


__all__ = ["extract"]
