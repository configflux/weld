"""Enrichment persistence across rediscovery (ADR 0079).

``props.enrichment`` is authoritative semantic provenance written by
``wd enrich`` (provider-backed) or the agent-direct ``/enrich-weld`` path.
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

# Structurally-required fields on a persisted enrichment record (ADR 0009 §3).
_REQUIRED_ENRICHMENT_FIELDS = ("provider", "model", "timestamp", "description")


def _stable_props(props: dict) -> dict:
    return {
        key: value
        for key, value in props.items()
        if key not in FINGERPRINT_EXCLUDED_PROPS
    }


def enrichment_fingerprint(node: dict) -> str:
    """SHA-256 over a node's own structural identity (ADR 0079).

    NODE-ONLY: the hash covers the node's ``id``/``type``/``label`` and its
    structural props (everything except the enrichment output fields).
    Neighbors are deliberately excluded so a change to a caller/callee does not
    invalidate a node's own enrichment -- only a change to the node's own source
    does. ``node`` is the ``{"id", "type", "label", "props"}`` shape produced by
    :func:`weld.enrich._snapshot_node` and by :func:`reattach_enrichment`.
    """
    payload = {
        "id": node.get("id"),
        "type": node.get("type"),
        "label": node.get("label"),
        "props": _stable_props(node.get("props") or {}),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def valid_enrichment(enrichment: object) -> bool:
    """True when *enrichment* is a structurally-complete record (ADR 0009 §3)."""
    if not isinstance(enrichment, dict):
        return False
    for key in _REQUIRED_ENRICHMENT_FIELDS:
        value = enrichment.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


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
    "enrichment_fingerprint",
    "valid_enrichment",
    "enrichment_records",
    "reattach_enrichment",
]
