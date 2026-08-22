"""Strategy: Entity Framework Core entity extraction (ADR 0056 Wave 2).

Detects two related shapes in C# source files:

1. **DbContext subclasses** -- any ``class Foo : ... DbContext ...``
   declaration where the base list contains ``DbContext``. Each becomes
   a ``symbol:csharp:<namespace>.<class>`` node carrying ``kind:
   dbcontext``.

2. **DbSet<T> properties** -- ``public DbSet<Order> Orders { get; set;
   }`` declarations inside a detected ``DbContext`` class. Each
   referenced type ``T`` becomes an ``entity:<T>`` node and a
   ``contains`` edge from the DbContext symbol to the entity node.

Entity nodes carry a ``table`` prop. When the entity class is defined
in the same file (or any scanned file) with a ``[Table("name")]``
attribute, that name is used (confidence ``definite``). Otherwise the
strategy falls back to a *pluralisation heuristic*: ``Order`` ->
``orders``, ``Customer`` -> ``customers``, ``History`` -> ``histories``,
``Bus`` -> ``buses``. The fallback case is annotated with
``table_confidence: inferred`` on the entity node so consumers can
filter the two populations.

Every emitted edge ships with ``confidence="definite"`` per ADR 0050:
the ``contains`` edge from DbContext to entity is XML-parsed
equivalent (a typed ``DbSet<>`` is unambiguous). Tables are encoded as
entity props rather than separate nodes because :mod:`weld.contract`
does not include a ``table`` node type; see ADR 0056 § "Out of scope"
for the fluent-API mapping that would justify a richer table model.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

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

#: Matches a ``DbSet<T> Name { ... }`` property declaration. The
#: capture extracts the type parameter ``T``. Optional modifiers
#: (``public``, ``virtual``, etc.) are tolerated. The trailing ``{``
#: anchors the regex to property declarations.
_DBSET_RE = re.compile(
    r"(?:(?:public|internal|protected|private|virtual|abstract|override|static)[\t ]+)*"
    r"DbSet[\t ]*<\s*([A-Za-z_][A-Za-z0-9_.]*)\s*>\s+[A-Za-z_][A-Za-z0-9_]*\s*\{",
)

#: Matches ``[Table("name")]`` and ``[Table("name", Schema=...)]``.
_TABLE_ATTR_RE = re.compile(r"\[\s*Table\s*\(\s*\"([^\"]+)\"")

#: Matches a ``DbContext`` base type in a class base list. Generic
#: forms like ``DbContext<TUser>`` also match because the base list is
#: parsed by tokenising on commas; ``DbContext`` appears as a prefix.
_DBCONTEXT_BASE_RE = re.compile(r"\bDbContext\b")


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract DbContext + entity nodes from C# source files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "**/*.cs")
    excludes = source.get("exclude", [])

    matched = resolve_glob(root, pattern, excludes)

    # First pass: locate [Table("...")] attributes for *every* class in
    # the repo so DbSet references can resolve to the correct table
    # name even when the entity class lives in a different file.
    # Provenance is every matched file, recorded before any read (bd od2a).
    # It is the whole match list because the pre-pass below reads all of
    # them: a ``[Table]`` attribute in a file this strategy never emits
    # from still decides the table name of an entity it does emit. The
    # parent directory this replaced degenerated to ``"./"`` for a
    # repo-root match, and recording only files that emitted a DbContext
    # meant adding the first one to a module never marked the graph stale.
    discovered_from.extend(file_provenance(root, matched))

    table_attrs = _collect_table_attributes(matched)

    for cs_file in matched:
        if not cs_file.is_file():
            continue
        try:
            source_text = cs_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = cs_file.relative_to(root).as_posix()
        for dbcontext_class, namespace, entities in _scan_dbcontexts(source_text):
            dbcontext_id = _symbol_id(namespace, dbcontext_class)
            nodes[dbcontext_id] = _dbcontext_node(
                dbcontext_class, namespace, entities, rel_path,
            )
            for entity_name in sorted(set(entities)):
                entity_id = _entity_id(entity_name)
                table_name, table_confidence = _resolve_table(
                    entity_name, table_attrs,
                )
                existing = nodes.get(entity_id)
                if _should_replace_entity(existing, table_confidence):
                    nodes[entity_id] = _entity_node(
                        entity_name, table_name, table_confidence,
                    )
                edges.append(_contains_edge(dbcontext_id, entity_id))

    seen: set[str] = set()
    deduped: list[str] = []
    for d in discovered_from:
        if d not in seen:
            seen.add(d)
            deduped.append(d)

    return StrategyResult(nodes, edges, deduped)


def _dbcontext_node(
    class_name: str,
    namespace: str,
    entities: list[str],
    rel_path: str,
) -> dict:
    """Build the DbContext class node payload."""
    label = f"{namespace}.{class_name}" if namespace else class_name
    return {
        "type": "symbol",
        "label": label,
        "props": {
            "file": rel_path,
            "name": class_name,
            "namespace": namespace,
            "kind": "dbcontext",
            "language": "csharp",
            "entities": sorted(set(entities)),
            "source_strategy": "csharp_efcore",
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["implementation"],
        },
    }


def _entity_node(name: str, table: str, table_confidence: str) -> dict:
    """Build the entity node payload.

    ``authority`` is ``canonical`` when the table was sourced from a
    ``[Table]`` attribute (definite mapping) and ``derived`` when it
    was sourced from the pluralisation heuristic.
    """
    return {
        "type": "entity",
        "label": name,
        "props": {
            "name": name,
            "table": table,
            "table_confidence": table_confidence,
            "language": "csharp",
            "source_strategy": "csharp_efcore",
            "authority": (
                "canonical" if table_confidence == "definite" else "derived"
            ),
            "confidence": "definite",
            "roles": ["implementation"],
        },
    }


def _should_replace_entity(
    existing: dict | None, new_confidence: str,
) -> bool:
    """Return True when *new_confidence* should replace *existing*.

    Order: brand-new entity -> replace. Existing with inferred table ->
    replace with a definite one. Existing with definite table ->
    replace only if the new mapping is also definite (keeps the most
    recent agreement and keeps the function deterministic across
    DbContext file ordering).
    """
    if existing is None:
        return True
    if existing["props"].get("table_confidence") == "inferred":
        return True
    if new_confidence == "definite":
        return True
    return False


def _contains_edge(dbcontext_id: str, entity_id: str) -> dict:
    """Build the DbContext -> entity contains edge."""
    return {
        "from": dbcontext_id,
        "to": entity_id,
        "type": "contains",
        "props": {
            "source_strategy": "csharp_efcore",
            "confidence": "definite",
        },
    }


def _symbol_id(namespace: str, class_name: str) -> str:
    """Return the canonical ``symbol:csharp:<namespace>.<class>`` id.

    Mirrors the shape used by other multi-language strategies
    (``symbol:<lang>:<module>:<qualname>``). An empty namespace
    collapses to a bare ``symbol:csharp:<class>`` id.
    """
    qualified = f"{namespace}.{class_name}" if namespace else class_name
    return f"symbol:csharp:{qualified}"


def _entity_id(name: str) -> str:
    """Return the canonical ``entity:<Name>`` id.

    Matches :mod:`weld.strategies.sqlalchemy`'s entity id shape so a
    polyglot repo with both SQLAlchemy and EF Core entities of the
    same logical name can share the entity node.
    """
    return f"entity:{name}"


def _scan_dbcontexts(
    source_text: str,
) -> Iterator[tuple[str, str, list[str]]]:
    """Yield ``(dbcontext_class, namespace, entities)`` triples.

    Walks class declarations in source order. A class is treated as a
    DbContext when its base list contains the ``DbContext`` token.
    Within each DbContext class body, every ``DbSet<T>`` property
    contributes one entity to the returned list.
    """
    namespaces = namespace_spans(source_text)
    for class_match in CLASS_RE.finditer(source_text):
        class_name = class_match.group(1)
        base_list = class_match.group(2) or ""
        if not _DBCONTEXT_BASE_RE.search(base_list):
            continue

        body_start, body_end = class_body_range(
            source_text, class_match.end(),
        )
        if body_start is None or body_end is None:
            continue

        namespace = namespace_at(class_match.start(), namespaces)
        body = source_text[body_start:body_end]
        entities = [
            dbset_match.group(1).split(".")[-1]
            for dbset_match in _DBSET_RE.finditer(body)
        ]
        yield class_name, namespace, entities


def _collect_table_attributes(files: list[Path]) -> dict[str, str]:
    """Return ``{class_name -> table_name}`` for every ``[Table]`` in repo.

    A pre-pass over the matched files so DbSet references can resolve
    to the correct table even when the entity class is defined in a
    sibling file.
    """
    mapping: dict[str, str] = {}
    for path in files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for class_match in CLASS_RE.finditer(text):
            class_name = class_match.group(1)
            attr_window = text[
                attribute_window_start(text, class_match.start())
                :class_match.start()
            ]
            attr_match = _TABLE_ATTR_RE.search(attr_window)
            if attr_match:
                mapping[class_name] = attr_match.group(1)
    return mapping


def _resolve_table(
    entity_name: str, table_attrs: dict[str, str],
) -> tuple[str, str]:
    """Return ``(table_name, confidence)`` for an entity.

    Lookup order: ``[Table("name")]`` attribute (confidence
    ``definite``) -> pluralised class name (confidence ``inferred``).
    """
    explicit = table_attrs.get(entity_name)
    if explicit:
        return explicit, "definite"
    return _pluralise(entity_name), "inferred"


def _pluralise(name: str) -> str:
    """Return the English pluralisation of *name*, lower-cased.

    Rules (in order):

    - ``y`` preceded by a consonant -> ``ies`` (``Category`` ->
      ``categories``, ``History`` -> ``histories``).
    - Trailing ``s``/``x``/``z``/``ch``/``sh`` -> append ``es``
      (``Bus`` -> ``buses``, ``Box`` -> ``boxes``).
    - Otherwise append ``s`` (``Order`` -> ``orders``).

    Lower-casing matches the most common Entity Framework naming
    convention; if a project uses ``PascalCase`` table names a
    ``[Table]`` attribute is the right escape hatch.
    """
    if not name:
        return name
    lower = name.lower()
    if lower.endswith("y") and len(lower) > 1 and lower[-2] not in "aeiou":
        return lower[:-1] + "ies"
    for suffix in ("s", "x", "z", "ch", "sh"):
        if lower.endswith(suffix):
            return lower + "es"
    return lower + "s"


__all__ = ["extract"]
