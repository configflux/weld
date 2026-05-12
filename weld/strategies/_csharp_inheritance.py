"""C# inheritance / implementation edge emission (ADR 0056 base-list).

Extracted from :mod:`weld.strategies._csharp_tree_sitter` to keep that
module under the 400-line cap. Three responsibilities:

1. :func:`extract_base_pairs` -- regex-based scan of a single source
   file. Returns the list of ``(namespace, derived_class, base_name)``
   triples declared in the file, one tuple per base entry in every
   ``class : ...``/``interface : ...``/``struct : ...``/``record : ...``
   ``base_list``.

2. :func:`split_inherits_implements` -- C# naming-convention heuristic.
   Bases whose final identifier matches ``^I[A-Z]`` are classified as
   ``implements`` (interface implementation); the rest default to
   ``inherits`` (class/struct/record inheritance). Per ADR 0050 every
   emitted edge ships with ``confidence: "inferred"`` because the
   heuristic is naming-only and cannot distinguish e.g. an
   I-prefixed concrete class from a real interface.

3. :func:`emit_base_edges` -- walks the extracted pairs and the
   discovered nodes dict, resolving each base to a project ``file:``
   id when a same-named class export exists, or minting a placeholder
   ``symbol:csharp:<namespace>.<base>`` node otherwise. Emits one
   ``inherits`` or ``implements`` edge per pair.

The helper intentionally does not parse expressions or method bodies.
Generic-parameter tails (``IList<int>``) and qualified prefixes
(``System.IDisposable``) are stripped to their final short name for
both the heuristic and the resolution key, mirroring the behaviour of
the tree-sitter ``bases`` capture in ``csharp.yaml``.
"""

from __future__ import annotations

import re

from weld.strategies._csharp_syntax import namespace_at, namespace_spans

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
        return "".join(
            "\n" if ch == "\n" else " "
            for ch in match.group(0)
        )
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
) -> None:
    """Append every base pair from *source_text* to *inheritance_records*.

    Per-file shim around :func:`extract_base_pairs`. Each appended
    record is a ``(file_node_id, namespace, derived_class, base_name)``
    tuple consumed by :func:`weld.strategies._csharp_tree_sitter.finalise`
    once every file has been visited so the cross-file project file
    index is complete.
    """
    for namespace, derived, base in extract_base_pairs(source_text):
        inheritance_records.append(
            (file_node_id, namespace, derived, base)
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
    (``System.IDisposable`` -> ``IDisposable`` -> matches ``^I[A-Z]``).
    The heuristic is naming-convention-based and explicitly marked
    ``inferred`` on the emitted edge per ADR 0050.
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
) -> None:
    """Emit one ``inherits`` or ``implements`` edge for a base entry.

    Resolves the base to a project ``file:`` id when *project_file_index*
    contains a same-named class symbol (case-sensitive). Otherwise
    mints a placeholder ``symbol:csharp:<namespace>.<base>`` node so
    the edge has a stable target the graph linter can attribute back
    to the C# enricher. Both directions of the target carry
    ``confidence: inferred`` per ADR 0050.

    *namespace* / *derived_class* are unused by the edge body itself
    but kept in the signature so the caller can plug additional
    debug metadata in without changing the call shape.
    """
    edge_type = split_inherits_implements(base_name)
    target_id = _resolve_base_target(
        nodes,
        namespace=namespace,
        base_name=base_name,
        project_file_index=project_file_index,
    )
    edges.append({
        "from": file_node_id,
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
) -> str:
    """Return the edge target id for *base_name*.

    Resolution order:

    1. Project file index by short name -- returns the file node id
       when a class export with the same short name lives in the
       project tree.
    2. External placeholder ``symbol:csharp:<qualified>``. When
       *base_name* is already dotted (``System.IDisposable``) we keep
       the declared prefix; bare names inherit the file's
       *namespace* so the placeholder collides with later symbol
       enrichment that knows its own namespace context.
    """
    short = _short_name(base_name)
    if project_file_index and short in project_file_index:
        return project_file_index[short]
    if "." in base_name:
        target_namespace = base_name.rsplit(".", 1)[0]
    else:
        target_namespace = namespace
    qualified = f"{target_namespace}.{short}" if target_namespace else short
    target_id = f"symbol:csharp:{qualified}"
    nodes.setdefault(
        target_id,
        {
            "type": "symbol",
            "label": short,
            "props": {
                "name": short,
                "namespace": target_namespace,
                "kind": "base_reference",
                "language": "csharp",
                "authority": "derived",
                "confidence": "inferred",
                "origin": "external",
                "roles": ["implementation"],
            },
        },
    )
    return target_id


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

    Builds the project file index once from the discovered file nodes,
    then resolves each ``(file_node_id, namespace, derived, base)``
    record to an ``inherits`` or ``implements`` edge. A ``None``
    *enricher_caches* (non-C# language path) is a no-op so the
    dispatcher in :mod:`weld.strategies._csharp_tree_sitter` can call
    this unconditionally.
    """
    if not enricher_caches:
        return
    records = enricher_caches.get("inheritance_records") or []
    if not records:
        return
    project_file_index = build_project_file_index(nodes)
    for file_node_id, namespace, derived, base in records:
        emit_base_edges(
            nodes,
            edges,
            file_node_id=file_node_id,
            namespace=namespace,
            derived_class=derived,
            base_name=base,
            source_strategy=source_strategy,
            project_file_index=project_file_index,
        )


__all__ = [
    "build_project_file_index",
    "emit_base_edges",
    "extract_base_pairs",
    "finalise",
    "record_base_pairs",
    "split_inherits_implements",
]
