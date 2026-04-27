"""Root meta-graph builder for polyrepo federation (ADR 0011 section 4).

This module owns the *pure* dispatch that ``wd discover`` takes when the
workspace root declares a :file:`workspaces.yaml`. Instead of walking the
root tree as one repository (which would mis-discover the workspace as a
monorepo), it emits a meta-graph consisting exclusively of ``repo:<name>``
nodes -- one per registered child whose status is ``present`` in the
current workspace ledger. Cross-repo edges are intentionally out of scope
for this branch; they land under the dedicated resolver tasks.

The builder is deterministic by construction:

* Children are sorted by ``name`` before emission so iteration order does
  not depend on ``workspaces.yaml`` declaration order.
* Path segments are derived from the declared relative path with POSIX
  semantics; absolute paths are rejected at registry-load time upstream.
* Missing/uninitialized/corrupt children are skipped here -- the
  workspace ledger records them, but they must not leak repo nodes so
  the root graph never references an absent fixture.

The module is intentionally self-contained: it depends only on
:mod:`weld._git` (for HEAD-SHA stamping), :mod:`weld.contract`,
:mod:`weld.workspace`, :mod:`weld.workspace_state`, and
:mod:`weld.serializer` so the discover branch can stay thin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from weld._git import get_git_sha
from weld.contract import SCHEMA_VERSION
from weld.serializer import canonical_graph as _canonical_graph
from weld.workspace import ChildEntry, WorkspaceConfig
from weld.workspace_state import WorkspaceState

__all__ = [
    "META_SCHEMA_VERSION",
    "ROOT_SOURCE_STRATEGY",
    "build_root_meta_graph",
]

#: Schema version stamped into the root meta-graph's ``meta`` block. Matches
#: ``weld.graph.ROOT_FEDERATED_SCHEMA_VERSION`` and ADR 0012 section 4.
META_SCHEMA_VERSION: int = 2

#: ``source_strategy`` label recorded on every emitted ``repo:*`` node so
#: diagnostics can trace the node back to the root federation branch rather
#: than a user-authored strategy plugin.
ROOT_SOURCE_STRATEGY: str = "federation_root"


def _split_segments(rel_path: str) -> list[str]:
    """Return the POSIX-normalised, non-empty segments of ``rel_path``.

    The workspace validator already forbids absolute paths and ``..``
    segments, so this helper can trust the input to be relative. Windows
    separators are normalised for robustness against hand-written YAML
    that sneaks a backslash in.
    """
    normalised = rel_path.replace("\\", "/")
    return [part for part in normalised.split("/") if part and part != "."]


def _build_repo_node(child: ChildEntry) -> tuple[str, dict]:
    """Build a single ``repo:<name>`` node entry for *child*.

    Returns a ``(node_id, node_body)`` pair. The body obeys the graph
    contract (``type``, ``label``, ``props``) with props:

    * ``path`` -- workspace-relative POSIX path (as declared).
    * ``path_segments`` -- list form of the path, for downstream matchers
      that prefer list comparisons over string manipulation.
    * ``depth`` -- number of path segments (children directly under the
      root have ``depth == 1``).
    * ``tags`` -- the auto-filled or user-supplied tag dict, copied so
      callers cannot mutate the registry.
    * ``source_strategy``/``authority``/``confidence`` -- canonical
      provenance metadata. Repo nodes are authoritative manual
      registrations in ``workspaces.yaml``, so ``authority=canonical``
      and ``confidence=definite`` are the correct choices.
    * ``remote`` -- surfaced only when the registry declared one; omitted
      otherwise to keep the graph minimal.
    """
    # Normalise the declared path to POSIX so the graph does not drift
    # between OSes. ADR 0011 section 4 mandates workspace-relative POSIX
    # paths; the validator enforces relativity upstream.
    rel_path = child.path.replace("\\", "/")
    segments = _split_segments(rel_path)

    props: dict = {
        "path": rel_path,
        "path_segments": list(segments),
        "depth": len(segments),
        # Copy so the caller's ChildEntry.tags is never aliased into the
        # emitted graph; downstream serializers assume props are self-owned.
        "tags": dict(child.tags),
        "source_strategy": ROOT_SOURCE_STRATEGY,
        "authority": "canonical",
        "confidence": "definite",
    }
    if child.remote:
        props["remote"] = child.remote

    node_body = {
        "type": "repo",
        "label": child.name,
        "props": props,
    }
    return f"repo:{child.name}", node_body


def _present_children(
    config: WorkspaceConfig,
    state: WorkspaceState,
) -> list[ChildEntry]:
    """Return children whose ledger status is ``present`` in *state*.

    The workspace ledger is the single source of truth for child lifecycle
    (ADR 0011 section 5). Children whose status is ``missing``,
    ``uninitialized``, or ``corrupt`` are intentionally omitted from the
    meta-graph: they still appear in ``workspace-state.json`` and in
    ``wd workspace status`` output, but emitting ``repo:*`` nodes for
    them would advertise edges we cannot resolve.
    """
    present: list[ChildEntry] = []
    for child in config.children:
        entry = state.children.get(child.name)
        if entry is None:
            continue
        if entry.status == "present":
            present.append(child)
    return present


def build_root_meta_graph(
    root: Path | str,
    config: WorkspaceConfig,
    state: WorkspaceState,
    *,
    now: str | None = None,
) -> dict:
    """Return the canonical root meta-graph for *config* and *state*.

    Parameters
    ----------
    root:
        Workspace root. Used to stamp ``meta.git_sha`` to the current HEAD
        (via :func:`weld._git.get_git_sha`) so downstream freshness checks
        match the single-repo discover path. Non-git roots simply omit the
        field; the rest of the meta-graph is fully determined by
        ``config``/``state``.
    config:
        Validated workspace registry loaded via
        :func:`weld.workspace.load_workspaces_yaml`.
    state:
        Current workspace ledger built by
        :func:`weld.workspace_state.build_workspace_state`. Only children
        whose ``status`` is ``present`` contribute a ``repo:*`` node.
    now:
        Optional ISO-8601 UTC timestamp used for ``meta.updated_at``.
        When omitted the current UTC time is used; tests inject a fixed
        value to assert determinism of the content payload.

    The returned dict already passes through
    :func:`weld.serializer.canonical_graph`, so nodes are a dict keyed by
    ``repo:<name>`` (the JSON dumper emits them in sorted key order) and
    edges are an empty list. The caller is responsible for serialising
    with :func:`weld.serializer.dumps_graph` if on-disk bytes are
    required.
    """
    updated_at = now if now is not None else datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Iterate children lexicographically by name so the builder is
    # deterministic regardless of the declaration order in
    # ``workspaces.yaml`` (ADR 0011 section 12 rule 2).
    ordered = sorted(_present_children(config, state), key=lambda c: c.name)

    nodes: dict[str, dict] = {}
    for child in ordered:
        node_id, body = _build_repo_node(child)
        nodes[node_id] = body

    meta: dict = {
        "version": SCHEMA_VERSION,
        "updated_at": updated_at,
        # ``discovered_from`` lists the files scanned during discovery.
        # A federation root does not scan children; the workspace registry
        # itself is the sole input, so we record it as the single entry.
        # This keeps ``graph.json`` diffable even when the meta-graph is
        # empty (e.g. all children are missing).
        "discovered_from": [".weld/workspaces.yaml"],
        # Root meta-graphs always stamp ``schema_version = 2`` so old
        # readers refuse them cleanly (ADR 0012 section 4). The graph
        # writer re-stamps this based on content on save; stamping it
        # here keeps the return value self-describing for callers that
        # skip the Graph abstraction and emit via ``dumps_graph`` directly.
        "schema_version": META_SCHEMA_VERSION,
    }

    # Stamp ``meta.git_sha`` to the workspace root's current HEAD so the
    # federated path matches the single-repo discover behaviour
    # (weld/discover.py:177-179) and ``compute_stale_info`` /
    # ``wd prime`` can compare against HEAD. Non-git roots return
    # ``None`` and the field is omitted -- ``compute_stale_info``
    # already handles non-git roots via ``is_git_repo`` (bd-1776099136-5038-tqe2).
    sha = get_git_sha(Path(root))
    if sha is not None:
        meta["git_sha"] = sha

    return _canonical_graph({"meta": meta, "nodes": nodes, "edges": []})
