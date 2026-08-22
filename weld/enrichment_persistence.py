"""Enrichment persistence across rediscovery (ADR 0079).

``props.enrichment`` is authoritative semantic provenance written by
``wd enrich --provider`` (provider-backed) or by an agent following the
``wd enrich --agent-direct`` work plan (ADR 0098).
``wd discover`` rebuilds structural nodes from source and would otherwise
clobber that investment. This module re-attaches persisted enrichment onto a
freshly built graph, keyed by node id and validated by a *node-only* source
fingerprint, so enrichment survives rediscovery and is invalidated only when
the node's own structural source changes.

The fingerprint and its validation are single-sourced here and consumed by
both :mod:`weld.enrich` (the idempotency cache) and discovery post-processing
(re-attachment), so the two never disagree about what "unchanged" means -- the
same single-sourcing discipline ADR 0077 applies to the edge-sort key.
"""

from __future__ import annotations

import hashlib
import json

# Enrichment OUTPUT fields, excluded from the fingerprint: they are what
# enrichment produces, not the structural source it describes, so hashing them
# would let enrichment invalidate itself.
FINGERPRINT_EXCLUDED_PROPS = frozenset(["description", "purpose", "enrichment"])

# POSITIONAL props, excluded from the fingerprint (ADR 0097): they say where a
# node sits, not what it is. `line` (symbols), `span`/`start_line`/`end_line`
# (doc sections) and `line_count` (files) all move when text is inserted
# ABOVE a node -- the one edit that provably cannot change it -- while staying
# put when its own body is rewritten. Hashing them drops enrichment for nodes
# whose source never changed, which is the opposite of the ADR 0079 contract.
FINGERPRINT_EXCLUDED_POSITIONAL_PROPS = frozenset(
    ["line", "start_line", "end_line", "span", "line_count"]
)

_EXCLUDED_FROM_FINGERPRINT = (
    FINGERPRINT_EXCLUDED_PROPS | FINGERPRINT_EXCLUDED_POSITIONAL_PROPS
)

# Structurally-required fields on a persisted enrichment record (ADR 0009 §3).
_REQUIRED_ENRICHMENT_FIELDS = ("provider", "model", "timestamp", "description")


def _stable_props(props: dict) -> dict:
    return {
        key: value
        for key, value in props.items()
        if key not in _EXCLUDED_FROM_FINGERPRINT
    }


