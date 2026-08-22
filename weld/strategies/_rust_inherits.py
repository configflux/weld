"""Rust trait-implementation edge emission (ADR 0064 criterion 2).

Rust has no class inheritance; its interface analog is the
``impl Trait for Type`` block. This module is the Rust counterpart to
:mod:`weld.strategies._java_inherits` /
:mod:`weld.strategies._csharp_inheritance`: it emits one ``implements``
edge per trait-impl, originating at the implementing type's symbol node
(criterion 2's "symbol-origin, not file-origin" contract).

Three responsibilities, matching the established cross-language shape:

1. :func:`extract_trait_impls` -- regex scan of one source file returning
   ``(type_short, trait_short, trait_full)`` triples, one per
   ``impl <Trait> for <Type>`` block. Inherent ``impl <Type> { ... }``
   blocks have no ``for`` clause and are intentionally not matched.
   Generic parameter lists, trait/type generic arguments, ``where``
   clauses, and ``unsafe``/``default`` qualifiers are tolerated; the
   captured names are the bare leading identifiers (qualified trait
   paths like ``std::fmt::Debug`` collapse to their final segment for
   the project-index lookup).

2. :func:`stage_trait_impls` -- append records into the run-wide
   accumulator seeded by :func:`build_caches` so :func:`finalise` can
   resolve trait short-names against the project-wide symbol index after
   every file has been visited (a trait declared in a sibling module
   resolves without an explicit-import join, mirroring the Java model).

3. :func:`finalise` -- consume the accumulator once the tree-sitter file
   loop completes, resolve each ``(type, trait)`` pair against the
   project Rust symbol index, and emit the ``implements`` edge. A trait
   defined outside the project (``Serialize``, ``Debug``) lands on the
   shared ``symbol:unresolved:<short>`` sentinel so the edge target
   stays referentially closed, exactly as the call-edge and Java
   inheritance paths do.

The regex never reads beyond the ``impl`` header line, so the cost is
proportional to source length and the output is deterministic (records
are appended in source order; :func:`finalise` dedups on
``(from, to)``).
"""

from __future__ import annotations

import re

from weld.strategies._ts_call_graph import ts_module_from_path

#: Match an ``impl <Trait> for <Type>`` header. Captures the leading
#: identifier of the trait path and of the implementing type. The
#: optional ``<...>`` groups consume one level of nested generics; the
#: optional ``where`` clause is swallowed up to the opening brace. An
#: inherent ``impl <Type> {`` has no ``for`` keyword and therefore never
#: matches -- only trait-impls produce an edge.
_IMPL_RE = re.compile(
    r"\bimpl\b"
    r"(?:\s*<[^<>]*(?:<[^<>]*>[^<>]*)*>)?"      # optional impl generics
    r"\s+"
    r"(?P<trait>[A-Za-z_][A-Za-z0-9_:]*)"        # trait path
    r"(?:\s*<[^<>]*(?:<[^<>]*>[^<>]*)*>)?"      # optional trait generic args
    r"\s+for\s+"
    r"(?P<type>[A-Za-z_][A-Za-z0-9_:]*)"         # implementing type
    r"(?:\s*<[^<>]*(?:<[^<>]*>[^<>]*)*>)?"      # optional type generic args
    r"(?:\s+where\b[^{]*)?"                       # optional where clause
    r"\s*\{",
    re.MULTILINE | re.DOTALL,
)

#: Line- and block-comment span; replaced with whitespace so a
#: commented-out ``impl`` line never stages a phantom edge while
#: character offsets used by sibling parsers stay stable. Mirrors the
#: Java / C# comment strippers.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(source_text: str) -> str:
    """Return *source_text* with line and block comments blanked out."""
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return _COMMENT_RE.sub(_blank, source_text)


def _short_name(path: str) -> str:
    """Return the final ``::``-separated segment of a Rust path.

    ``std::fmt::Debug`` -> ``Debug``; ``Shape`` -> ``Shape``. The project
    symbol index is keyed by the declared short name, so the use-side
    qualifier is dropped for lookup.
    """
    return path.rsplit("::", 1)[-1]


def extract_trait_impls(source_text: str) -> list[tuple[str, str, str]]:
    """Return ``(type_short, trait_short, trait_full)`` for each trait-impl.

    *trait_full* preserves the qualified path as written (recorded on the
    edge for provenance); *trait_short* / *type_short* are the bare final
    segments used for symbol resolution.
    """
    out: list[tuple[str, str, str]] = []
    for match in _IMPL_RE.finditer(_strip_comments(source_text)):
        trait_full = match.group("trait")
        type_full = match.group("type")
        out.append((_short_name(type_full), _short_name(trait_full), trait_full))
    return out


