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
   deterministically, deduplicated).

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
import sys
from pathlib import Path

from weld._workspace_inspect import resolve_child_root
from weld.cross_repo import ResolverContext, run_resolvers
from weld.federation_support import edge_key, sorted_edges
from weld.graph import Graph
from weld.workspace import WorkspaceConfig
from weld.workspace_state import WorkspaceState

__all__ = ["merge_cross_repo_edges"]


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
        print(
            f"[weld] federate: child at {child_root} has no graph.json; skipping",
            file=sys.stderr,
        )
        return None

    try:
        raw = graph_path.read_bytes()
    except OSError as exc:
        print(
            f"[weld] federate: failed to read {graph_path}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None

    graph = Graph(child_root)
    try:
        graph.load()
    except Exception as exc:  # noqa: BLE001 -- one bad child must not sink the pass
        print(
            f"[weld] federate: failed to parse {graph_path}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
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

    Early-returns *graph* unchanged when:

    * ``config.cross_repo_strategies`` is empty (nothing to run), or
    * no child repo has status ``present`` (resolvers would have nothing
      to read), or
    * every present child's graph failed to load (same net effect).

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
    if not edges:
        return graph

    # Translate to the on-wire dict form the serializer consumes. Sort
    # + dedupe via ``edge_key`` so repeated runs produce byte-identical
    # output and so a resolver emitting an edge that was already on the
    # root graph (unlikely on a fresh build, possible when composed with
    # future incremental logic) does not duplicate the entry.
    existing = list(graph.get("edges", []))
    seen_keys = {edge_key(e) for e in existing}
    merged = list(existing)
    for edge in edges:
        payload = edge.to_dict()
        key = edge_key(payload)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(payload)

    graph["edges"] = sorted_edges(merged)
    return graph
