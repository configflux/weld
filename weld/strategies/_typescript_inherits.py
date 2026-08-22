"""TypeScript class/interface inheritance edge emission (ADR 0064 criterion 2).

TypeScript's interface analog is twofold: a class ``extends`` a single
base class and/or ``implements`` one or more interfaces, and an
``interface`` ``extends`` one or more parent interfaces. This module is
the TypeScript counterpart to :mod:`weld.strategies._rust_inherits` /
:mod:`weld.strategies._java_inherits`: it emits one edge per
inheritance clause, originating at the declaring symbol node
(criterion 2's "symbol-origin, not file-origin" contract).

Edge-type mapping (mirrors :mod:`weld.strategies._java_inherits`):

* ``class X extends Base``     -> ``inherits``   edge ``X -> Base``
* ``interface I extends J``    -> ``inherits``   edge ``I -> J``
* ``class X implements Iface`` -> ``implements`` edge ``X -> Iface``

Multiple bases (``implements A, B`` / ``interface I extends J, K``) emit
one edge per base in source order.

Three responsibilities, matching the established cross-language shape:

1. :func:`extract_inheritance` -- regex scan of one source file
   returning ``(decl_short, base_short, base_full, edge_type)`` tuples,
   one per inheritance base. Type-argument lists on the declaration
   (``class X<T>``) and on each base (``implements Iface<T>``) are
   tolerated; the captured names are the bare leading identifiers
   (qualified bases like ``ns.Base`` collapse to their final segment for
   the project-index lookup).

2. :func:`stage_inheritance` -- append records into the run-wide
   accumulator seeded by :func:`build_caches` so :func:`finalise` can
   resolve base short-names against the project-wide symbol index after
   every file has been visited (a base declared in a sibling module
   resolves without an explicit-import join, mirroring the Java model).

3. :func:`finalise` -- consume the accumulator once the tree-sitter file
   loop completes, resolve each ``(decl, base)`` pair against the
   project TypeScript symbol index, and emit the edge. A base defined
   outside the project lands on the shared ``symbol:unresolved:<short>``
   sentinel so the edge target stays referentially closed, exactly as
   the call-edge and Rust/Java inheritance paths do.

The regex never reads beyond the declaration header (up to the opening
brace), so the cost is proportional to source length and the output is
deterministic (records are appended in source order; :func:`finalise`
dedups on ``(from, to, type)``).
"""

from __future__ import annotations

import re

from weld.strategies._ts_call_graph import ts_module_from_path

#: Match a ``class`` / ``interface`` declaration header with optional
#: ``extends`` and ``implements`` clauses. Captures:
#:   (1) the declaring keyword (``class`` / ``interface``)
#:   (2) the declared name
#:   (3) the ``extends`` clause body (comma-separated; a class has at
#:       most one, an interface may have several)
#:   (4) the ``implements`` clause body (classes only; comma-separated)
#: A leading type-parameter list on the declared name (``class X<T>``)
#: is consumed before the ``extends`` lookahead so generic bounds do not
#: leak in as bases. ``abstract``/``export``/``default`` modifiers sit
#: before the keyword and are not part of the match (the keyword anchor
#: is ``\b(class|interface)\b``).
_DECL_RE = re.compile(
    r"\b(?P<kind>class|interface)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)"
    r"(?:\s*<[^<>]*(?:<[^<>]*>[^<>]*)*>)?"                 # decl generics
    r"(?:\s+extends\s+(?P<extends>[^{]+?))?"               # extends clause
    r"(?:\s+implements\s+(?P<implements>[^{]+?))?"         # implements clause
    r"\s*\{",
    re.MULTILINE | re.DOTALL,
)

#: One base reference inside an ``extends`` / ``implements`` clause:
#: a dotted identifier path with an optional trailing type-argument
#: list (``ns.Iface<Foo>``). The path is captured; the generic args are
#: consumed but discarded.
_BASE_RE = re.compile(
    r"(?P<path>[A-Za-z_$][\w$.]*)"
    r"(?:\s*<[^<>]*(?:<[^<>]*>[^<>]*)*>)?",
)

