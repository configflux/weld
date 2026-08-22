"""Strategy: generic DDS ``.idl`` contract and topic extraction.

Parses OMG IDL data-definition files used by non-ROS2 DDS stacks
(CycloneDDS, FastDDS) into weld nodes, per ADR 0086's static-truth
policy. The parse is text-only (see
:mod:`weld.strategies._dds_idl_parser`): no ``idlc`` / ``fastddsgen``
run, no ``#include`` following, no code evaluation.

Emitted vocabulary (all existing -- no schema change):

- ``struct`` -> ``contract:dds:<qualified>`` node with typed fields.
- ``enum``   -> ``enum:dds:<qualified>`` node with member identifiers.
- Every topic-capable struct also mints a ``channel:ros2_dds:<qualified>``
  node (surface_kind ``pub_sub``). A struct is topic-capable unless it is
  explicitly ``@nested``; DDS code generators emit TypeSupport for every
  top-level struct, so each is a publishable topic type. The channel is
  ``confidence="definite"`` when an ``@topic`` annotation or a
  ``#pragma keylist`` names the struct, else ``"inferred"``.

Transport choice (the design decision for this strategy): channels reuse
the existing ``ros2_dds`` transport value rather than minting a new
``dds`` value. ``ros2_dds`` denotes the DDS/RTPS *wire*, not the ROS2
framework (ROS2 was merely the first DDS consumer weld modelled), so
reuse keeps this a purely additive strategy (no ``SCHEMA_VERSION`` bump,
no ADR) and lets the cross-repo ``channel_binding`` resolver bind a
raw-DDS topic to a ROS2 topic that ride the same wire. ``protocol`` is
left unset: no ``PROTOCOL_VALUES`` member fits non-ROS2 DDS (``event`` is
incompatible with ``ros2_dds`` per ``PROTOCOL_TRANSPORT_COMPATIBILITY``
and ``ros2`` would be a false framework claim), so omission is the
honest, coherence-check-skipping choice.

Structural edges: ``file:<rel> --contains--> {contract, enum, channel}``
and ``channel --implements--> contract`` (kind ``dds_topic_type``),
mirroring how ``ros2_topology`` links a topic to its message interface.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import canonical_slug, entity_id, file_id
from weld._rel_path import rel_to_root
from weld.strategies._dds_idl_parser import IdlFile, parse_idl_text
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies.events_shared import channel_id

_STRATEGY = "dds_idl"
#: DDS rides the RTPS/DDS wire; reuse the existing transport value rather
#: than minting a new one (see module docstring for the full rationale).
_TRANSPORT = "ros2_dds"


def _contract_id(qualified: str) -> str:
    return entity_id("contract", platform="dds", name=qualified)


def _enum_id(qualified: str) -> str:
    return entity_id("enum", platform="dds", name=qualified)


def _channel_id(qualified: str) -> str:
    return channel_id(_TRANSPORT, canonical_slug(qualified))


def _edge(src: str, dst: str, etype: str, *, confidence: str, **extra) -> dict:
    props = {"source_strategy": _STRATEGY, "confidence": confidence}
    props.update(extra)
    return {"from": src, "to": dst, "type": etype, "props": props}


def _contract_node(struct, rel_path: str) -> dict:
    return {
        "type": "contract",
        "label": struct.qualified_name,
        "props": {
            "name": struct.qualified_name,
            "fields": [dict(f) for f in struct.fields],
            "source_strategy": _STRATEGY,
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["implementation"],
            "declared_in": rel_path,
        },
    }


def _enum_node(enum, rel_path: str) -> dict:
    return {
        "type": "enum",
        "label": enum.qualified_name,
        "props": {
            "name": enum.qualified_name,
            "members": list(enum.members),
            "source_strategy": _STRATEGY,
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["implementation"],
            "declared_in": rel_path,
        },
    }


def _channel_node(struct, rel_path: str) -> dict:
    # ``protocol`` is deliberately omitted -- see module docstring.
    return {
        "type": "channel",
        "label": struct.qualified_name,
        "props": {
            "name": struct.qualified_name,
            "topic": struct.qualified_name,
            "type_name": struct.qualified_name,
            "source_strategy": _STRATEGY,
            "authority": "canonical",
            "confidence": "definite" if struct.topic_definite else "inferred",
            "roles": ["implementation"],
            "surface_kind": "pub_sub",
            "transport": _TRANSPORT,
            "boundary_kind": "internal",
            "declared_in": rel_path,
        },
    }


def _build_fragment(
    parsed: IdlFile, rel_path: str, nodes: dict[str, dict], edges: list[dict],
) -> bool:
    """Turn one parsed ``.idl`` file into nodes/edges. Returns True if any."""
    file_nid = file_id(rel_path)
    emitted = False
    for struct in parsed.structs:
        cid = _contract_id(struct.qualified_name)
        nodes[cid] = _contract_node(struct, rel_path)
        edges.append(_edge(file_nid, cid, "contains", confidence="definite"))
        emitted = True
        if struct.is_topic:
            chid = _channel_id(struct.qualified_name)
            nodes[chid] = _channel_node(struct, rel_path)
            edges.append(
                _edge(file_nid, chid, "contains", confidence="definite")
            )
            edges.append(_edge(
                chid, cid, "implements",
                confidence="definite" if struct.topic_definite else "inferred",
                kind="dds_topic_type",
            ))
    for enum in parsed.enums:
        eid = _enum_id(enum.qualified_name)
        nodes[eid] = _enum_node(enum, rel_path)
        edges.append(_edge(file_nid, eid, "contains", confidence="definite"))
        emitted = True
    return emitted


def _iter_sources(
    root: Path,
    pattern: str,
    excludes: list[str] | None = None,
) -> list[Path]:
    """Resolve *pattern* under *root*, pruning excluded directories.

    Delegates to :func:`weld.strategies._glob_resolve.resolve_glob`, which
    prunes matching directories during descent rather than filtering the
    resolved list (bd 9gdq): ``matches_exclude`` tests the file path with
    no ancestor-directory check, so the directory form (``pkg/tests``)
    never matched ``pkg/tests/foo.idl`` and the subtree leaked.
    """
    return resolve_glob(root, pattern, excludes)


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract ``.idl`` declarations into contract, enum, and channel nodes."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)
    excludes = source.get("exclude", [])

    for path in _iter_sources(root, pattern, excludes):
        if not path.is_file() or path.suffix != ".idl":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parsed = parse_idl_text(text)
        if not (parsed.structs or parsed.enums):
            continue
        rel_path = rel_to_root(path, root)
        if _build_fragment(parsed, rel_path, nodes, edges):
            discovered_from.append(rel_path)

    return StrategyResult(nodes, edges, discovered_from)
