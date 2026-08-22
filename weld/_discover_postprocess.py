"""Post-processing pass for discovered graph nodes and edges.

Resolves deferred FK edges, detects agent invocations, overlays topology
nodes/edges from ``discover.yaml``, deduplicates, and builds the final
canonical graph dict with metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from weld._discover_origin_reconcile import reconcile_intra_repo_origins
from weld._events_join import link_producers_consumers
from weld._git import get_git_sha
from weld._git_worktree import get_git_branch
from weld._rel_path import canonical_rel_path, needs_folding
from weld.contract import SCHEMA_VERSION
from weld.enrichment_persistence import reattach_enrichment
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
    *,
    previous_graph: dict | None = None,
) -> dict:
    """Run post-processing and build the final graph dict.

    ADR 0055: after edges are deduplicated, apply the review-state file
    so rejected edges drop and accepted edges keep their promoted
    ``definite`` confidence even if the strategy emitted them as
    ``speculative`` again. The import is local so this hot path does
    not pay the cost when no review-state has been written.

    ADR 0079: with node props final, re-attach any enrichment persisted in
    *previous_graph* onto the freshly built nodes (keyed by node id, gated by
    a node-only source fingerprint) *before* the single canonicalization pass,
    so re-attached fields are canonicalized for free. Both the full and
    incremental discover paths funnel through here, so running it in one place
    is what preserves the incremental==full byte-identity contract when
    enrichment is present.
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
    # ADR 0090: with nodes final, derive the one-hop producer->consumer
    # ``feeds_into`` edges from ``produces``/``consumes`` bindings that meet
    # at a shared ``channel`` node. Runs before the dedup/dangle sweep so
    # the derived edges are cleaned uniformly; idempotent (strips its own
    # prior output first) so the incremental path stays byte-identical to a
    # full discover.
    link_producers_consumers(nodes, edges)
    _clean_and_dedup_edges(nodes, edges)
    from weld._review import apply_review_state as _apply_review_state
    edges[:] = _apply_review_state(root, edges)
    reattach_enrichment(nodes, previous_graph)
    # bd 244j: with every prop final, spell the stored path props canonically.
    # Runs after enrichment re-attachment for the same reason the sorted walk
    # does -- one place, both discover paths, so incremental stays
    # byte-identical to full.
    _canonicalize_path_props(nodes, edges)
    unique_from = _dedup_discovered_from(
        _canonicalize_discovered_from(discovered_from)
    )

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
    # ADR 0096 §3: stamp which branch this graph was discovered on so every
    # downstream answer can name the checkout it came from. Both this key and
    # ``git_sha`` are in ``VOLATILE_META_KEYS``, so they are split into the
    # gitignored sidecar and never reach the content-addressable graph body --
    # which is what keeps ``graph.json`` byte-identical across two worktrees on
    # two branches at the same commit (ADR 0065). Absent (not ``None``) on a
    # detached HEAD or outside git, mirroring ``git_sha``: a key that is only
    # ever present-and-true beats a nullable one every reader must special-case.
    branch = get_git_branch(root)
    if branch is not None:
        meta["git_branch"] = branch

    return _canonical_sorted({"meta": meta, "nodes": nodes, "edges": edges})


#: Node/edge ``props`` keys that carry a repo-relative path (with
#: ``provenance.file``, nested and handled separately below).
#:
#: ``file`` and ``declared_in`` were the two a read-side consumer already
#: matches paths against, which is what made their split visible. ``dir`` --
#: 8 ``package:`` nodes in this repo's own graph, e.g. ``weld/strategies`` --
#: is here because the membership rule is *path-shaped prop*, not *prop some
#: reader currently folds*: an artifact whose file anchors are canonical and
#: whose directory anchors are native is a worse state than either, and the
#: reader that eventually matches on ``props.dir`` should not have to
#: discover that it is the odd one out (bd mzv1). Adding it costs nothing on
#: POSIX, where the whole pass is skipped.
_PATH_PROP_KEYS = ("file", "declared_in", "dir")


def _canonicalize_path_props(nodes: dict[str, dict], edges: list[dict]) -> None:
    """Rewrite the stored path props into the canonical POSIX spelling.

    ADR 0041 already makes node *ids* POSIX; ``props.file``,
    ``props.declared_in`` and ``props.provenance.file`` were left to whichever
    strategy wrote them, and roughly half write ``as_posix()`` while half
    write ``str()``. Identical on POSIX; off it, one graph carries both
    spellings for sibling files, and a graph is meant to be portable -- read
    on another platform, or federated as a child of a POSIX root, those
    anchors reach a reader that correctly refuses to rewrite them, because a
    POSIX reader has no business folding a backslash (bd 244j).

    Canonicalizing at the *writing* platform is what makes the read side
    right, and it is why the whole pass is skipped when this platform's
    separator is already canonical: on POSIX there is no walk and the stored
    bytes are unchanged, so this lands with no migration on any platform weld
    supports (ADR 0065, ADR 0012 §3). Off POSIX the result is a byte-level
    canonicalization a plain re-discovery reproduces.

    Non-string values are left exactly as they are.
    :func:`weld._rel_path.canonical_rel_path` answers ``""`` for a non-string
    -- correct for a comparison, destructive for a rewrite.
    """
    if not needs_folding():
        return
    for item in (*nodes.values(), *edges):
        props = item.get("props")
        if not isinstance(props, dict):
            continue
        for key in _PATH_PROP_KEYS:
            value = props.get(key)
            if isinstance(value, str):
                props[key] = canonical_rel_path(value)
        provenance = props.get("provenance")
        if isinstance(provenance, dict) and isinstance(provenance.get("file"), str):
            provenance["file"] = canonical_rel_path(provenance["file"])


def _canonicalize_discovered_from(paths: list[str]) -> list[str]:
    """Canonicalize the ``meta.discovered_from`` manifest, preserving order.

    The same treatment as the node/edge props above, applied to the one path
    list that lives in ``meta`` -- an artifact canonical in its props and
    native in its manifest would be a worse state than either.

    Not :func:`weld._rel_path.canonical_rel_paths`, despite the near-identical
    name: that one answers a *set*, for the side of a comparison that is
    tested against repeatedly, and order is contract here (ADR 0012 §3).
    Returns the input unchanged on POSIX.
    """
    if not needs_folding():
        return paths
    return [canonical_rel_path(p) if isinstance(p, str) else p for p in paths]


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