#: Line- and block-comment span; replaced with whitespace so a
#: commented-out declaration never stages a phantom edge while character
#: offsets used by sibling parsers stay stable. Mirrors the Java / Rust
#: comment strippers.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(source_text: str) -> str:
    """Return *source_text* with line and block comments blanked out."""
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return _COMMENT_RE.sub(_blank, source_text)


def _short_name(path: str) -> str:
    """Return the final ``.``-separated segment of a TS base path.

    ``ns.Base`` -> ``Base``; ``Shape`` -> ``Shape``. The project symbol
    index is keyed by the declared short name, so the use-side qualifier
    is dropped for lookup.
    """
    return path.rsplit(".", 1)[-1]


def _split_bases(clause: str) -> list[tuple[str, str]]:
    """Split a clause body into ``(base_short, base_full)`` pairs.

    Commas at the top level separate bases; type-argument lists are
    consumed by :data:`_BASE_RE` so a comma inside ``<...>`` does not
    over-split. Empty fragments are skipped.
    """
    out: list[tuple[str, str]] = []
    for raw in _split_top_level(clause):
        match = _BASE_RE.match(raw.strip())
        if not match:
            continue
        full = match.group("path")
        if full:
            out.append((_short_name(full), full))
    return out


def _split_top_level(clause: str) -> list[str]:
    """Split *clause* on commas that are not nested inside ``<...>``."""
    parts: list[str] = []
    depth = 0
    start = 0
    for idx, ch in enumerate(clause):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(clause[start:idx])
            start = idx + 1
    parts.append(clause[start:])
    return parts


def extract_inheritance(source_text: str) -> list[tuple[str, str, str, str]]:
    """Return ``(decl_short, base_short, base_full, edge_type)`` tuples.

    One tuple per inheritance base, in source order. *edge_type* is
    ``inherits`` for an ``extends`` base (class or interface) and
    ``implements`` for an ``implements`` base (classes only). *base_full*
    preserves the qualified path as written (recorded on the edge for
    provenance); *decl_short* / *base_short* are the bare final segments
    used for symbol resolution.
    """
    out: list[tuple[str, str, str, str]] = []
    for match in _DECL_RE.finditer(_strip_comments(source_text)):
        decl_short = _short_name(match.group("name"))
        extends_clause = match.group("extends") or ""
        implements_clause = match.group("implements") or ""
        for base_short, base_full in _split_bases(extends_clause):
            out.append((decl_short, base_short, base_full, "inherits"))
        for base_short, base_full in _split_bases(implements_clause):
            out.append((decl_short, base_short, base_full, "implements"))
    return out


def build_caches(language: str) -> dict | None:
    """Seed the run-wide ``inherit_records`` accumulator for TypeScript.

    Returns ``None`` for any non-TS language so the caller can fold the
    result into the shared ``enricher_caches`` ``or``-chain uniformly
    with the other per-language cache builders. ``tsx`` / ``javascript``
    / ``jsx`` are intentionally excluded: ES classes do extend/implement,
    but this MVP scopes the inheritance edge to the ``typescript``
    variant the bundled fixture exercises.
    """
    if language != "typescript":
        return None
    return {"inherit_records": []}


def stage_inheritance(
    inherit_records: list | None,
    *,
    rel_path: str,
    source_text: str,
) -> None:
    """Append this file's inheritance records to the run-wide accumulator.

    No-op when *inherit_records* is ``None`` (the accumulator is only
    seeded for TypeScript). Records carry the declaring symbol's module
    path so :func:`finalise` can mint the correct
    ``symbol:typescript:<module>:<decl>`` origin id without re-deriving
    it, and the raw ``rel_path`` so :func:`finalise` can stamp
    ``props.provenance.file`` on the emitted edge (ADR 0074): an
    ``extends``/``implements`` clause is declared at exactly one point in
    exactly one file, so the record that clause lands in unambiguously
    names the edge's producing file (bd rifzk).
    """
    if inherit_records is None:
        return
    module_path = ts_module_from_path(rel_path)
    for decl_short, base_short, base_full, edge_type in extract_inheritance(
        source_text
    ):
        inherit_records.append(
            {
                "module_path": module_path,
                "rel_path": rel_path,
                "decl_short": decl_short,
                "base_short": base_short,
                "base_full": base_full,
                "edge_type": edge_type,
            }
        )