def enrichment_fingerprint(node: dict) -> str:
    """SHA-256 over a node's own structural identity (ADR 0079).

    NODE-ONLY: the hash covers the node's ``id``/``type``/``label`` and its
    structural props (everything except the enrichment output fields).
    Neighbors are deliberately excluded so a change to a caller/callee does not
    invalidate a node's own enrichment -- only a change to the node's own source
    does. ``node`` is the ``{"id", "type", "label", "props"}`` shape produced by
    :func:`weld.enrich._snapshot_node` and by :func:`reattach_enrichment`.

    IDENTITY, NOT COORDINATES (ADR 0097): positional props are excluded too, so
    inserting text above a node does not invalidate it. What remains is the
    node's declared identity -- for a symbol its qualname/kind/module/file, for
    a file its exports/constants/imports. The graph carries no body hash, so a
    body-only rewrite that leaves that identity intact is not detected here;
    ``wd enrich --force`` is the deliberate refresh.
    """
    payload = {
        "id": node.get("id"),
        "type": node.get("type"),
        "label": node.get("label"),
        "props": _stable_props(node.get("props") or {}),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def missing_enrichment_fields(enrichment: object) -> list[str]:
    """Required fields *enrichment* lacks, in declaration order (ADR 0009 §3).

    Empty list means structurally complete. A non-dict lacks everything. This
    is the detail behind the write-time rejection (ADR 0097): callers name the
    returned fields so the author learns exactly what to supply, and
    :func:`valid_enrichment` is defined in terms of it so the boolean gate and
    the rejection message can never disagree.
    """
    if not isinstance(enrichment, dict):
        return list(_REQUIRED_ENRICHMENT_FIELDS)
    return [
        key
        for key in _REQUIRED_ENRICHMENT_FIELDS
        if not isinstance(enrichment.get(key), str)
        or not enrichment[key].strip()
    ]


def valid_enrichment(enrichment: object) -> bool:
    """True when *enrichment* is a structurally-complete record (ADR 0009 §3).

    Defined in terms of :func:`missing_enrichment_fields` so the gate and the
    rejection detail cannot drift. The ``isinstance`` term is a short-circuit
    for the overwhelmingly common "no enrichment on this node" case --
    :func:`enrichment_records` calls this once per node of the previous graph
    on every discover, and it agrees with the field walk by construction (a
    non-dict is missing every field).
    """
    return isinstance(enrichment, dict) and not missing_enrichment_fields(enrichment)


def enrichment_records(previous_graph: dict | None) -> dict[str, dict]:
    """Map node id -> structurally-valid enrichment record from *previous_graph*."""
    if not isinstance(previous_graph, dict):
        return {}
    records: dict[str, dict] = {}
    for node_id, node in (previous_graph.get("nodes") or {}).items():
        enrichment = (node.get("props") or {}).get("enrichment")
        if valid_enrichment(enrichment):
            records[node_id] = enrichment
    return records


def _mirror(props: dict, record: dict) -> None:
    """Mirror a persisted record's description/purpose into top-level props.

    After discovery, top-level ``description``/``purpose`` are a pure function
    of ``props.enrichment``: ``description`` always mirrors; ``purpose`` mirrors
    when the record carries one and is otherwise dropped. Making them a pure
    function keeps both discover paths byte-identical regardless of what a
    carried node happened to hold.
    """
    props["enrichment"] = record
    props["description"] = record["description"]
    purpose = record.get("purpose")
    if isinstance(purpose, str) and purpose.strip():
        props["purpose"] = purpose
    else:
        props.pop("purpose", None)


def reattach_enrichment(
    nodes: dict[str, dict], previous_graph: dict | None,
) -> None:
    """Re-attach persisted enrichment onto freshly discovered *nodes* in place.

    For each node that carried a structurally-valid enrichment record in
    *previous_graph*, the record is re-attached (and its description/purpose
    mirrored) iff the node's current node-only fingerprint still matches the
    record's stored fingerprint. A record **without** a stored fingerprint
    (agent-direct/manual enrichment) is persisted verbatim -- there is no source
    fingerprint to compare, so it is sticky until re-enriched. When the stored
    fingerprint is present and no longer matches, the node's own source changed:
    the enrichment is dropped and the fresh structural description stands.

    Runs identically on the full and incremental discover paths, so the
    incremental==full byte-identity contract holds with enrichment present
    (ADR 0079).
    """
    records = enrichment_records(previous_graph)
    if not records:
        return
    for node_id, node in nodes.items():
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        record = records.get(node_id)
        if record is None:
            # No persisted record for this id: strip any enrichment a carried
            # (incremental non-dirty) node still holds, so a never-/no-longer-
            # enriched node is byte-identical on the full and incremental paths.
            props.pop("enrichment", None)
            continue
        stored_fingerprint = record.get("fingerprint")
        if stored_fingerprint is not None and stored_fingerprint != (
            enrichment_fingerprint({"id": node_id, **node})
        ):
            # The node's own structural source changed -> invalidate. The node
            # was re-minted on both paths, so its fresh structural description
            # (if any) stands identically.
            props.pop("enrichment", None)
            continue
        _mirror(props, record)


__all__ = [
    "FINGERPRINT_EXCLUDED_PROPS",
    "FINGERPRINT_EXCLUDED_POSITIONAL_PROPS",
    "enrichment_fingerprint",
    "missing_enrichment_fields",
    "valid_enrichment",
    "enrichment_records",
    "reattach_enrichment",
]
