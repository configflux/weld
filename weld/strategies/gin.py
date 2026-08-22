"""Strategy: gin HTTP routes (ADR 0064 criterion 3 / ADR 0071, Go GO-2).

Recognises the gin handler-registration callsites that name an HTTP
method and a literal path:

1. **Verb methods** -- ``r.GET("/path", handler)`` and the
   ``POST`` / ``PUT`` / ``DELETE`` / ``PATCH`` / ``HEAD`` / ``OPTIONS``
   siblings on any router-or-group receiver. Each becomes one
   ``route:<VERB>:<path>`` node.
2. **``Any``** -- ``r.Any("/path", handler)`` registers a handler for
   every verb, so it explodes into one route node per concrete verb in
   :data:`weld.strategies._gin_routes_helpers.GIN_VERBS`.
3. **``Handle``** -- ``r.Handle("GET", "/path", handler)`` takes the
   method as a literal string argument; the method is read from arg 0
   and the path from arg 1.

The strategy is regex-based (mirroring
:mod:`weld.strategies.csharp_aspnet_routes`'s minimal-API scan) rather
than tree-sitter AST-based: gin registration callsites are lexically
regular and a regex keeps the strategy free of any grammar dependency.
Extraction is gated on a real ``github.com/gin-gonic/gin`` import so
unrelated ``.GET(...)`` callsites (e.g. an HTTP-client builder) do not
over-fire.

For every route the strategy also emits a diagnostic ``exposes`` edge
from the declaring boundary *file* node to the route. Per ADR 0071 § 1
route-node presence -- not the edge -- is the criterion-3 gate, because
gin handlers are overwhelmingly inline closures (``func(c
*gin.Context)``) with no statically named handler symbol.

Determinism (ADR 0012): routes are de-duplicated by id and emitted in
sorted ``(verb, path)`` order; no set iterates into output order.

Layering: imports only :mod:`weld.strategies._helpers`,
:mod:`weld.strategies._gin_routes_helpers`, and stdlib. No
``weld.runtime`` import (gate-pinned per ADR 0071 § 1).

Static-only: no imports are followed, no runtime hooks run.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._gin_routes_helpers import (
    GIN_VERBS,
    GinRoute,
    boundary_file_id,
    boundary_file_node,
    exposes_edge,
    route_id,
    route_node,
)
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._provenance import file_provenance

#: Matches the gin module import path inside an import string literal.
#: Covers the root package and any subpackage (``gin-gonic/gin/render``).
_GIN_IMPORT_RE = re.compile(r"github\.com/gin-gonic/gin")

#: Matches ``<recv>.GET("/path"`` and the verb siblings. Group 1 is the
#: verb (upper-case in source), group 2 is the literal path. A leading
#: ``.`` requires a receiver so a bare ``GET(`` call cannot match.
_VERB_RE = re.compile(
    r"\.(" + "|".join(GIN_VERBS) + r")\s*\(\s*\"([^\"]*)\"",
)

#: Matches ``<recv>.Any("/path"``. Group 1 is the literal path.
_ANY_RE = re.compile(r"\.Any\s*\(\s*\"([^\"]*)\"")

#: Matches ``<recv>.Handle("GET", "/path"``. Group 1 is the method
#: literal, group 2 is the path literal. The method is upper-cased and
#: validated against :data:`GIN_VERBS` so a typo'd / dynamic verb is
#: dropped rather than minted into a junk route.
_HANDLE_RE = re.compile(
    r"\.Handle\s*\(\s*\"([^\"]*)\"\s*,\s*\"([^\"]*)\"",
)

#: Glob fallback when ``source['glob']`` is absent.
_DEFAULT_GLOB: str = "**/*.go"


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract gin route nodes and diagnostic exposes edges from Go src."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", _DEFAULT_GLOB)
    excludes = source.get("exclude", [])
    candidates = resolve_glob(root, pattern, excludes)

    for go_file in candidates:
        if not go_file.is_file():
            continue
        # Provenance is this file, recorded before the read (bd od2a): the
        # parent directory it replaced degenerated to ``"./"`` for a
        # repo-root match (``main.go`` is exactly that), and recording only
        # files that emitted a route meant adding the first ``r.GET`` to a
        # module never marked it stale.
        discovered_from.extend(file_provenance(root, [go_file]))
        try:
            text = go_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _GIN_IMPORT_RE.search(text):
            continue

        rel_path = go_file.relative_to(root).as_posix()
        _emit_for_file(text, rel_path, nodes, edges)

    return StrategyResult(nodes, edges, discovered_from)


def _strip_line_comments(text: str) -> str:
    """Drop the ``// ...`` tail of each line so commented-out route
    callsites do not mint routes.

    A conservative line-level strip: anything from the first ``//`` to
    end-of-line is removed. This can over-trim a ``//`` that appears
    inside a string literal on a route line (e.g. a URL with ``//``),
    but gin route paths are server-relative (``/users``) and never
    contain ``//``, so no real route is lost. Block comments
    (``/* ... */``) are not stripped; a route callsite inside one is
    rare and the gin-import gate already bounds false positives.
    """
    out: list[str] = []
    for line in text.splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _scan_routes(text: str) -> list[GinRoute]:
    """Return every gin route callsite in *text*, de-duplicated + sorted.

    The three callsite grammars (verb method / ``Any`` / ``Handle``) are
    scanned independently, collected into a set keyed by the route
    tuple, then sorted so the caller emits route nodes in a stable
    ``(verb, path, source)`` order regardless of source layout (ADR
    0012 determinism). ``//`` line comments are stripped first so a
    commented-out registration does not surface as a route.
    """
    text = _strip_line_comments(text)
    found: set[GinRoute] = set()
    for match in _VERB_RE.finditer(text):
        found.add(GinRoute(match.group(1).upper(), match.group(2), "verb_method"))
    for match in _ANY_RE.finditer(text):
        path = match.group(1)
        for verb in GIN_VERBS:
            found.add(GinRoute(verb, path, "any"))
    for match in _HANDLE_RE.finditer(text):
        verb = match.group(1).strip().upper()
        if verb not in GIN_VERBS:
            continue
        found.add(GinRoute(verb, match.group(2), "handle"))
    return sorted(found)


def _emit_for_file(
    text: str, rel_path: str, nodes: dict[str, dict], edges: list[dict],
) -> bool:
    """Emit route nodes + exposes edges for one gin source file.

    Returns ``True`` when at least one route was emitted so the caller
    records the parent directory under ``discovered_from``. When two
    callsite grammars resolve the same ``route:<VERB>:<path>`` id (e.g.
    ``GET`` declared both directly and via ``Any``) the first sorted
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
