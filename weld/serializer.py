#!/usr/bin/env python3
"""Canonical serializer for ``graph.json``.

This module is the single permitted emission path for ``graph.json``. It
enforces the determinism contract documented in ADR 0012 §3:

1. **Nodes sorted by ``id``** (lexicographic, bytewise on UTF-8 encoding).
2. **Edges sorted by the tuple**
   ``(from, to, type, json.dumps(props, sort_keys=True))``.
3. **Props serialized with ``sort_keys=True``** at every level of nesting.
4. **Top-level object keys serialized with ``sort_keys=True``.``
5. **Whitespace and indentation fixed** -- ``indent=2``, ``ensure_ascii=False``.
6. **Trailing newline** -- exactly one ``\\n`` at end of emitted text.

Two entry points:

* :func:`canonical_graph` returns the contract-shaped dict -- ``nodes`` as a
  dict keyed by ``id`` (emitted in sorted-key order by the JSON dumper) and
  ``edges`` sorted by the ADR tuple. This is useful when callers want the
  canonical in-memory shape (e.g., for diffing or equality checks) without
  serialising to bytes.
* :func:`dumps_graph` emits the canonical JSON text with the fixed whitespace
  contract and a trailing newline. This is the function that every writer of
  ``graph.json`` must use.

Rule 1 (nodes sorted by ``id``) is enforced by ``sort_keys=True`` at the
JSON emission layer: a dict keyed by node id, emitted with sorted keys,
yields nodes in lex order. Rule 2 (edge sort) requires explicit sort --
edges are a list, and JSON list order is not touched by ``sort_keys``.
"""

from __future__ import annotations

import copy
import json
from typing import Any

__all__ = ["canonical_graph", "dumps_graph"]

# Fixed canonical dump settings. The whitespace contract lives here so any
# drift is a single-line change reviewable in a single diff.
_JSON_SETTINGS: dict[str, Any] = {
    "indent": 2,
    "ensure_ascii": False,
    "sort_keys": True,
}


def _edge_sort_key(edge: dict) -> tuple[str, str, str, str]:
    """Compute the ADR 0012 §3 rule 2 sort key for an edge.

    The ``json.dumps(props, sort_keys=True)`` component breaks ties between
    edges that share endpoints and type but carry different props. It uses
    ``ensure_ascii=True`` deliberately: the sort key is a pure ordering
    primitive, never emitted to disk, and ASCII-only strings compare
    byte-stably without depending on locale-sensitive Unicode collation.
    """
    props = edge.get("props", {}) or {}
    return (
        str(edge.get("from", "")),
        str(edge.get("to", "")),
        str(edge.get("type", "")),
        json.dumps(props, sort_keys=True, ensure_ascii=True),
    )


def _nodes_as_dict(nodes: Any) -> dict[str, dict]:
    """Normalise ``nodes`` to the canonical dict form keyed by node ``id``.

    Accepts either the in-memory dict form (``{id: {type, label, props}}``)
    used by ``weld.graph.Graph`` or a list form
    (``[{id, type, label, props}, ...]``) emitted by some callers. Returns
    a dict; the JSON dumper is responsible for emitting keys in sorted
    order (ADR 0012 §3 rule 1) via ``sort_keys=True``.
    """
    if isinstance(nodes, dict):
        return copy.deepcopy(nodes)
    if isinstance(nodes, list):
        out: dict[str, dict] = {}
        for entry in nodes:
            if not isinstance(entry, dict) or "id" not in entry:
                raise TypeError(
                    "list-form graph['nodes'] entries must be dicts with an 'id' key"
                )
            nid = str(entry["id"])
            body = {k: copy.deepcopy(v) for k, v in entry.items() if k != "id"}
            out[nid] = body
        return out
    raise TypeError(
        f"graph['nodes'] must be dict or list, got {type(nodes).__name__}"
    )


def _edges_as_sorted_list(edges: Any) -> list[dict]:
    """Normalise ``edges`` to a sorted list per ADR 0012 §3 rule 2."""
    if not isinstance(edges, list):
        raise TypeError(
            f"graph['edges'] must be list, got {type(edges).__name__}"
        )
    entries = [copy.deepcopy(e) for e in edges]
    entries.sort(key=_edge_sort_key)
    return entries


def canonical_graph(graph: dict) -> dict:
    """Return the canonical shape of ``graph`` without mutating the input.

    The returned dict has:

    * ``meta``: unchanged (key ordering is decided by the JSON dumper, not by
      this function -- ``dumps_graph`` handles that via ``sort_keys=True``).
    * ``nodes``: a **dict** keyed by node ``id``. The JSON dumper emits
      these keys in sorted order via ``sort_keys=True`` (rule 1).
    * ``edges``: a list sorted by ``(from, to, type, json.dumps(props, sort_keys=True))``.

    Extra top-level keys (forward-compat) are preserved verbatim. The input
    dict is never mutated.
    """
    out: dict = {}
    for key, value in graph.items():
        if key == "nodes":
            out["nodes"] = _nodes_as_dict(value)
        elif key == "edges":
            out["edges"] = _edges_as_sorted_list(value)
        else:
            out[key] = copy.deepcopy(value)
    # Guarantee both keys exist even if the input omitted them -- consumers
    # expect the contract shape.
    out.setdefault("nodes", {})
    out.setdefault("edges", [])
    return out


def dumps_graph(graph: dict) -> str:
    """Emit the canonical JSON text for ``graph``.

    Applies :func:`canonical_graph` then serialises with the fixed settings
    bundle (``indent=2``, ``ensure_ascii=False``, ``sort_keys=True``) and a
    single trailing newline.

    The input dict is never mutated.
    """
    canonical = canonical_graph(graph)
    text = json.dumps(canonical, **_JSON_SETTINGS)
    return text + "\n"
