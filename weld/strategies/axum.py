"""Strategy: axum HTTP routes (ADR 0064 criterion 3 / ADR 0071, Rust).

Recognises the axum ``Router`` route-registration callsites that name a
literal path plus one or more ``axum::routing`` method-router builders:

1. **Single method** -- ``.route("/path", get(handler))`` mints one
   ``route:GET:/path`` node. The HTTP method is read from the builder
   function name (``get`` / ``post`` / ``put`` / ``delete`` / ``patch`` /
   ``head`` / ``options`` / ``trace``) in :data:`AXUM_VERBS`.
2. **Method chaining** -- ``.route("/users", get(list).post(create))``
   registers a handler per method on one path, so it explodes into one
   route node per builder named in the second argument (``GET`` and
   ``POST`` here).

The path is the literal first string argument of ``.route(...)``; the
method-router expression is everything up to the matching close paren of
the callsite. A non-axum builder name (axum has no ``any`` builder) is
ignored rather than minting a junk verb.

The strategy is regex-based (mirroring :mod:`weld.strategies.gin` and
:mod:`weld.strategies.csharp_aspnet_routes`'s minimal-API scan) rather
than tree-sitter AST-based: axum registration callsites are lexically
regular and a regex keeps the strategy free of any grammar dependency.
Extraction is gated on a real ``axum`` ``use`` import so unrelated
``.route(...)`` callsites (e.g. an unrelated builder) do not over-fire.

For every route the strategy also emits a diagnostic ``exposes`` edge
from the declaring boundary *file* node to the route. Per ADR 0071 § 1
route-node presence -- not the edge -- is the criterion-3 gate, because
the route callsite names only the builder + path, not a statically
resolvable router symbol.

Determinism (ADR 0012): routes are de-duplicated by id and emitted in
sorted ``(verb, path)`` order; no set iterates into output order.

Layering: imports only :mod:`weld.strategies._helpers`,
:mod:`weld.strategies._axum_routes_helpers`, and stdlib. No
``weld.runtime`` import (gate-pinned per ADR 0071 § 1).

Static-only: no imports are followed, no runtime hooks run.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._axum_routes_helpers import (
    AXUM_VERBS,
    AxumRoute,
    boundary_file_id,
    boundary_file_node,
    exposes_edge,
    route_id,
    route_node,
)
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._provenance import file_provenance

#: Matches an ``axum`` ``use`` import. ``use axum::...`` / ``use axum;``
#: both fire; ``use axum_extra::...`` does not (the word boundary plus
#: ``::``-or-``;`` lookahead stops a longer-crate false positive). A
#: re-export alias (``use axum as web;``) is rare and out of scope.
_AXUM_IMPORT_RE = re.compile(r"\buse\s+axum\b\s*(?:::|;)")

#: Matches the opening of a ``.route("/path",`` callsite. Group 1 is the
#: literal path. A leading ``.`` requires a receiver so a bare
#: ``route(`` call cannot match. The remainder of the callsite (the
#: method-router argument) is scanned for builder names by
#: :func:`_verbs_in_arg` after the path is captured.
_ROUTE_RE = re.compile(r'\.route\s*\(\s*"([^"]*)"\s*,')

#: Matches an ``axum::routing`` builder function call (``get(`` ...). The
#: name is validated against :data:`AXUM_VERBS` before a route is minted,
#: so a non-axum identifier that happens to look like a call is dropped.
_BUILDER_RE = re.compile(r"\b([a-z]+)\s*\(")

#: Glob fallback when ``source['glob']`` is absent.
_DEFAULT_GLOB: str = "**/*.rs"

#: Lower-cased verb set for O(1) builder-name validation.
_VERB_SET: frozenset[str] = frozenset(v.lower() for v in AXUM_VERBS)


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract axum route nodes and diagnostic exposes edges from Rust src."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", _DEFAULT_GLOB)
    excludes = source.get("exclude", [])
    candidates = resolve_glob(root, pattern, excludes)

    for rs_file in candidates:
        if not rs_file.is_file():
            continue
        # Provenance is this file, recorded before the read (bd od2a): the
        # parent directory it replaced degenerated to ``"./"`` for a
        # repo-root match, and recording only files that emitted a route
        # meant adding the first ``.route()`` to a module never marked it
        # stale.
        discovered_from.extend(file_provenance(root, [rs_file]))
        try:
            text = rs_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _AXUM_IMPORT_RE.search(text):
            continue

        rel_path = rs_file.relative_to(root).as_posix()
        _emit_for_file(text, rel_path, nodes, edges)

    return StrategyResult(nodes, edges, discovered_from)


def _strip_line_comments(text: str) -> str:
    """Drop the ``// ...`` tail of each line so commented-out route
    callsites do not mint routes.

    A conservative line-level strip: anything from the first ``//`` to
    end-of-line is removed. This can over-trim a ``//`` that appears
    inside a string literal on a route line (e.g. a URL with ``//``),
    but axum route paths are server-relative (``/users``) and never
    contain ``//``, so no real route is lost. Block comments
    (``/* ... */``) are not stripped; a route callsite inside one is
    rare and the axum-import gate already bounds false positives.
    """
    out: list[str] = []
    for line in text.splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _verbs_in_arg(arg: str) -> list[str]:
    """Return the upper-cased axum verbs named in a method-router *arg*.

    ``arg`` is the method-router expression of one ``.route(...)``
    callsite (``get(h).post(h2)``). Every ``axum::routing`` builder name
    is scanned; only names in :data:`AXUM_VERBS` survive, so a typo'd or
    non-axum builder is dropped rather than minting a junk verb. Order is
    not significant -- the caller de-duplicates and sorts.
    """
    return [
        name.upper()
        for name in _BUILDER_RE.findall(arg)
        if name in _VERB_SET
    ]


def _route_arg_span(text: str, comma_idx: int) -> str:
    """Return the method-router argument text after a ``.route(`` comma.

    Starting just past *comma_idx* (the comma that follows the path
    literal), walk forward tracking paren depth so the returned span
    stops at the ``.route(...)`` callsite's matching close paren -- not
    at a close paren belonging to a nested builder call. The opening
    ``.route(`` paren is depth 1 on entry; the span ends when depth
    returns to 0.
    """
    depth = 1
    start = comma_idx + 1
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i]


def _scan_routes(text: str) -> list[AxumRoute]:
    """Return every axum route callsite in *text*, de-duplicated + sorted.

    Each ``.route("/path", <method-router>)`` callsite contributes one
    route per axum verb named in its method-router argument. Results are
    collected into a set keyed by the route tuple, then sorted so the
    caller emits route nodes in a stable ``(verb, path, source)`` order
    regardless of source layout (ADR 0012 determinism). ``//`` line
    comments are stripped first so a commented-out registration does not
    surface as a route.
    """
    text = _strip_line_comments(text)
    found: set[AxumRoute] = set()
    for match in _ROUTE_RE.finditer(text):
        path = match.group(1)
        arg = _route_arg_span(text, match.end() - 1)
        for verb in _verbs_in_arg(arg):
            found.add(AxumRoute(verb, path, "route_builder"))
    return sorted(found)


def _emit_for_file(
    text: str, rel_path: str, nodes: dict[str, dict], edges: list[dict],
) -> bool:
    """Emit route nodes + exposes edges for one axum source file.

    Returns ``True`` when at least one route was emitted so the caller
    records the parent directory under ``discovered_from``. When two
    callsites resolve the same ``route:<VERB>:<path>`` id (e.g. ``GET``
    declared for the same path twice) the first sorted entry wins and the
    duplicate is skipped, so the emitted-edge set stays one-per-route.
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
        # dangling-edge post-pass when tree_sitter is not paired; when it is,
        # the placeholder's ``inferred`` rank loses the node id to the real
        # file node under the ADR 0103 merge veto, in either entry order
        # (bd iurvv). ``setdefault`` is the same refusal within one result.
        nodes.setdefault(boundary_id, boundary_file_node(rel_path))
        edges.append(exposes_edge(boundary_id, rid))
        any_emitted = True
    return any_emitted


__all__ = ["extract"]
