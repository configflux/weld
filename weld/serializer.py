#!/usr/bin/env python3
"""Canonical serializer for ``graph.json``.

This module is the single permitted emission path for ``graph.json``. It
enforces the determinism contract documented in ADR 0012 §3:

1. **Nodes sorted by ``id``** (lexicographic, bytewise on UTF-8 encoding).
2. **Edges sorted by the tuple**
   ``(from, to, type, json.dumps(props, sort_keys=True))``.
3. **Props serialized with ``sort_keys=True``** at every level of nesting.
4. **Top-level object keys serialized with ``sort_keys=True``.``
5. **Whitespace fixed, and line-oriented** -- one entity per line,
   ``ensure_ascii=False``. See below.
6. **Trailing newline** -- exactly one ``\\n`` at end of emitted text.

Rule 5 is the **entity-per-line** layout (ADR 0110, bd az06.2), which
replaced ``indent=2``. Every node and every edge occupies exactly one
line; the ``meta`` header keeps an ``indent=2`` block because it is small
and read by humans. The result is still a *single valid JSON document* --
not JSON Lines -- so every reader keeps calling ``json.loads`` on the whole
file and nothing about the schema changes::

    {
    "edges": [
    {"from": "...", "props": {...}, "to": "...", "type": "contains"},
    ...
    ],
    "meta": {
      "schema_version": 2
    },
    "nodes": {
    "file:weld/graph": {"label": "graph", "props": {...}, "type": "file"},
    ...
    }
    }

Why this shape and not one-JSON-object-per-line (JSONL): a graph change
then diffs as the entity lines it touched instead of the whole file
(measured on this repo: 711368 lines to 49303, and a *smaller* file --
21.1 MB to 15.9 MB, because the indentation was a quarter of the bytes),
while JSONL would have broken every ``json.loads`` reader, the three
whole-file-hash integrity surfaces, and the schema, to buy the same
per-entity diff. It also emits and parses faster than ``indent=2``, so
there is no cost to weigh against it.

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

# ``_edge_sort_key`` is exported (ADR 0077) so the discover post-process can
# reuse the *single* canonical edge-sort definition when it fuses the edge
# sort into its recursive key-sort walk, instead of re-spelling the tuple.
__all__ = [
    "canonical_graph",
    "dumps_graph",
    "dumps_graph_canonical",
    "_edge_sort_key",
]

# Fixed canonical dump settings. The whitespace contract lives here so any
# drift is a single-line change reviewable in a single diff.
#
# ``_ENTITY_SETTINGS`` renders one node or one edge onto one line -- no
# ``indent``, so ``json.dumps`` emits its compact single-line form. The
# separators are left at the default ``(", ", ": ")`` rather than the tight
# ``(",", ":")``: a line a reviewer has to read is worth the two bytes, and
# the bytes saved by dropping the indentation dwarf them either way.
#
# ``_HEADER_SETTINGS`` renders every *other* top-level value (today only
# ``meta``) as an ``indent=2`` block. That half of the file is small,
# human-read, and holds lists like ``discovered_from`` whose own entries
# want a line each for the same diff reason the entities do.
_ENTITY_SETTINGS: dict[str, Any] = {
    "ensure_ascii": False,
    "sort_keys": True,
}
_HEADER_SETTINGS: dict[str, Any] = {
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


def _object_key(key: Any) -> str:
    """The JSON text for *key* used as an object member name.

    Mirrors what ``json.dumps`` does with a non-string mapping key, so the
    hand-rolled entity lines below cannot emit a bare ``1:`` where the
    stdlib would have emitted ``"1":``. Node ids are strings everywhere in
    weld; this exists so the emitter degrades to valid JSON rather than to
    a corrupt file if one ever is not.
    """
    if isinstance(key, str):
        return json.dumps(key, **_ENTITY_SETTINGS)
    if key is True:
        return '"true"'
    if key is False:
        return '"false"'
    if key is None:
        return '"null"'
    if isinstance(key, (int, float)):
        return json.dumps(json.dumps(key))
    raise TypeError(f"graph object keys must be str, got {type(key).__name__}")


def _nodes_block(nodes: dict) -> str:
    """``nodes`` as ``{`` + one ``"id": {...}`` line per node + ``}``.

    Keys are sorted here rather than left to ``sort_keys=True``, because the
    dumper never sees the ``nodes`` mapping as a whole -- each value is
    dumped on its own. The order is the same one ``sort_keys`` produces
    (``sorted`` over the raw keys), which is ADR 0012 §3 rule 1.
    """
    if not nodes:
        return "{}"
    lines = ",\n".join(
        f"{_object_key(key)}: {json.dumps(nodes[key], **_ENTITY_SETTINGS)}"
        for key in sorted(nodes)
    )
    return "{\n" + lines + "\n}"


def _edges_block(edges: list) -> str:
    """``edges`` as ``[`` + one edge object per line + ``]``.

    Order is taken as given: the caller has already applied ADR 0012 §3
    rule 2 (:func:`canonical_graph`, or the fast path's
    :func:`_is_already_canonical` check).
    """
    if not edges:
        return "[]"
    lines = ",\n".join(json.dumps(edge, **_ENTITY_SETTINGS) for edge in edges)
    return "[\n" + lines + "\n]"


def _dumps_canonical_text(canonical: dict) -> str:
    """Emit the entity-per-line text for an already-canonical *canonical*.

    Top-level keys are emitted in sorted order (rule 4). ``nodes`` and
    ``edges`` get the line-oriented blocks above; every other key -- today
    only ``meta``, plus anything a forward-compatible writer adds -- is
    handed to ``json.dumps`` with the header settings, so an unknown key
    can never be emitted in a shape this module invented.
    """
    parts: list[str] = []
    for key in sorted(canonical):
        value = canonical[key]
        if key == "nodes" and isinstance(value, dict):
            body = _nodes_block(value)
        elif key == "edges" and isinstance(value, list):
            body = _edges_block(value)
        else:
            body = json.dumps(value, **_HEADER_SETTINGS)
        parts.append(f"{_object_key(key)}: {body}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def dumps_graph(graph: dict) -> str:
    """Emit the canonical JSON text for ``graph``.

    Applies :func:`canonical_graph` then serialises in the entity-per-line
    layout (rule 5) with a single trailing newline.

    The input dict is never mutated.
    """
    return _dumps_canonical_text(canonical_graph(graph))


def _is_already_canonical(graph: dict) -> bool:
    """True when *graph* is already in canonical shape (cheap structural check).

    Canonical means ``nodes`` is a dict and ``edges`` is a list already in
    the ADR 0012 §3 rule-2 order. Node-key ordering is handled by
    ``sort_keys=True`` at emit time regardless, so only the edge order needs
    verifying. The check is O(edges) and avoids re-sorting or copying.
    """
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, dict) or not isinstance(edges, list):
        return False
    prev: tuple[str, str, str, str] | None = None
    for edge in edges:
        if not isinstance(edge, dict):
            return False
        key = _edge_sort_key(edge)
        if prev is not None and key < prev:
            return False
        prev = key
    return True


def dumps_graph_canonical(graph: dict) -> str:
    """Emit canonical JSON for a graph that is *already* canonical.

    Byte-for-byte identical to :func:`dumps_graph` but skips the defensive
    deep copy + re-sort that :func:`canonical_graph` performs -- a ~900 ms
    saving on a 6.5k-node graph (bd 85tb.2). The output of
    :func:`weld._discover_postprocess.post_process` and any graph loaded
    back from a canonical ``graph.json`` already satisfy the contract, so
    re-canonicalizing them is pure waste.

    Safety: if the input turns out *not* to be canonical (edges out of
    order, or a list-form ``nodes``), this transparently falls back to the
    full :func:`dumps_graph` path so output can never diverge from the
    contract. The fast path is therefore an optimization, never a new way
    to emit non-canonical bytes.
    """
    if not _is_already_canonical(graph):
        return dumps_graph(graph)
    return _dumps_canonical_text(graph)
