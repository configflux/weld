"""Shared channel-node helpers for the events strategy (tracked project).

Both :mod:`weld.strategies.events_config` and
:mod:`weld.strategies.events_callsite` produce ``channel`` nodes with
the same shape. Centralizing the constructor keeps protocol metadata
in one place and guarantees the two halves of the strategy can never
drift out of sync on ADR 0086 / tracked project vocabulary.

The module intentionally has no knowledge of which half called it --
the rel_path and transport come in from the caller.
"""

from __future__ import annotations

from weld._node_ids import file_id as _canonical_file_id

def file_node_id(rel_path: str) -> str:
    """Return the canonical ``file:`` node id for a repo-relative path.

    Mirrors ``python_module``'s file-anchor minting (ADR 0041): the id is
    the extensionless POSIX path (``file:pkg/mod`` for ``pkg/mod.py``), so a
    file->channel edge shares an *exact* endpoint with the ``file:`` node
    ``python_module`` mints. Building the endpoint as ``f"file:{rel_path}"``
    (extension kept) makes it dangle against the canonical node and be pruned
    by ``weld._discover_postprocess._clean_and_dedup_edges`` (exact match, no
    extension normalization). Centralized here so the four events-family emit
    sites (``events_callsite``, ``events_bindings``, ``events_mqtt``,
    ``events_config``) share one minter and cannot drift back to the raw form.
    """
    return _canonical_file_id(rel_path)

def channel_id(transport: str, name: str) -> str:
    """Build the channel node id for a declared async surface.

    The id is keyed on ``(transport, name)`` so that the same topic
    declared in multiple places collapses into a single node, mirroring
    how http_client's rpc ids key on ``(method, url)``.
    """
    return f"channel:{transport}:{name}"

def channel_node(*, transport: str, name: str, rel_path: str) -> dict:
    """Build a channel node stamped with ADR 0086 interaction metadata.

    The caller supplies ``transport`` (``kafka``/``amqp``/``tcp``),
    the declared ``name``, and the repo-relative path the declaration
    was discovered in. Every other prop is fixed: declared channels
    are event-family, pub/sub surfaces whose boundary_kind is
    ``internal`` until xoq.6.2 links producers and consumers.
    """
    return {
        "type": "channel",
        "label": name,
        "props": {
            "name": name,
            "source_strategy": "events",
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["implementation"],
            # Interaction-surface metadata (ADR 0086, tracked project).
            "protocol": "event",
            "surface_kind": "pub_sub",
            "transport": transport,
            # Declarations are not inherently directional. Producer /
            # consumer linking (xoq.6.2) will stamp directional edges
            # on top of this node.
            "boundary_kind": "internal",
            "declared_in": rel_path,
        },
    }

def contains_edge(src: str, dst: str) -> dict:
    """Build a ``contains`` edge from a declaring file to a channel node."""
    return {
        "from": src,
        "to": dst,
        "type": "contains",
        "props": {
            "source_strategy": "events",
            "confidence": "definite",
        },
    }
