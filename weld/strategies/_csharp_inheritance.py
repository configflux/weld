"""C# inheritance / implementation edge emission (ADR 0056 base-list).

Three responsibilities:

1. :func:`extract_base_pairs` -- regex scan returning
   ``(namespace, derived, base)`` triples for every base entry in
   every ``class``/``interface``/``struct``/``record`` ``base_list``.

2. :func:`split_inherits_implements` -- C# naming-convention heuristic
   (final identifier matches ``^I[A-Z]`` -> ``implements``; else
   ``inherits``). Per ADR 0050 every edge ships ``confidence: inferred``.

3. :func:`emit_base_edges` -- resolves each base to a project ``file:``
   id when known, else delegates to
   :mod:`._csharp_inheritance_resolve` to mint a canonical external
   placeholder (one per resolved FQN).

Edge ``from`` resolution in :func:`_resolve_from_id`: canonical
partial-class symbol (ADR 0056 Wave 3 merger), per-file class symbol
(ADR 0064 criterion 2), or *file_node_id* as a legacy fallback.

Generic-parameter tails (``IList<int>``) and qualified prefixes
(``System.IDisposable``) are stripped to their final short name for
both the heuristic and the resolution key.
"""

from __future__ import annotations

import re

from weld.strategies._csharp_inheritance_resolve import (
    resolve_external_base_target,
)
from weld.strategies._csharp_partial_classes import partial_class_symbol_id
from weld.strategies._csharp_syntax import namespace_at, namespace_spans
from weld.strategies._ts_call_graph import ts_module_from_path

#: Match the *declaring* construct (``class``/``interface``/``struct``/
#: ``record``) that opens a ``base_list``. Captures: (1) the declaring
#: identifier, (2) the comma-separated base list before the body brace
#: or semicolon. Skips ``where`` constraint clauses by terminating the
#: capture at ``{`` / ``;`` / ``where``.
_DECL_WITH_BASES_RE = re.compile(
    r"(?:(?:public|internal|protected|private|static|partial|sealed|abstract|readonly|ref)\s+)*"
    r"(?:class|interface|struct|record)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*<[^>]*>)?"
    r"\s*:\s*"
    r"([^{;]+?)"
    r"(?=\s*(?:\{|;|where\b))",
)

#: Match a single base entry. Bases can be dotted (``System.IDisposable``),
#: generic (``IList<int>``), or simple identifiers. Captures: the bare
#: dotted name without any generic-argument tail.
_BASE_ENTRY_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_.]*)"
    r"(?:\s*<[^>]*>)?",
)

#: Match a line-comment tail (``// ...`` to end-of-line) and a
#: block-comment span (``/* ... */``). Same approach as
#: :mod:`weld.strategies._csharp_partial_classes`: replace the comment
#: contents with whitespace so offsets used by
#: :func:`weld.strategies._csharp_syntax.namespace_at` stay stable.
_COMMENT_RE = re.compile(
    r"//[^\n]*|/\*.*?\*/",
    re.DOTALL,
)

#: C# interface naming convention: a name starting with capital ``I``
#: followed by another uppercase letter (``IFoo``, ``IDisposable``).
_INTERFACE_RE = re.compile(r"^I[A-Z]")


def _strip_comments(source_text: str) -> str:
    """Return *source_text* with line and block comments blanked out."""
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return _COMMENT_RE.sub(_blank, source_text)


def _short_name(base_entry: str) -> str:
    """Return the final identifier of a qualified base entry.

    ``System.IDisposable`` -> ``IDisposable``. ``IList`` -> ``IList``.
    The heuristic split runs on the short name; resolution against
    project files also uses the short name because the file index is
    indexed by class symbol.
    """
    return base_entry.rsplit(".", 1)[-1]


def record_base_pairs(
    inheritance_records: list,
    *,
    file_node_id: str,
    source_text: str,
    rel_path: str = "",
    imports: list[str] | None = None,
) -> None:
    """Append every base pair from *source_text* to *inheritance_records*.

    Each record is a
    ``(file_node_id, rel_path, namespace, derived, base, imports)``
    tuple consumed by :func:`finalise` after the file loop completes.
    *rel_path* feeds :func:`_resolve_from_id` (per-file class symbol);
    *imports* feeds the external-base resolver so it can normalise
    placeholder ids to one canonical node per resolved FQN.
    ``None`` *imports* is treated as an empty list.
    """
    file_imports = list(imports or [])
    for namespace, derived, base in extract_base_pairs(source_text):
        inheritance_records.append(
            (file_node_id, rel_path, namespace, derived, base, file_imports)
        )


