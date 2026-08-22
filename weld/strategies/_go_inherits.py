"""Go struct-embedding and interface-satisfaction edges (ADR 0064 criterion 2).

Go has no inheritance keyword; its two structural analogs are:

* **Struct embedding** (``type Circle struct { shapes.Base }``) -- the
  embedded type's fields and methods are promoted onto the embedder,
  Go's closest "is-a" idiom, so an embedded *struct* field emits an
  ``inherits`` edge ``Circle -> Base``.
* **Interface satisfaction** -- a type whose method set covers every
  method an interface declares satisfies it *implicitly* (no
  ``implements`` keyword), so such a ``(type, interface)`` pair emits an
  ``implements`` edge ``Circle -> Shape``.

The Go counterpart to :mod:`weld.strategies._rust_inherits` /
:mod:`weld.strategies._typescript_inherits`, in the same three stages:

1. :func:`extract_file_facts` -- regex scan of one source file into a
   :class:`FileFacts` (struct embeddings, method receivers, interface
   method sets). The regexes never read beyond a declaration body, so
   cost is linear and output deterministic (source order).
2. :func:`build_caches` / :func:`stage_file` -- the Go-only run-wide
   accumulator seam, so :func:`finalise` resolves against the *whole
   project* after every file is visited (an embedded base or satisfied
   interface in a sibling package resolves without an import join).
3. :func:`finalise` -- promote methods across embedding (``Circle``
   gains ``Base``'s ``Describe`` transitively), close each interface's
   required set over embedded interfaces, then emit one ``inherits`` per
   embedding and one ``implements`` per satisfying ``(type, interface)``
   with a non-empty required set.

Edges originate at the declaring type's symbol (``symbol:go:<module>:<Type>``);
a record whose type symbol was never minted is dropped rather than left
dangling. An embedded base outside the project lands on the shared
``symbol:unresolved:<short>`` sentinel (referentially closed, as the
call-edge / Rust / Java paths do); interface ``implements`` resolution is
project-local only -- satisfaction against an external interface whose
method set is unknown is intentionally not inferred.
"""

from __future__ import annotations

from weld.strategies._go_inherits_extract import (
    FileFacts,
    extract_file_facts,
)
from weld.strategies._ts_call_graph import ts_module_from_path


def build_caches(language: str) -> dict | None:
    """Seed the ``go_inherit_records`` accumulator (Go only, else ``None``).

    ``None`` for non-Go languages so the caller folds the result into the
    shared ``enricher_caches`` ``or``-chain like the other cache builders.
    """
    if language != "go":
        return None
    return {"go_inherit_records": []}


def stage_file(
    go_inherit_records: list | None,
    *,
    rel_path: str,
    source_text: str,
) -> None:
    """Append this file's structural facts to the run-wide accumulator.

    No-op when *go_inherit_records* is ``None`` (Go-only seam). Each
    record carries the module path so :func:`finalise` mints the correct
    ``symbol:go:<module>:<Type>`` origin id without re-deriving it, and the
    raw ``rel_path`` so :func:`_emit_inherits` can stamp
    ``props.provenance.file`` on the embedding's ``inherits`` edge (ADR
    0074): a struct's embed clause is declared at exactly one point in
    exactly one file, so the record that clause lands in unambiguously
    names the edge's producing file (bd rifzk).
    """
    if go_inherit_records is None:
        return
    facts = extract_file_facts(source_text)
    if not (facts.embeddings or facts.methods or facts.interfaces):
        return
    go_inherit_records.append(
        {
            "module_path": ts_module_from_path(rel_path),
            "rel_path": rel_path,
            "embeddings": facts.embeddings,
            "methods": facts.methods,
            "interfaces": facts.interfaces,
        }
    )


def build_project_symbol_index(nodes: dict[str, dict]) -> dict[str, str]:
    """Return ``{symbol_short_label: symbol_id}`` for project Go symbols.

    Indexes every ``type='symbol'`` ``language='go'`` node by its label.
    The first declaration of a short name wins (deterministic); a later
    same-named symbol in another package falls through -- the conservative
    single-module resolution this MVP targets.
    """
    index: dict[str, str] = {}
    for nid, node in nodes.items():
        if not isinstance(node, dict) or node.get("type") != "symbol":
            continue
        props = node.get("props") or {}
        if props.get("language") != "go":
            continue
        label = node.get("label", "")
        if label:
            index.setdefault(label, nid)
    return index


