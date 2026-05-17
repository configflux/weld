"""Tree-sitter definition promotion helpers."""

from __future__ import annotations

from collections.abc import Iterable

from weld.strategies._ts_call_graph import ts_module_from_path

T1_DEFINITION_LANGUAGES = {
    "python",
    "typescript",
    "go",
    "rust",
    "java",
    "csharp",
}

_CSHARP_DEFINITION_KEYS = (
    "classes",
    "interfaces",
    "structs",
    "records",
    "methods",
    "properties",
)

# Canonical singular ``kind`` per ADR 0064 criterion 1.
#
# Tree-sitter queries emit buckets named by their plural category
# (``classes``, ``methods``, ``properties``); the promoted symbol's
# ``kind`` property must use the documented singular vocabulary so
# downstream filters like ``kind == 'class'`` work. The historical bug
# (weld 0.19.1 ShareX dogfood, 2026-05-15) was a naive
# ``key[:-1] if key.endswith('s') else key`` that produced
# ``'classe'`` and ``'propertie'``. Map explicitly here; unknown future
# buckets fall back to the input key verbatim so a missing entry never
# silently produces a mangled value.
_CSHARP_CANONICAL_KIND: dict[str, str] = {
    "classes": "class",
    "methods": "method",
    "properties": "property",
    "interfaces": "interface",
    "structs": "struct",
    "enums": "enum",
    "records": "record",
}

# Per-strategy semantic ``kind`` values minted on real C# source-derived
# symbol nodes by framework-specific strategies. Unlike
# ``_CSHARP_CANONICAL_KIND`` (a structural plural -> singular tree-sitter
# bucket map for the base grammar) and unlike the synthetic placeholders
# in ``tools.tier_check_kinds._SYNTHETIC_KINDS`` (placeholder kinds used
# by weld's own modelling of unresolved/merged shapes), these are *real*
# source symbols that a framework-aware strategy enriches with a more
# specific kind:
#
# * ``controller`` -- ASP.NET Core MVC controller class (minted by
#   ``weld.strategies.csharp_aspnet_routes`` via ``controller_node``).
#   The underlying type IS a class; the strategy upgrades the kind so
#   downstream filters like ``kind == "controller"`` work.
# * ``dbcontext`` -- EF Core ``DbContext``-derived class (minted by
#   ``weld.strategies.csharp_efcore``). Same rationale: the underlying
#   type IS a class.
#
# Extend this set whenever a new framework strategy mints a semantic
# kind on a ``type='symbol'`` node so criterion 1 keeps treating those
# values as accepted vocabulary rather than mangled breaches. ADR 0064
# criterion 1 accepts ``_CSHARP_CANONICAL_KIND.values() |
# _CSHARP_SEMANTIC_KIND`` as the C# vocabulary.
_CSHARP_SEMANTIC_KIND: frozenset[str] = frozenset({
    "controller",
    "dbcontext",
})


def _canonical_csharp_kind(key: str) -> str:
    """Map a C# tree-sitter bucket name to its canonical singular kind.

    Returns the input ``key`` unchanged for unknown plurals so future
    grammar additions surface as a non-mangled raw bucket name in the
    graph (and trip review) rather than silently emitting a
    suffix-stripped value like ``'interfac'``.
    """
    return _CSHARP_CANONICAL_KIND.get(key, key)


def promote_definition_symbols(
    *,
    language: str,
    rel_path: str,
    symbols: dict[str, list[str]],
    file_node_id: str,
    source_strategy: str,
) -> tuple[dict[str, dict], list[dict]]:
    """Emit symbol nodes and file containment edges for parsed definitions."""
    if language not in T1_DEFINITION_LANGUAGES:
        return {}, []
    module_path = ts_module_from_path(rel_path)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for name, kind in _definition_records(language, symbols):
        symbol_id = f"symbol:{language}:{module_path}:{name}"
        nodes.setdefault(
            symbol_id,
            {
                "type": "symbol",
                "label": name,
                "props": _symbol_props(
                    language=language,
                    rel_path=rel_path,
                    module_path=module_path,
                    name=name,
                    kind=kind,
                    source_strategy=source_strategy,
                ),
            },
        )
        edges.append(
            {
                "from": file_node_id,
                "to": symbol_id,
                "type": "contains",
                "props": {
                    "source_strategy": source_strategy,
                    "confidence": "definite",
                },
            }
        )
    return nodes, edges


def _definition_records(
    language: str,
    symbols: dict[str, list[str]],
) -> list[tuple[str, str | None]]:
    if language == "csharp":
        records: list[tuple[str, str | None]] = []
        for key in _CSHARP_DEFINITION_KEYS:
            kind = _canonical_csharp_kind(key)
            records.extend((name, kind) for name in symbols.get(key, []))
        return _dedupe(records)
    return _dedupe((name, None) for name in symbols.get("exports", []))


def _symbol_props(
    *,
    language: str,
    rel_path: str,
    module_path: str,
    name: str,
    kind: str | None,
    source_strategy: str,
) -> dict:
    props: dict = {
        "file": rel_path,
        "module": module_path,
        "qualname": name,
        "language": language,
        "source_strategy": source_strategy,
        "authority": "derived",
        "confidence": "definite",
        "roles": ["implementation"],
        "origin": "project",
    }
    if kind:
        props["kind"] = kind
    return props


def _dedupe(records: Iterable[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    seen: set[tuple[str, str | None]] = set()
    result: list[tuple[str, str | None]] = []
    for name, kind in records:
        if not isinstance(name, str) or not name:
            continue
        record = (name, kind)
        if record in seen:
            continue
        seen.add(record)
        result.append(record)
    return result