def extract_base_pairs(
    source_text: str,
) -> list[tuple[str, str, str]]:
    """Return ``(namespace, derived_class, base_name)`` triples.

    One triple per base entry in every declaring construct's
    ``base_list``. Comments are stripped before scanning so a literal
    ``class Foo : Base`` inside a documentation block does not
    synthesise a phantom edge. The namespace assignment uses the same
    most-recent-file-scope heuristic as
    :mod:`weld.strategies._csharp_partial_classes`.

    The returned base name is the bare dotted identifier without
    any generic-parameter tail (``IList<int>`` -> ``IList``).
    """
    scan = _strip_comments(source_text)
    spans = namespace_spans(scan)
    triples: list[tuple[str, str, str]] = []
    for match in _DECL_WITH_BASES_RE.finditer(scan):
        derived = match.group(1)
        bases_chunk = match.group(2)
        ns = namespace_at(match.start(), spans)
        for base_match in _BASE_ENTRY_RE.finditer(bases_chunk):
            base = base_match.group(1).strip()
            if not base:
                continue
            triples.append((ns, derived, base))
    return triples


def split_inherits_implements(base_name: str) -> str:
    """Return ``"implements"`` for interface-named bases, else ``"inherits"``.

    The decision uses only the final identifier of a dotted name
    (``System.IDisposable`` -> matches ``^I[A-Z]``). The naming-only
    heuristic ships with ``inferred`` confidence per ADR 0050.
    """
    short = _short_name(base_name)
    return "implements" if _INTERFACE_RE.match(short) else "inherits"


def emit_base_edges(
    nodes: dict[str, dict],
    edges: list[dict],
    *,
    file_node_id: str,
    namespace: str,
    derived_class: str,
    base_name: str,
    source_strategy: str,
    project_file_index: dict[str, str] | None = None,
    from_id: str | None = None,
    imports: list[str] | None = None,
    package_references: frozenset[str] | None = None,
    project_namespace_roots: frozenset[str] | None = None,
) -> None:
    """Emit one ``inherits`` or ``implements`` edge for a base entry.

    Resolves the base to a project ``file:`` id when *project_file_index*
    contains a same-named class symbol; otherwise delegates to
    :func:`._csharp_inheritance_resolve.resolve_external_base_target`,
    which uses *imports* (file using-directives), *package_references*,
    and *project_namespace_roots* to mint one canonical placeholder per
    resolved FQN. Edge ships ADR 0050 ``confidence: inferred``.

    *from_id* defaults to *file_node_id* for legacy direct callers; the
    production :func:`finalise` pass picks the class-level symbol id via
    :func:`_resolve_from_id`. Omitting *imports* /
    *package_references* / *project_namespace_roots* is supported --
    the resolver treats it as "no external usings visible" and keeps
    the legacy consuming-namespace placeholder shape.
    """
    edge_type = split_inherits_implements(base_name)
    target_id = _resolve_base_target(
        nodes,
        namespace=namespace,
        base_name=base_name,
        project_file_index=project_file_index,
        imports=imports,
        package_references=package_references,
        project_namespace_roots=project_namespace_roots,
    )
    edges.append({
        "from": from_id if from_id is not None else file_node_id,
        "to": target_id,
        "type": edge_type,
        "props": {
            "source_strategy": source_strategy,
            "confidence": "inferred",
            "base_name": base_name,
            "derived_class": derived_class,
        },
    })


def _resolve_base_target(
    nodes: dict[str, dict],
    *,
    namespace: str,
    base_name: str,
    project_file_index: dict[str, str] | None,
    imports: list[str] | None,
    package_references: frozenset[str] | None,
    project_namespace_roots: frozenset[str] | None,
) -> str:
    """Return the edge target id for *base_name*.

    (1) Project file index by short name when the same-named class
    lives in the project tree. (2) Otherwise delegate to
    :mod:`._csharp_inheritance_resolve`, which mints the canonical
    external placeholder node (one per resolved FQN).
    """
    short = _short_name(base_name)
    if project_file_index and short in project_file_index:
        return project_file_index[short]
    return resolve_external_base_target(
        nodes,
        namespace=namespace,
        base_name=base_name,
        imports=imports,
        package_references=package_references,
        project_namespace_roots=project_namespace_roots,
    )


def build_project_file_index(nodes: dict[str, dict]) -> dict[str, str]:
    """Return ``{class_short_name: file_node_id}`` for resolved bases.

    Walks the discovered ``file`` nodes' ``types``/``exports`` lists
    and indexes each declared class/interface/struct/record name to
    the file id that declared it. When multiple files declare the
    same short name the first one wins; this collides only on
    duplicate symbol names across the project, which is independently
    a code smell.
    """
    index: dict[str, str] = {}
    for nid, node in nodes.items():
        if node.get("type") != "file":
            continue
        props = node.get("props") or {}
        if props.get("language") and props["language"] != "csharp":
            continue
        for name in props.get("types", []) or props.get("exports", []):
            index.setdefault(name, nid)
    return index