def _direct_methods(records: list[dict]) -> dict[str, set[str]]:
    """Return ``{type_short: {method_name, ...}}`` for directly-declared methods."""
    out: dict[str, set[str]] = {}
    for record in records:
        for recv, method in record["methods"]:
            out.setdefault(recv, set()).add(method)
    return out


def _embeddings_by_type(records: list[dict]) -> dict[str, list[str]]:
    """Return ``{type_short: [embedded_base_short, ...]}`` across the project."""
    out: dict[str, list[str]] = {}
    for record in records:
        for struct, base_short, _base_full in record["embeddings"]:
            out.setdefault(struct, []).append(base_short)
    return out


def _closed_method_set(
    type_short: str,
    direct: dict[str, set[str]],
    embeds: dict[str, list[str]],
    _seen: frozenset[str] = frozenset(),
) -> set[str]:
    """Return *type_short*'s method set, including methods promoted via embedding.

    Embedding promotes the embedded type's whole method set onto the
    embedder; this walks that transitively. ``_seen`` guards an embedding
    cycle (illegal in real Go, but a malformed corpus must not hang).
    """
    if type_short in _seen:
        return set()
    methods = set(direct.get(type_short, set()))
    next_seen = _seen | {type_short}
    for base in embeds.get(type_short, ()):  # promote embedded base methods
        methods |= _closed_method_set(base, direct, embeds, next_seen)
    return methods


def _closed_interface_set(
    iface_short: str,
    interfaces: dict[str, tuple[set[str], set[str]]],
    _seen: frozenset[str] = frozenset(),
) -> set[str]:
    """Return *iface_short*'s required methods, folding embedded interfaces."""
    if iface_short in _seen or iface_short not in interfaces:
        return set()
    methods, embeds = interfaces[iface_short]
    required = set(methods)
    next_seen = _seen | {iface_short}
    for embed in embeds:
        required |= _closed_interface_set(embed, interfaces, next_seen)
    return required


def _emit_edge(
    edges: list[dict],
    *,
    from_id: str,
    to_id: str,
    edge_type: str,
    resolved: bool,
    source_strategy: str,
    base_name: str,
    impl_type: str,
    provenance_file: str = "",
) -> None:
    """Append one ``inherits`` / ``implements`` edge with the shared props shape.

    *provenance_file*, when non-empty, stamps ``props.provenance.file`` (ADR
    0074) so :func:`weld._incremental_purge.purge_edges_by_provenance` can
    attribute the edge to the file that declared it rather than falling back
    to the conservative endpoint-membership purge -- which drops the edge
    outright, with no chance to re-derive the unresolved-sentinel downgrade a
    full discover would produce, when the base's file is deleted and the
    declaring file itself stays clean (bd rifzk). Left empty for
    ``implements`` (interface satisfaction): a type's method set can be
    declared across multiple files in the same package, so there is no
    single unambiguous producing file to attribute the edge to -- stamping
    one file would misattribute rather than merely omit an optimization.
    """
    props: dict = {
        "source_strategy": source_strategy,
        "confidence": "definite" if resolved else "speculative",
        "resolved": resolved,
        "base_name": base_name,
        "impl_type": impl_type,
    }
    if provenance_file:
        props["provenance"] = {"file": provenance_file}
    edges.append({"from": from_id, "to": to_id, "type": edge_type, "props": props})


def _mint_unresolved(nodes: dict[str, dict], short: str, source_strategy: str) -> str:
    """Return the ``symbol:unresolved:<short>`` id, minting the node lazily."""
    target_id = f"symbol:unresolved:{short}"
    nodes.setdefault(
        target_id,
        {
            "type": "symbol",
            "label": short,
            "props": {
                "language": "go",
                "source_strategy": source_strategy,
                "authority": "derived",
                "confidence": "speculative",
                "kind": "unresolved",
                "origin": "unresolved",
                "qualname": short,
            },
        },
    )
    return target_id