def build_caches(language: str) -> dict | None:
    """Seed the run-wide ``impl_records`` accumulator for Rust.

    Returns ``None`` for any non-Rust language so the caller can fold the
    result into the shared ``enricher_caches`` ``or``-chain uniformly
    with the other per-language cache builders.
    """
    if language != "rust":
        return None
    return {"impl_records": []}


def stage_trait_impls(
    impl_records: list,
    *,
    rel_path: str,
    source_text: str,
) -> None:
    """Append this file's trait-impl records to the run-wide accumulator.

    No-op when *impl_records* is ``None`` (the accumulator is only seeded
    for Rust). Records carry the implementing type's module path so
    :func:`finalise` can mint the correct ``symbol:rust:<module>:<type>``
    origin id without re-deriving it, and the raw ``rel_path`` so
    :func:`finalise` can stamp ``props.provenance.file`` on the emitted
    ``implements`` edge (ADR 0074): an ``impl Trait for Type`` block is
    declared at exactly one point in exactly one file, so the record that
    block lands in unambiguously names the edge's producing file (bd rifzk).
    """
    if impl_records is None:
        return
    module_path = ts_module_from_path(rel_path)
    for type_short, trait_short, trait_full in extract_trait_impls(source_text):
        impl_records.append(
            {
                "module_path": module_path,
                "rel_path": rel_path,
                "type_short": type_short,
                "trait_short": trait_short,
                "trait_full": trait_full,
            }
        )


def build_project_symbol_index(nodes: dict[str, dict]) -> dict[str, str]:
    """Return ``{symbol_short_label: symbol_id}`` for project Rust symbols.

    Indexes every ``type='symbol'`` ``language='rust'`` node by its label
    (the declared short name). The first declaration of a given short
    name wins so resolution is deterministic; a later same-named symbol
    in another module falls through to the unresolved sentinel, matching
    the conservative single-crate resolution this MVP targets.
    """
    index: dict[str, str] = {}
    for nid, node in nodes.items():
        if not isinstance(node, dict) or node.get("type") != "symbol":
            continue
        props = node.get("props") or {}
        if props.get("language") != "rust":
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
    """Emit one ``implements`` edge per staged trait-impl record.

    Resolution mirrors :func:`weld.strategies._java_inherits.emit_inheritance_edges`:

    * Project-local trait -- resolves to ``symbol:rust:<module>:<trait>``
      via the project symbol index. ``confidence: definite``,
      ``resolved=True``.
    * Otherwise -- ``symbol:unresolved:<trait_short>`` sentinel, minted
      lazily so the edge target is referentially closed.
      ``confidence: speculative``, ``resolved=False``.

    Edges originate at the implementing type's symbol node
    (``symbol:rust:<module>:<type>``); a record whose type symbol was
    never minted (e.g. a non-``pub`` type the ``exports`` query skipped)
    is dropped rather than anchored to a dangling source.
    """
    if not enricher_caches:
        return
    records = enricher_caches.get("impl_records") or []
    if not records:
        return
    index = build_project_symbol_index(nodes)
    seen: set[tuple[str, str]] = set()
    for record in records:
        from_id = f"symbol:rust:{record['module_path']}:{record['type_short']}"
        if from_id not in nodes:
            continue
        trait_short = record["trait_short"]
        if trait_short in index:
            target_id = index[trait_short]
            resolved = True
        else:
            target_id = f"symbol:unresolved:{trait_short}"
            resolved = False
        key = (from_id, target_id)
        if key in seen:
            continue
        seen.add(key)
        if not resolved:
            nodes.setdefault(
                target_id,
                {
                    "type": "symbol",
                    "label": trait_short,
                    "props": {
                        "language": "rust",
                        "source_strategy": source_strategy,
                        "authority": "derived",
                        "confidence": "speculative",
                        "kind": "unresolved",
                        "origin": "unresolved",
                        "qualname": trait_short,
                    },
                },
            )
        props: dict = {
            "source_strategy": source_strategy,
            "confidence": "definite" if resolved else "speculative",
            "resolved": resolved,
            "trait_name": record["trait_full"],
            "impl_type": record["type_short"],
        }
        rel_path = record.get("rel_path", "")
        if rel_path:
            # ADR 0074: attributes the edge to the file whose ``impl Trait
            # for Type`` block produced it, so a purge can tell "this file
            # is stale" from "this file is clean" instead of only "this
            # edge's endpoint node is gone" (bd rifzk).
            props["provenance"] = {"file": rel_path}
        edges.append(
            {"from": from_id, "to": target_id, "type": "implements", "props": props}
        )


__all__ = [
    "build_caches",
    "build_project_symbol_index",
    "extract_trait_impls",
    "finalise",
    "stage_trait_impls",
]
