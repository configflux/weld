"""Strategy: Next.js app-router routes (ADR 0064 criterion 3, TypeScript / JS).

The declared Next.js framework strategy (ADR 0142 D4), joining express, gin
and axum on the ADR 0071 mechanism. Where those three read a *registration
callsite* -- ``app.get("/health", h)``, ``r.GET("/health", h)`` --
the Next.js app router has no callsite to read: the URL is the directory the
file sits in and the HTTP method is the name it exports. So this strategy
reads two file conventions instead:

1. **Route handlers** -- ``app/**/route.{ts,tsx,js,jsx,mjs,cjs}``. Every
   exported function named for an HTTP method
   (:data:`weld.strategies._next_routes_helpers.NEXT_VERBS`) mints one
   ``route:<VERB>:<path>`` node. All three export spellings a real handler
   module uses are recognised: ``export async function GET()``,
   ``export const GET = ...`` and the export list (``export { handler as GET }``,
   including its re-export form).
2. **Pages** -- ``app/**/page.{ts,tsx,js,jsx,mjs,cjs}``. A page is an inbound
   ``GET`` surface: the browser asks for the URL and the server answers with
   the rendered component. It therefore mints a ``route:GET:<path>`` node
   like any other inbound HTTP surface rather than a node type of its own --
   "what URLs does this app expose" is one question, and a second vocabulary
   for half the answer would make it two. ``props.route_source``
   distinguishes the two populations (``page`` vs ``route_handler``) for a
   consumer that wants only hand-written handlers.

The URL derivation -- the directory chain, route groups, parallel slots,
private folders, dynamic segments -- lives next door in
:mod:`weld.strategies._next_routes_helpers`, which documents each rule.

**The discriminator is the file convention, not an import.** express gates on
a real ``express`` import because ``.get("key")`` on a ``Map`` has the same
lexical shape as a route registration; nothing here is that ambiguous. A file
named ``route.ts`` under an ``app/`` directory exporting a function named
``GET`` is an app-router handler in any repository that has one, and a
``page.tsx`` is required to default-export a component -- which is what this
strategy checks before minting a page route, so a stray ``page.ts`` helper
module contributes nothing.

For every route the strategy also emits a diagnostic ``exposes`` edge from
the declaring boundary *file* node to the route. Route-node presence -- not
the edge -- is the criterion-3 gate, matching express.

Determinism (ADR 0012): routes are de-duplicated by id and emitted in sorted
``(verb, path, source)`` order; no set iterates into output order.

Layering: imports only :mod:`weld.strategies` helper modules and stdlib. No
``weld.runtime`` import (gate-pinned layering invariant).

Static-only: no imports are followed, no runtime hooks run.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._next_routes_helpers import (
    NEXT_SOURCE_STRATEGY,
    NEXT_VERBS,
    ROUTE_SOURCE_HANDLER,
    ROUTE_SOURCE_PAGE,
    NextRoute,
    route_node,
    route_path,
)
from weld.strategies._provenance import file_provenance
from weld.strategies._ts_route_helpers import (
    boundary_file_id,
    boundary_file_node,
    exposes_edge,
    route_id,
    strip_line_comments,
)

#: The two app-router file conventions this strategy reads, by file stem.
#: ``layout``/``template``/``loading``/``error`` are deliberately absent: they
#: render *inside* a page rather than answering a URL of their own, so a route
#: node for one would claim a surface no request reaches.
_ROUTE_STEM: str = "route"
_PAGE_STEM: str = "page"
_CLAIMED_STEMS: frozenset[str] = frozenset({_ROUTE_STEM, _PAGE_STEM})

#: Extensions the app router accepts for those files.
_CLAIMED_SUFFIXES: frozenset[str] = frozenset(
    {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
)

#: Glob fallback when ``source['glob']`` is absent. Deliberately the same
#: dialect union express defaults to rather than a ``route|page``-shaped
#: brace glob: the stem filter below is what selects the app-router files,
#: and one glob spelling shared with the sibling strategy is one thing a
#: reader has to learn. Resolution only *stats* the candidates; a file whose
#: name is not claimed is dropped before it is read.
_DEFAULT_GLOB: str = "**/*.{ts,js,mjs,cjs,tsx,jsx}"

#: Verb alternation for the export regexes. Sorted longest-first so
#: ``OPTIONS`` is tried before ``POST`` (regex alternation is ordered).
_VERB_ALT: str = "|".join(sorted(NEXT_VERBS, key=len, reverse=True))

#: ``export function GET(`` / ``export async function GET(``.
_EXPORT_FUNCTION_RE = re.compile(
    rf"\bexport\s+(?:async\s+)?function\s+({_VERB_ALT})\b",
)

#: ``export const GET = ...`` (and the ``let`` / ``var`` spellings).
_EXPORT_BINDING_RE = re.compile(
    rf"\bexport\s+(?:const|let|var)\s+({_VERB_ALT})\b",
)

#: ``export { GET, POST }`` and ``export { handler as GET } from "./h"`` --
#: the form a module that wraps its handlers uses. The braces' contents are
#: split and re-matched by :func:`_exported_names_in_list` rather than being
#: parsed here, so an ``as`` rename is read at its *exported* name.
_EXPORT_LIST_RE = re.compile(r"\bexport\s*\{([^}]*)\}")

#: One entry of an export list, matched at the name the module actually
#: exports: either the whole entry (``GET``) or the tail of a rename
#: (``handler as GET``). Anchored at both ends so ``formatGET`` is not a verb
#: and ``GET as handler`` -- which exports ``handler`` -- is not one either.
_EXPORT_LIST_ENTRY_RE = re.compile(
    rf"(?:^|\bas\s+)({_VERB_ALT})\s*$",
)

#: A default export -- what makes a ``page.*`` file a page. Next requires
#: one; a ``page.ts`` without it is a helper module that happens to be named
#: ``page``. Both spellings count: the inline declaration
#: (``export default function Home()``) and the re-export a page that only
#: re-labels a component uses (``export { default } from "./Home"``,
#: ``export { Home as default }``).
_EXPORT_DEFAULT_RE = re.compile(
    r"\bexport\s+default\b|\bexport\s*\{[^}]*\bdefault\b[^}]*\}",
)


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Next.js app-router route nodes + diagnostic exposes edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", _DEFAULT_GLOB)
    excludes = source.get("exclude", [])

    for src_file in resolve_glob(root, pattern, excludes):
        if not _is_app_router_file(src_file):
            continue
        rel_path = src_file.relative_to(root).as_posix()
        path = route_path(rel_path)
        if path is None:
            continue
        # Provenance is recorded for every routable file, before the read and
        # regardless of whether it yielded a route (the bd od2a rule express
        # follows): adding the first ``export function POST`` to an existing
        # handler module must mark it stale. A file this strategy can never
        # route -- wrong name, or outside any app directory -- is deliberately
        # *not* recorded: unlike express, whose gate is an import a file can
        # gain by being edited, this gate is the path, and a path only changes
        # by a rename, which the file-hash delta already sees.
        discovered_from.extend(file_provenance(root, [src_file]))
        try:
            text = src_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        _emit_for_file(text, rel_path, path, src_file.stem, nodes, edges)

    return StrategyResult(nodes, edges, discovered_from)


def _is_app_router_file(src_file: Path) -> bool:
    """Return True when *src_file* is named like an app-router entry file."""
    return (
        src_file.is_file()
        and src_file.stem in _CLAIMED_STEMS
        and src_file.suffix in _CLAIMED_SUFFIXES
    )


def _exported_names_in_list(body: str) -> list[str]:
    """Return the HTTP verbs an ``export { ... }`` list *body* exports.

    Each comma-separated entry is matched at its end, so ``handler as GET``
    is read as ``GET`` (the exported name) rather than as ``handler``, and a
    non-verb entry (``metadata``) contributes nothing.
    """
    found: list[str] = []
    for entry in body.split(","):
        match = _EXPORT_LIST_ENTRY_RE.search(entry.strip())
        if match is not None:
            found.append(match.group(1))
    return found


def _handler_verbs(text: str) -> list[str]:
    """Return every HTTP verb *text* exports as a route handler.

    All three export spellings, de-duplicated. Order is not significant --
    the caller sorts.
    """
    verbs = {match.group(1) for match in _EXPORT_FUNCTION_RE.finditer(text)}
    verbs |= {match.group(1) for match in _EXPORT_BINDING_RE.finditer(text)}
    for match in _EXPORT_LIST_RE.finditer(text):
        verbs.update(_exported_names_in_list(match.group(1)))
    return sorted(verbs)


def _scan_routes(text: str, path: str, stem: str) -> list[NextRoute]:
    """Return the routes *text* declares for *path*, de-duplicated + sorted.

    ``//`` line comments are stripped first so a commented-out handler export
    does not mint a route. A ``page.*`` file contributes its implicit ``GET``
    only when it actually default-exports something; a ``route.*`` file
    contributes one route per exported verb.
    """
    text = strip_line_comments(text)
    if stem == _PAGE_STEM:
        if _EXPORT_DEFAULT_RE.search(text) is None:
            return []
        return [NextRoute("GET", path, ROUTE_SOURCE_PAGE)]
    return sorted(
        NextRoute(verb, path, ROUTE_SOURCE_HANDLER)
        for verb in _handler_verbs(text)
    )


def _emit_for_file(
    text: str,
    rel_path: str,
    path: str,
    stem: str,
    nodes: dict[str, dict],
    edges: list[dict],
) -> None:
    """Emit route nodes + exposes edges for one app-router file.

    When two files resolve the same ``route:<VERB>:<path>`` id the first
    sorted entry wins and the duplicate is skipped, so the emitted-edge set
    stays one-per-route. Next itself rejects that collision (a ``route.ts``
    and a ``page.tsx`` cannot serve the same segment), so this is a guard
    against a malformed tree rather than a modelled case.
    """
    boundary_id = boundary_file_id(rel_path)
    for route in _scan_routes(text, path, stem):
        rid = route_id(route.verb, route.path)
        if rid in nodes:
            continue
        nodes[rid] = route_node(
            verb=route.verb, path=route.path, rel_path=rel_path,
            source=route.source,
        )
        # Placeholder boundary file node so the exposes edge survives the
        # dangling-edge post-pass when tree_sitter is not paired on the same
        # tree; when it is, the placeholder's ``inferred`` rank loses the node
        # id to the real file node under the ADR 0103 merge veto, in either
        # entry order (bd iurvv). ``setdefault`` is the same refusal within
        # one result.
        nodes.setdefault(
            boundary_id,
            boundary_file_node(
                rel_path, source_strategy=NEXT_SOURCE_STRATEGY,
            ),
        )
        edges.append(
            exposes_edge(
                boundary_id, rid, source_strategy=NEXT_SOURCE_STRATEGY,
            )
        )


__all__ = ["extract"]
