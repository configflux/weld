"""Per-language agent-trust metrics for ``wd stats`` / ``wd doctor``.

Factored out of :mod:`weld._graph_stats` so that module stays well under
the 400-line cap (see AGENTS.md / CLAUDE.md line-count policy) and the
trust aggregation lives in one cohesive place that both ``wd stats`` and
``wd doctor`` consume.

Motivation
----------
"Do agents trust weld output in language X?" was a vibe. This module
turns it into three numbers, computed per language:

- ``unresolved_symbol_ratio`` -- the share of that language's symbol
  nodes whose ``props.origin`` is ``"unresolved"``. High ratio means the
  origin resolver could not place most symbols, so cross-symbol edges and
  query results in that language are speculative noise.
- ``edge_resolution_rate`` -- the share of that language's ``inherits``
  and ``calls`` edges whose target resolved to a concrete node (the
  resolver set ``props.resolved`` truthy). These are the two edge kinds
  that carry call-graph and inheritance trust; an external or stdlib
  target still counts as resolved because a real in-graph node was found.
- ``description_coverage_pct`` -- the share of that language's symbol
  nodes carrying a non-empty enrichment ``description``.

Defensible choices
------------------
- **Language attribution for symbols**: ``props.language`` on symbol
  nodes. Only symbol nodes carry a language field, and they carry it
  uniformly, so symbol-level metrics are exact.
- **Language attribution for edges**: an edge is attributed to the
  language of its *source* (``from``) node -- the calling / subclassing
  side, which is the language whose extractor and resolver produced the
  edge. A cross-language edge (a node in language ``L`` calling into a
  target written in another language) therefore counts under ``L``. That
  is the trust question being asked ("when an agent reads ``L`` code, do
  its outbound edges resolve?"), so source-side attribution is the
  correct denominator.
- **Edge resolution signal**: the edge's own ``props.resolved`` boolean
  -- the resolver's verdict. It agrees exactly with "the target is not a
  ``symbol:unresolved:*`` placeholder", and is robust on hand-edited
  graphs because it does not require the target node to be present.

All maps are emitted with sorted keys so the payload is deterministic
across runs (ADR 0012 canonical-ordering requirement).
"""

from __future__ import annotations

from typing import Any, Mapping

# Edge kinds that carry per-language trust signal. ``calls`` is the
# call-graph; ``inherits`` is the class hierarchy. Other edge kinds
# (``contains``, ``depends_on``, ``tests`` ...) are structural and not
# part of the "do symbols resolve" question, so they are excluded.
TRUST_EDGE_TYPES: frozenset[str] = frozenset({"calls", "inherits"})

# The origin value that marks a symbol the resolver could not place.
_UNRESOLVED_ORIGIN = "unresolved"


def compute_per_language_trust(
    nodes: Mapping[str, Mapping[str, Any]],
    edges: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> dict[str, dict[str, Any]]:
    """Return per-language trust metrics keyed by language name.

    ``nodes`` is the raw ``data["nodes"]`` mapping (node-id -> node) and
    ``edges`` is the raw ``data["edges"]`` list. Only symbol nodes with a
    ``props.language`` contribute; languages with no symbol node never
    appear in the result.

    The returned dict is sorted by language name and each per-language
    entry carries the symbol, edge, and description counts plus their
    derived ratios. Ratios use a fixed denominator convention so JSON
    consumers never have to special-case an empty bucket:

    - ``unresolved_symbol_ratio`` is ``0.0`` when the language has no
      symbols (nothing unresolved if nothing exists).
    - ``edge_resolution_rate`` is ``1.0`` when the language has no trust
      edges (vacuously fully resolved -- no unresolved edges to drag it
      down), so a language that simply has no calls/inherits edges is not
      reported as untrustworthy.
    - ``description_coverage_pct`` is ``0.0`` when the language has no
      symbols.
    """
    sym_total: dict[str, int] = {}
    sym_unresolved: dict[str, int] = {}
    sym_described: dict[str, int] = {}
    for node in nodes.values():
        if node.get("type") != "symbol":
            continue
        props = node.get("props") or {}
        lang = props.get("language")
        if not lang or not isinstance(lang, str):
            continue
        sym_total[lang] = sym_total.get(lang, 0) + 1
        if props.get("origin") == _UNRESOLVED_ORIGIN:
            sym_unresolved[lang] = sym_unresolved.get(lang, 0) + 1
        desc = props.get("description")
        if isinstance(desc, str) and desc.strip():
            sym_described[lang] = sym_described.get(lang, 0) + 1

    edge_total: dict[str, int] = {}
    edge_resolved: dict[str, int] = {}
    for edge in edges:
        if edge.get("type") not in TRUST_EDGE_TYPES:
            continue
        src = edge.get("from")
        src_node = nodes.get(src) if src is not None else None
        if not src_node:
            continue
        lang = (src_node.get("props") or {}).get("language")
        if not lang or not isinstance(lang, str):
            continue
        edge_total[lang] = edge_total.get(lang, 0) + 1
        if (edge.get("props") or {}).get("resolved"):
            edge_resolved[lang] = edge_resolved.get(lang, 0) + 1

    out: dict[str, dict[str, Any]] = {}
    for lang in sorted(sym_total):
        symbols = sym_total[lang]
        unresolved = sym_unresolved.get(lang, 0)
        described = sym_described.get(lang, 0)
        edges_n = edge_total.get(lang, 0)
        resolved_n = edge_resolved.get(lang, 0)
        out[lang] = {
            "symbols": symbols,
            "unresolved_symbols": unresolved,
            "unresolved_symbol_ratio": (
                round(unresolved / symbols, 4) if symbols else 0.0
            ),
            "edges": edges_n,
            "resolved_edges": resolved_n,
            "edge_resolution_rate": (
                round(resolved_n / edges_n, 4) if edges_n else 1.0
            ),
            "described_symbols": described,
            "description_coverage_pct": (
                round(described / symbols * 100, 2) if symbols else 0.0
            ),
        }
    return out


__all__ = ["compute_per_language_trust", "TRUST_EDGE_TYPES"]