def build_project_symbol_index(nodes: dict[str, dict]) -> dict[str, str]:
    """Return ``{symbol_short_label: symbol_id}`` for project TS symbols.

    Indexes every ``type='symbol'`` ``language='typescript'`` node by its
    label (the declared short name). The first declaration of a given
    short name wins so resolution is deterministic; a later same-named
    symbol in another module falls through to the unresolved sentinel,
    matching the conservative single-project resolution this MVP targets.
    """
    index: dict[str, str] = {}
    for nid, node in nodes.items():
        if not isinstance(node, dict) or node.get("type") != "symbol":
            continue
        props = node.get("props") or {}
        if props.get("language") != "typescript":
            continue
        label = node.get("label", "")
        if label:
            index.setdefault(label, nid)
    return index


def finalise(
    nodes: dict[str, dict],
    edges: list[dict],
    enricher_caches: dict | None,
    source_strategy: str,
) -> None:
    """Emit one edge per staged inheritance record.

    Resolution mirrors :func:`weld.strategies._rust_inherits.finalise`:

    * Project-local base -- resolves to
      ``symbol:typescript:<module>:<base>`` via the project symbol index.
      ``confidence: definite``, ``resolved=True``.
    * Otherwise -- ``symbol:unresolved:<base_short>`` sentinel, minted
      lazily so the edge target is referentially closed.
      ``confidence: speculative``, ``resolved=False``.

    Edges originate at the declaring symbol node
    (``symbol:typescript:<module>:<decl>``); a record whose declaring
    symbol was never minted (e.g. a non-``export`` class the ``exports``
    query skipped) is dropped rather than anchored to a dangling source.
    """
    if not enricher_caches:
        return
    records = enricher_caches.get("inherit_records") or []
    if not records:
        return
    index = build_project_symbol_index(nodes)
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        from_id = (
            f"symbol:typescript:{record['module_path']}:{record['decl_short']}"
        )
        if from_id not in nodes:
            continue
        base_short = record["base_short"]
        edge_type = record["edge_type"]
        if base_short in index:
            target_id = index[base_short]
            resolved = True
        else:
            target_id = f"symbol:unresolved:{base_short}"
            resolved = False
        key = (from_id, target_id, edge_type)
        if key in seen:
            continue
        seen.add(key)
        if not resolved:
            nodes.setdefault(
                target_id,
                {
                    "type": "symbol",
                    "label": base_short,
                    "props": {
                        "language": "typescript",
                        "source_strategy": source_strategy,
                        "authority": "derived",
                        "confidence": "speculative",
                        "kind": "unresolved",
                        "origin": "unresolved",
                        "qualname": base_short,
                    },
                },
            )
        props: dict = {
            "source_strategy": source_strategy,
            "confidence": "definite" if resolved else "speculative",
            "resolved": resolved,
            "base_name": record["base_full"],
            "impl_type": record["decl_short"],
        }
        rel_path = record.get("rel_path", "")
        if rel_path:
            # ADR 0074: attribute to the file whose extends/implements
            # clause produced this edge (bd rifzk).
            props["provenance"] = {"file": rel_path}
        edges.append(
            {"from": from_id, "to": target_id, "type": edge_type, "props": props}
        )


__all__ = [
    "build_caches",
    "build_project_symbol_index",
    "extract_inheritance",
    "finalise",
    "stage_inheritance",
]