def finalise(
    nodes: dict[str, dict],
    edges: list[dict],
    enricher_caches: dict | None,
    source_strategy: str,
) -> None:
    """Walk ``inheritance_records`` and emit one edge per recorded pair.

    Builds the project file index once, then resolves each record to
    an ``inherits``/``implements`` edge. Edge ``from`` follows the
    canonical-partial / per-file-symbol / file-node ladder in
    :func:`_resolve_from_id`. A ``None`` *enricher_caches* (non-C#
    language path) is a no-op.
    """
    if not enricher_caches:
        return
    records = enricher_caches.get("inheritance_records") or []
    if not records:
        return
    project_file_index = build_project_file_index(nodes)
    partial_class_state = enricher_caches.get("partial_class_state") or {}
    package_references = enricher_caches.get("package_references")
    project_namespace_roots = enricher_caches.get("project_namespace_roots")
    for record in records:
        # Tolerate the historical 4- and 5-tuple shapes (no imports, no
        # rel_path) so legacy tests calling :func:`record_base_pairs`
        # with older signatures keep producing valid records.
        file_node_id, rel_path, namespace, derived, base, imports = (
            _unpack_record(record)
        )
        from_id = _resolve_from_id(
            nodes,
            partial_class_state=partial_class_state,
            namespace=namespace,
            derived=derived,
            rel_path=rel_path,
            file_node_id=file_node_id,
        )
        emit_base_edges(
            nodes,
            edges,
            file_node_id=file_node_id,
            namespace=namespace,
            derived_class=derived,
            base_name=base,
            source_strategy=source_strategy,
            project_file_index=project_file_index,
            from_id=from_id,
            imports=imports,
            package_references=package_references,
            project_namespace_roots=project_namespace_roots,
        )


def _unpack_record(record: tuple) -> tuple:
    """Normalise legacy 4/5-tuple records to the 6-tuple shape
    ``(file_node_id, rel_path, namespace, derived, base, imports)``."""
    if len(record) == 6:
        return record
    if len(record) == 5:
        f, r, n, d, b = record
        return (f, r, n, d, b, [])
    f, n, d, b = record
    return (f, "", n, d, b, [])


def _resolve_from_id(
    nodes: dict[str, dict],
    *,
    partial_class_state: dict,
    namespace: str,
    derived: str,
    rel_path: str,
    file_node_id: str,
) -> str:
    """Return the edge ``from`` id for an inheritance/implementation edge.

    Resolution order:

    1. Canonical partial-class symbol
       (``symbol:csharp:<namespace>.<derived>``) when
       ``(namespace, derived)`` is a recorded partial-class key AND
       the merged node exists in *nodes* (the merger runs before this
       pass per :func:`weld.strategies._csharp_tree_sitter.finalise`).
       Consumers query inheritance from the canonical id, so partial
       classes must originate at that node not the per-file alias.
    2. Per-file class symbol ``symbol:csharp:<module_path>:<derived>``
       when the discovery loop promoted the class definition
       (ADR 0064 criterion 2 -- keeps multi-class files unambiguous).
    3. *file_node_id* fallback for legacy records or unpromoted classes
       so the graph stays valid.
    """
    if (namespace, derived) in partial_class_state:
        canonical = partial_class_symbol_id(namespace, derived)
        if canonical in nodes:
            return canonical
    derived_symbol_id = _derived_class_symbol_id(rel_path, derived)
    if derived_symbol_id and derived_symbol_id in nodes:
        return derived_symbol_id
    return file_node_id


def _derived_class_symbol_id(rel_path: str, derived_class: str) -> str:
    """Return ``symbol:csharp:<module_path>:<derived_class>`` or ``""``.

    Mirrors :func:`weld.strategies._ts_definitions.promote_definition_symbols`
    which mints the same id shape for every C# class. Empty *rel_path*
    returns the empty string so callers know to fall back.
    """
    if not rel_path or not derived_class:
        return ""
    module_path = ts_module_from_path(rel_path)
    if not module_path:
        return ""
    return f"symbol:csharp:{module_path}:{derived_class}"


__all__ = [
    "build_project_file_index",
    "emit_base_edges",
    "extract_base_pairs",
    "finalise",
    "record_base_pairs",
    "split_inherits_implements",
]
