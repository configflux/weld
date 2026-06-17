"""Post-processing pass for discovered graph nodes and edges.

Resolves deferred FK edges, detects agent invocations, overlays topology
nodes/edges from ``discover.yaml``, deduplicates, and builds the final
canonical graph dict with metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from weld._discover_origin_reconcile import reconcile_intra_repo_origins
from weld._git import get_git_sha
from weld.contract import SCHEMA_VERSION
from weld.graph import _schema_version_for
from weld.graph_closure import close_graph
from weld.serializer import _edge_sort_key


def post_process(
    nodes: dict[str, dict],
    edges: list[dict],
    context: dict,
    config: dict,
    root: Path,
    discovered_from: list[str],
) -> dict:
    """Run post-processing and build the final graph dict.

    ADR 0055: after edges are deduplicated, apply the review-state file
    so rejected edges drop and accepted edges keep their promoted
    ``definite`` confidence even if the strategy emitted them as
    ``speculative`` again. The import is local so this hot path does
    not pay the cost when no review-state has been written.
    """
    _resolve_fk_edges(edges, context)
    _detect_agent_invocations(nodes, edges, context)
    _apply_topology_overlay(nodes, edges, config, root)
    close_graph(nodes, edges)
    # ADR 0042: heal cross-glob origin clobber. A multi-glob run mints a
    # first-party call target as ``external`` in the batch that does not
    # own its module, and the orchestrator's last-batch-wins merge lets
    # that speculative tag overwrite the definite ``project`` node the
    # owning batch walked. Promote those back to ``project`` using the
    # run-level project module set. No-op on an already-correct graph.
    reconcile_intra_repo_origins(nodes, context)
    _clean_and_dedup_edges(nodes, edges)
    from weld._review import apply_review_state as _apply_review_state
    edges[:] = _apply_review_state(root, edges)
    unique_from = _dedup_discovered_from(discovered_from)

    meta: dict = {
        "version": SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "discovered_from": unique_from,
        # Federation schema version (ADR 0011 section 11, ADR 0012 section 4).
        "schema_version": _schema_version_for(nodes),
    }
    sha = get_git_sha(root)
    if sha is not None:
        meta["git_sha"] = sha

    return _canonical_sorted({"meta": meta, "nodes": nodes, "edges": edges})


def _sort(v):
    """Recursively rebuild every dict with sorted keys; preserve list order.

    Allocates fresh containers at every level (dict-comprehension and
    list-comprehension), so the returned tree shares no mutable structure
    with the input. Scalars pass through unchanged. This is the in-memory
    recursive sorted-key shape asserted by
    ``weld/tests/weld_determinism_dict_order_test.py``.
    """
    if isinstance(v, dict):
        return {k: _sort(v[k]) for k in sorted(v)}
    if isinstance(v, list):
        return [_sort(x) for x in v]
    return v


def _canonical_sorted(graph: dict) -> dict:
    """Build the canonical in-memory graph in a single recursive walk.

    ADR 0077: this fuses what used to be two passes —
    ``_sort(canonical_graph(...))`` — into one. ``canonical_graph``'s only
    output ``_sort`` could not reproduce was the edge ordering (``_sort``
    preserves list order); everything else it deep-copied was re-materialized
    by ``_sort`` immediately after, so that deep-copy was redundant. Here the
    edge sort moves *into* the single sorted-rebuild walk, eliminating the
    extra ``copy.deepcopy``.

    Behavior is identical to the old two-pass: top-level keys (and every
    nested dict's keys) are sorted, the edge list is sorted by the ADR 0012
    §3 rule-2 tuple via the single-sourced ``weld.serializer._edge_sort_key``,
    and each edge/node/meta value is recursively key-sorted. The walk builds a
    fresh tree and never mutates its input.

    ``nodes``/``edges`` are guaranteed present so the contract shape holds even
    for an empty graph; the defaults are folded in *before* the sorted walk so
    the emitted top-level keys stay in sorted order whether or not the input
    supplied them (matching the legacy ``_sort`` rebuild, which re-sorted the
    top level after ``canonical_graph``'s ``setdefault``).

    ``sorted`` (here) and ``list.sort`` (the old ``canonical_graph``) are both
    stable Timsort with the same key over the same input list, so edges that
    tie on the sort key keep identical relative order across both paths.
    """
    defaults: dict = {"nodes": {}, "edges": []}
    keys = sorted(set(graph) | set(defaults))
    out: dict = {}
    for key in keys:
        value = graph.get(key, defaults.get(key))
        if key == "edges" and isinstance(value, list):
            ordered = sorted(value, key=_edge_sort_key)
            out[key] = [_sort(edge) for edge in ordered]
        else:
            out[key] = _sort(value)
    return out


def _resolve_fk_edges(edges: list[dict], context: dict) -> None:
    """Resolve deferred ``__table__:`` FK edges in-place."""
    table_to_entity = context.get("table_to_entity", {})
    for e in context.get("pending_fk_edges", []):
        to_id = e["to"]
        if to_id.startswith("__table__:"):
            real = table_to_entity.get(to_id.split(":", 1)[1])
            if real:
                edges.append({**e, "to": real})
        else:
            edges.append(e)


def _detect_agent_invocations(
    nodes: dict[str, dict], edges: list[dict], context: dict,
) -> None:
    """Emit ``invokes`` edges where command texts mention agent names."""
    agent_names = [nid.split(":", 1)[1] for nid in nodes if nid.startswith("agent:")]
    for cmd_nid, text in context.get("command_texts", {}).items():
        for aname in agent_names:
            if aname.lower() in text.lower():
                edges.append({
                    "from": cmd_nid,
                    "to": f"agent:{aname}",
                    "type": "invokes",
                    "props": {
                        "source_strategy": "post_processing",
                        "confidence": "inferred",
                    },
                })


def _apply_topology_overlay(
    nodes: dict[str, dict], edges: list[dict], config: dict, root: Path,
) -> None:
    """Merge topology nodes/edges from ``discover.yaml``."""
    topology = config.get("topology", {})

    for sn in topology.get("nodes", []):
        nid = sn["id"]
        if nid not in nodes:
            props = dict(sn.get("props", {})) if isinstance(sn.get("props"), dict) else {}
            if "path" in props and not (root / props["path"]).is_dir():
                continue
            props.setdefault("source_strategy", "topology")
            props.setdefault("authority", "manual")
            props.setdefault("confidence", "definite")
            nodes[nid] = {"type": sn["type"], "label": sn.get("label", nid), "props": props}

    for se in topology.get("edges", []):
        ep = dict(se.get("props", {})) if isinstance(se.get("props"), dict) else {}
        ep.setdefault("source_strategy", "topology")
        ep.setdefault("confidence", "definite")
        edges.append({"from": se["from"], "to": se["to"], "type": se["type"], "props": ep})

    for mapping in (topology.get("entity_packages") or []):
        pkg_id, modules = mapping.get("package", ""), mapping.get("modules", [])
        if isinstance(modules, list):
            for nid, n in list(nodes.items()):
                if n["type"] == "entity" and n["props"].get("module") in modules:
                    edges.append({
                        "from": pkg_id,
                        "to": nid,
                        "type": "contains",
                        "props": {"source_strategy": "topology", "confidence": "definite"},
                    })


def _clean_and_dedup_edges(nodes: dict[str, dict], edges: list[dict]) -> None:
    """Remove dangling edges and deduplicate in-place."""
    valid = [e for e in edges if e["from"] in nodes and e["to"] in nodes]
    seen: set[str] = set()
    deduped: list[dict] = []
    for e in valid:
        key = f"{e['from']}|{e['to']}|{e['type']}"
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    edges[:] = deduped


def _dedup_discovered_from(discovered_from: list[str]) -> list[str]:
    """Return ``discovered_from`` with duplicates removed, order preserved."""
    seen: set[str] = set()
    return [p for p in discovered_from if p not in seen and not seen.add(p)]  # type: ignore[func-returns-value]
