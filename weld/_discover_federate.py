"""Cross-repo resolver invocation for federated ``wd discover``.

This module is the thin bridge between :func:`weld.federation_root.build_root_meta_graph`
(which produces the root's ``repo:*`` nodes) and the cross-repo resolver
framework in :mod:`weld.cross_repo.base` (which turns child graphs into
typed cross-repo edges).

Responsibilities, kept deliberately narrow:

1. Load each *present* child's ``.weld/graph.json`` as a
   :class:`weld.graph.Graph` instance and record the SHA-256 of the bytes
   that were read -- the orchestrator hands those hashes to resolvers so
   they can report the exact byte identity they consumed.
2. Assemble a :class:`weld.cross_repo.ResolverContext` that the
   orchestrator understands. Only workspaces with at least one
   ``cross_repo_strategies`` entry and at least one present child
   participate; everything else is a no-op.
3. Invoke :func:`weld.cross_repo.run_resolvers` and merge the emitted
   edges into the root meta-graph under the contract expected by the
   serializer (``{"from","to","type","props"}`` dicts, sorted
   deterministically, deduplicated) -- dropping the ones whose endpoints
   resolve to nothing, and stamping ``meta.cross_repo`` so a reader can
   tell a measured zero from a pass that never ran (ADR 0137 ss4).

Invariants:

* The caller holds the :class:`WorkspaceLock` already, so child files are
  stable for the duration of this call.
* Children with status other than ``present`` are silently skipped --
  federation-root node emission already filtered them out, so a missing
  child never gets a ``repo:*`` node and must not participate in edge
  resolution either.
* Corrupt or unreadable child graphs are logged to stderr and skipped;
  they never sink the whole discover pass.
* The returned graph is a fresh dict -- we never mutate the input object.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from weld._discover_federate_origin import (
    federated_cpp_project_modules,
    federated_project_modules_for_language,
    federated_python_project_modules,
    retag_external_cpp_origins,
    retag_external_origins_for_language,
    retag_external_python_origins,
    retag_federated_origins_on_disk,
)
from weld._federation_validate import ENDPOINT_OK, FederationIdIndex
from weld._workspace_inspect import resolve_child_root
from weld.cross_repo import ResolverContext, run_resolvers
from weld.federation_support import edge_key, sorted_edges
from weld.graph import Graph
from weld.workspace import WorkspaceConfig
from weld.workspace_state import WorkspaceState
from weld._notice import emit

__all__ = [
    "federated_cpp_project_modules",
    "federated_project_modules_for_language",
    "federated_python_project_modules",
    "merge_cross_repo_edges",
    "retag_external_cpp_origins",
    "retag_external_origins_for_language",
    "retag_external_python_origins",
    "retag_federated_origins_on_disk",
]

#: Per-resolver ceiling on individual dropped-edge warnings before the rest
#: are summarised as a count. A resolver emitting the wrong endpoint shape
#: gets every one of its edges dropped (ADR 0137's motivating case was 14/14
#: on a real workspace), and a transcript of that buries the notices around
#: it; the operator needs the resolver's name, an example, and how many.
DROPPED_EDGE_WARNING_CAP: int = 3


def _load_present_child_graph(
    child_root: Path,
) -> tuple[Graph, bytes] | None:
    """Return ``(Graph, raw_bytes)`` for a present child, or ``None`` on failure.

    The SHA-256 of ``raw_bytes`` is what the resolver context exposes as
    that child's byte identity. Returning the bytes alongside the parsed
    :class:`Graph` means the caller does not re-read the file to compute
    the digest -- the file is read exactly once per pass.

    Returns ``None`` when the child has no ``graph.json`` yet (rare: the
    ledger status check in the caller should have filtered this already)
    or when the graph fails to parse. Both cases print a notice to stderr
    so the operator sees why a child was skipped.
    """
    graph_path = child_root / ".weld" / "graph.json"
    if not graph_path.is_file():
        # This should be unreachable when the caller filters to
        # ``status == "present"`` children, but belt-and-braces guards
        # against a race where the child's graph file is removed between
        # ``build_workspace_state`` and here.
        emit(
            f"[weld] federate: child at {child_root} has no graph.json; skipping"
        )
        return None

    try:
        raw = graph_path.read_bytes()
    except OSError as exc:
        emit(
            f"[weld] federate: failed to read {graph_path}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    graph = Graph(child_root)
    try:
        graph.load()
    except Exception as exc:  # noqa: BLE001 -- one bad child must not sink the pass
        emit(
            f"[weld] federate: failed to parse {graph_path}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    return graph, raw


def _present_child_names(
    config: WorkspaceConfig,
    state: WorkspaceState,
) -> list[str]:
    """Return child names whose ledger status is ``present``, sorted by name.

    This mirrors :func:`weld.federation_root._present_children` but
    returns names rather than :class:`ChildEntry` objects so the caller
    can look up both the entry (for its path) and the ledger status in
    a single lexicographic pass.
    """
    present: list[str] = []
    for child in config.children:
        entry = state.children.get(child.name)
        if entry is None:
            continue
        if entry.status == "present":
            present.append(child.name)
    return sorted(present)


def merge_cross_repo_edges(
    root: Path,
    config: WorkspaceConfig,
    state: WorkspaceState,
    graph: dict,
) -> dict:
    """Return *graph* with cross-repo edges appended, if any were produced.

    Early-returns *graph* unchanged -- and, crucially, **unstamped** -- when
    no resolver could run at all:

    * ``config.cross_repo_strategies`` is empty (nothing to run), or
    * no child repo has status ``present`` (resolvers would have nothing
      to read), or
    * every present child's graph failed to load (same net effect).

    Past that point a resolver pass *has* happened, so ``meta.cross_repo`` is
    stamped even when it produced nothing -- see :func:`_stamp_cross_repo`.
    Edges whose endpoints resolve to no node are dropped with a warning
    attributed to the resolver that emitted them (ADR 0137 ss4): a dangling
    root edge is unreachable by every reader, so keeping it buys nothing, and
    one buggy resolver must not sink ``wd discover``.

    When edges *are* produced, they are merged into ``graph["edges"]``
    (a list) using :func:`weld.federation_support.sorted_edges` so the
    final edge list is deterministic regardless of resolver order.
    Duplicate edges -- same ``(from, to, type, props)`` -- are dropped
    via :func:`weld.federation_support.edge_key` before the sort, so a
    resolver that re-emits an edge the root already carries (from a
    previous run) does not duplicate it.

    The returned dict is the same object as the input (safe because the
    caller is always the discover pipeline which has just built the
    meta-graph for this call). Callers that need isolation should
    ``copy.deepcopy`` before invoking.
    """
    strategies = list(config.cross_repo_strategies)
    if not strategies:
        return graph

    present_names = _present_child_names(config, state)
    if not present_names:
        return graph

    children: dict[str, Graph] = {}
    child_hashes: dict[str, str] = {}
    root_path = Path(root)
    # Look up each present child's path via the config so the child-root
    # derivation matches the federation_root node paths exactly. This
    # avoids any chance of the resolver seeing a different root than the
    # meta-graph recorded in ``path``.
    paths_by_name = {c.name: c.path for c in config.children}
    for name in present_names:
        child_path = paths_by_name.get(name)
        if child_path is None:
            # Defensive: config/state drift should be impossible because
            # ``state`` is built from the same config. Skip cleanly.
            continue
        # Resolve via the same worktree-aware helper used by inspect_child
        # so the loader sees the same on-disk repo the ledger marked
        # ``present``. ADR 0028 §1.
        loaded = _load_present_child_graph(resolve_child_root(root_path, child_path))
        if loaded is None:
            continue
        child_graph, raw = loaded
        children[name] = child_graph
        child_hashes[name] = hashlib.sha256(raw).hexdigest()

    if not children:
        return graph

    context = ResolverContext(
        workspace_root=str(root_path),
        cross_repo_strategies=strategies,
        children=children,
        child_hashes=child_hashes,
    )

    edges = run_resolvers(context)

    # Translate to the on-wire dict form the serializer consumes. Sort
    # + dedupe via ``edge_key`` so repeated runs produce byte-identical
    # output and so a resolver emitting an edge that was already on the
    # root graph (unlikely on a fresh build, possible when composed with
    # future incremental logic) does not duplicate the entry.
    existing = list(graph.get("edges", []))
    seen_keys = {edge_key(e) for e in existing}
    merged = list(existing)
    index = _endpoint_index(graph, children)
    kept = 0
    dropped_by_resolver: dict[str, int] = {}
    for edge in edges:
        payload = edge.to_dict()
        unresolved = [
            field for field in ("from", "to")
            if index.classify_endpoint(payload.get(field)) != ENDPOINT_OK
        ]
        if unresolved:
            _warn_dropped_edge(payload, unresolved, dropped_by_resolver)
            continue
        key = edge_key(payload)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(payload)
        kept += 1

    _warn_dropped_remainder(dropped_by_resolver)
    graph["edges"] = sorted_edges(merged)
    _stamp_cross_repo(graph, strategies, children, kept, dropped_by_resolver)
    return graph


def _endpoint_index(
    root_graph: dict, children: dict[str, Graph],
) -> FederationIdIndex:
    """Index the ids a merged resolver edge is allowed to reference.

    Built from the root meta-graph as it stands before the merge and from the
    child graphs the resolvers were actually handed -- the same two id spaces
    ``wd graph validate`` will judge the written file by (ADR 0137 ss1).

    Every child here is readable by construction (an unreadable one never
    reaches the resolver context), so nothing this index classifies can come
    back ``unverifiable``: at merge time the only question is whether the
    endpoint resolves. A ``repo:<name>`` for a registered-but-absent child is
    therefore dangling, which is right -- ``build_root_meta_graph`` mints
    ``repo:`` nodes for present children only, and a resolver that reads
    ``workspaces.yaml`` itself can name a child the root never minted.
    """
    return FederationIdIndex(
        root_ids=frozenset(root_graph.get("nodes") or {}),
        child_ids={
            name: frozenset(child.dump().get("nodes", {}))
            for name, child in children.items()
        },
    )


def _resolver_label(payload: dict) -> str:
    """Name the resolver an edge came from, for attribution in a warning."""
    props = payload.get("props")
    if isinstance(props, dict):
        strategy = props.get("source_strategy")
        if isinstance(strategy, str) and strategy:
            return strategy
    edge_type = payload.get("type")
    return str(edge_type) if edge_type else "<unattributed resolver>"


def _warn_dropped_edge(
    payload: dict, unresolved: list[str], dropped_by_resolver: dict[str, int],
) -> None:
    """Record a dropped edge and warn about it, up to the per-resolver cap."""
    label = _resolver_label(payload)
    seen = dropped_by_resolver.get(label, 0)
    dropped_by_resolver[label] = seen + 1
    if seen >= DROPPED_EDGE_WARNING_CAP:
        return
    emit(
        f"[weld] federate: {label}: dropping cross-repo edge "
        f"{payload.get('from')!r} -> {payload.get('to')!r}: "
        f"{'/'.join(unresolved)} resolves to no node in the root or any child"
    )


def _warn_dropped_remainder(dropped_by_resolver: dict[str, int]) -> None:
    """Summarise the drops each resolver had beyond the warning cap."""
    for label in sorted(dropped_by_resolver):
        extra = dropped_by_resolver[label] - DROPPED_EDGE_WARNING_CAP
        if extra > 0:
            emit(
                f"[weld] federate: {label}: and {extra} more unresolvable "
                f"cross-repo edge(s) dropped"
            )


def _stamp_cross_repo(
    graph: dict,
    strategies: list[str],
    children: dict[str, Graph],
    kept: int,
    dropped_by_resolver: dict[str, int],
) -> None:
    """Record that a resolver pass ran, on ``meta.cross_repo`` (ADR 0137 ss4).

    Written whenever resolvers ran, **including when they produced no edges**:
    a zero-edge run is exactly the case a reader needs the stamp for, because
    without it "no cross-repo edge points at this repo" is indistinguishable
    from "no resolver ever looked".

    ``resolved_children`` is the resolver *input* set -- the children whose
    graphs loaded and were handed to the resolvers -- not the children that
    happened to yield an edge, so "nothing was available to read" stays
    distinguishable from "everything was read and produced nothing".

    ``strategies`` is likewise an input: the configured set the pass invoked.
    A resolver that raised is isolated by :func:`run_resolvers` and still
    appears here, because what the stamp records is that the pass ran with
    those strategies wired -- not that each of them succeeded.

    ``edges`` counts what this pass merged in and ``dropped`` what it
    discarded as unresolvable. The two sum to the emitted count except where
    two resolvers emit the identical edge, which dedupes to one entry and is
    counted once. Every value is a sorted list or a scalar, so the stamp is
    byte-identical across runs over unchanged input (ADR 0011 ss12).
    """
    meta = graph.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        graph["meta"] = meta
    meta["cross_repo"] = {
        "strategies": sorted(set(strategies)),
        "resolved_children": sorted(children),
        "edges": kept,
        "dropped": sum(dropped_by_resolver.values()),
    }