def _emit_inherits(
    nodes: dict[str, dict],
    edges: list[dict],
    records: list[dict],
    index: dict[str, str],
    source_strategy: str,
) -> None:
    """Emit one ``inherits`` edge per struct embedding (resolved or sentinel)."""
    seen: set[tuple[str, str]] = set()
    for record in records:
        module = record["module_path"]
        rel_path = record.get("rel_path", "")
        for struct, base_short, base_full in record["embeddings"]:
            from_id = f"symbol:go:{module}:{struct}"
            if from_id not in nodes:
                continue  # struct never promoted to a symbol -> drop.
            if base_short in index:
                target_id = index[base_short]
                resolved = True
            else:
                target_id = _mint_unresolved(nodes, base_short, source_strategy)
                resolved = False
            key = (from_id, target_id)
            if key in seen:
                continue
            seen.add(key)
            _emit_edge(
                edges, from_id=from_id, to_id=target_id, edge_type="inherits",
                resolved=resolved, source_strategy=source_strategy,
                base_name=base_full, impl_type=struct,
                provenance_file=rel_path,
            )


def _emit_implements(
    nodes: dict[str, dict],
    edges: list[dict],
    records: list[dict],
    index: dict[str, str],
    source_strategy: str,
) -> None:
    """Emit ``implements`` edges for each type whose method set satisfies an iface.

    Project-local only (the required set must be known): a type satisfies
    an interface when its closed method set is a superset of the iface's
    non-empty required set. The iface's own symbol is skipped as a
    candidate so an interface never "implements" itself.

    The implementing type's origin is resolved through *index* (short name
    -> minted symbol id), not reconstructed from a record's module: a Go
    method may live in a sibling file of the same package whose per-file
    module differs from the ``type`` declaration's, so the index is the
    only reliable origin. A candidate short name with no project symbol
    (e.g. an unexported type the ``exports`` query skipped) is dropped.
    """
    direct = _direct_methods(records)
    embeds = _embeddings_by_type(records)

    # Union every interface's closed required set, keyed by short name.
    interfaces: dict[str, tuple[set[str], set[str]]] = {}
    for record in records:
        interfaces.update(record["interfaces"])
    iface_required = {
        name: _closed_interface_set(name, interfaces) for name in interfaces
    }

    # Candidate types: every type that declares a method or embeds a base
    # (a method-less embedder still gets promoted methods). Iterated once
    # by short name -- the index supplies the canonical origin id.
    seen: set[tuple[str, str]] = set()
    for type_short in sorted(set(direct) | set(embeds)):
        from_id = index.get(type_short)
        if from_id is None or from_id not in nodes:
            continue
        method_set = _closed_method_set(type_short, direct, embeds)
        for iface_short, required in iface_required.items():
            if iface_short == type_short or not required:
                continue
            if not required.issubset(method_set):
                continue
            target_id = index.get(iface_short)
            if target_id is None or target_id == from_id:
                continue  # iface not a project symbol -> cannot resolve.
            key = (from_id, target_id)
            if key in seen:
                continue
            seen.add(key)
            _emit_edge(
                edges, from_id=from_id, to_id=target_id,
                edge_type="implements", resolved=True,
                source_strategy=source_strategy,
                base_name=iface_short, impl_type=type_short,
            )


def finalise(
    nodes: dict[str, dict],
    edges: list[dict],
    enricher_caches: dict | None,
    source_strategy: str,
) -> None:
    """Emit Go ``inherits`` (embedding) and ``implements`` (satisfaction) edges.

    Mirrors :func:`weld.strategies._rust_inherits.finalise`; see the
    module docstring for the resolution and drop-when-unminted contract.
    """
    if not enricher_caches:
        return
    records = enricher_caches.get("go_inherit_records") or []
    if not records:
        return
    index = build_project_symbol_index(nodes)
    _emit_inherits(nodes, edges, records, index, source_strategy)
    _emit_implements(nodes, edges, records, index, source_strategy)


__all__ = [
    "FileFacts",
    "build_caches",
    "build_project_symbol_index",
    "extract_file_facts",
    "finalise",
    "stage_file",
]
