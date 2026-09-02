"""Auto-recurse stale children on root reads (ADR 0066 part 3).

ADR 0051 made single-repo read commands refresh-before-serving; ADR 0066
extends that to **federated roots**: a root read (``wd query`` / ``context``
/ ``path``) at a workspace whose child has drifted incrementally refreshes
*only the stale-or-uninitialized children* via the existing
:func:`weld._discover_recurse.recurse_children` engine, rebuilds the root
meta-graph (re-running cross-repo resolvers against the fresh child bytes),
and serves fresh cross-repo answers -- no ``cd`` into the child.

The single-repo branch of :func:`weld._auto_refresh.auto_refresh_if_stale`
returns early at federated roots; that caller now delegates here instead.
The opt-outs are identical to ADR 0051 so users keep one mental model:

* ``WELD_AUTO_REFRESH=0`` (and the off-aliases) disables it globally -- the
  CI / batch / gate-freeze contract (bd 19tw). The reported staleness signal
  (``wd workspace status`` / ``wd stale``) stays available under the opt-out;
  only the *refresh* is suppressed, so CI never silently rewrites committed
  child ``graph.json`` files.
* ``--no-refresh`` bypasses the refresh and warns, naming the stale children.
* ``--safe`` (ADR 0024) propagates into ``recurse_children`` so per-child
  discovery runs the reduced strategy set.

Two invariants carried from ADR 0066:

* **Read-only selection, locked refresh.** The oracle probe (git + file-stat
  per child) takes no lock -- a torn read at worst yields a spurious
  ``stale`` that the next pass corrects. The *write* path (recurse subset +
  ledger + meta-graph) runs inside the single existing
  :class:`weld.workspace_state.WorkspaceLock` so a concurrent root discover
  cannot interleave its ledger/meta-graph write with this one.
* **Per-child failure isolation.** One child whose discovery raises is
  recorded in ``RecurseResult.errors`` and never breaks the refresh of the
  others; the whole helper is wrapped so any unexpected failure degrades to
  "serve the existing meta-graph" rather than crashing the read.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO

from weld._auto_refresh import _env_disabled
from weld._notice import emit

__all__ = ["auto_refresh_federated_root", "select_stale_children"]


def select_stale_children(root, config, state) -> set[str]:  # noqa: ANN001
    """Return the names of children to refresh: stale ``present`` + uninitialized.

    Uses the ADR 0066 part 1 oracle (:func:`weld._federation_staleness`):

    * ``present`` children the oracle reports ``stale`` (source drift,
      unknown discovered-from SHA, or ledger-digest drift) are selected.
    * ``uninitialized`` children (a registered git child with no graph yet)
      are selected so a first root read materialises them, mirroring
      ``wd discover --recurse``'s uninitialized -> present transition.

    Fresh ``present`` children and ``missing`` / ``corrupt`` children are
    excluded -- refreshing them would either be wasted work (fresh) or
    impossible (missing/corrupt degrade gracefully, ADR 0011 §6).

    A child with **no ``.weld/discover.yaml``** is also excluded: there is
    nothing to re-derive (it was never wired for discovery), and recursing it
    would clobber a hand-seeded graph with an empty one. This mirrors the
    single-repo discover.yaml guard in
    :func:`weld._auto_refresh.auto_refresh_if_stale` and protects synthetic
    federated fixtures (children with a hand-written ``graph.json`` and no
    discover config) from being wiped by a read.

    Selection is read-only and failure-isolated at the per-child level (the
    oracle never raises into here); an unreadable child surfaces as
    ``state="unknown"``/``stale=False`` and is simply not selected.
    """
    from weld._federation_staleness import freshness_by_name

    selected: set[str] = set()

    # Stale present children (the oracle only runs over present children).
    state_dict = state.to_dict() if hasattr(state, "to_dict") else state
    for name, info in freshness_by_name(root, state_dict).items():
        if info.get("stale"):
            selected.add(name)

    # Uninitialized children: registered git child, no graph yet. The oracle
    # short-circuits these to stale=False (they are not "behind"), so select
    # them explicitly from the ledger's stored lifecycle status.
    children = state_dict.get("children")
    if isinstance(children, dict):
        for name, entry in children.items():
            if isinstance(entry, dict) and entry.get("status") == "uninitialized":
                selected.add(name)

    return {n for n in selected if _child_has_discover_config(root, config, n)}


def _child_has_discover_config(root, config, name: str) -> bool:  # noqa: ANN001
    """True iff the named child has a ``.weld/discover.yaml`` to re-derive from.

    Resolves the child root via :func:`resolve_child_root` (ADR 0028 linked-
    worktree fallback) so the guard agrees with where ``recurse_children``
    will actually write. Failure-isolated: any error resolving / probing a
    child is treated as "no config" so a broken child is skipped, never
    refreshed against a missing path.
    """
    from weld._workspace_inspect import resolve_child_root

    try:
        child = next(
            (c for c in config.children if c.name == name), None,
        )
        if child is None:
            return False
        child_root = resolve_child_root(Path(root), child.path)
        return (child_root / ".weld" / "discover.yaml").is_file()
    except Exception:  # noqa: BLE001 -- failure isolation (ADR 0066)
        return False


def _emit_no_refresh_warning(stderr: IO[str], stale_names: set[str]) -> None:
    """Warn that stale children exist but refresh is suppressed (``--no-refresh``).

    Names the stale children (bounded, sorted) so the operator knows exactly
    which child graphs may not reflect their source -- the federated
    extension of the ADR 0051 single-repo no-refresh warning.
    """
    names = ", ".join(sorted(stale_names))
    emit(
        "[weld] warning: stale federated children "
        f"({names}); --no-refresh in effect, cross-repo answers may not "
        "reflect current child source",
        stream=stderr,
    )


def auto_refresh_federated_root(
    root: Path,
    *,
    no_refresh: bool = False,
    safe: bool = False,
    json_output: bool = False,
    env: Mapping[str, str] | None = None,
    stderr: IO[str] | None = None,
) -> dict | None:
    """Refresh stale children before a federated root read (ADR 0066 part 3).

    Returns a dict ``{"refreshed_children", "errors"}`` when a refresh ran,
    or ``None`` when refresh was skipped: env opt-out, ``--no-refresh``, not a
    federated root, or nothing stale (the common steady state -- cheap, no
    lock).

    The whole body is failure-isolated: a federated read must keep serving
    even if the refresh path itself breaks. The fallback is the existing
    meta-graph; the user's next explicit ``wd discover`` surfaces the cause.
    """
    env_map = env if env is not None else os.environ
    err = stderr if stderr is not None else sys.stderr

    # CI / gate-freeze contract (bd 19tw): identical opt-out to ADR 0051.
    if _env_disabled(env_map):
        return None

    try:
        return _refresh_federated_root(
            root, no_refresh=no_refresh, safe=safe,
            json_output=json_output, stderr=err,
        )
    except Exception:  # noqa: BLE001 -- read must keep serving (ADR 0066)
        return None


def _refresh_federated_root(
    root: Path,
    *,
    no_refresh: bool,
    safe: bool,
    json_output: bool,
    stderr: IO[str],
) -> dict | None:
    """Selection + locked refresh for one federated root (see caller)."""
    from weld.workspace_state import (
        build_workspace_state,
        load_workspace_config,
    )

    config = load_workspace_config(root)
    if config is None:
        return None  # not a federated root (or unreadable registry)

    # Read-only selection (no lock): the oracle is git + file-stat per child.
    state = build_workspace_state(root, config)
    stale_names = select_stale_children(root, config, state)
    if not stale_names:
        return None  # steady state -- serve the existing meta-graph

    if no_refresh:
        _emit_no_refresh_warning(stderr, stale_names)
        return None

    return _locked_refresh(
        root, config, stale_names, safe=safe, json_output=json_output,
        stderr=stderr,
    )


def _locked_refresh(
    root: Path,
    config,  # noqa: ANN001 -- WorkspaceConfig
    stale_names: set[str],
    *,
    safe: bool,
    json_output: bool,
    stderr: IO[str],
) -> dict | None:
    """Recurse the stale subset and rebuild ledger + meta-graph under the lock.

    Mirrors the federated write block in :func:`weld.discover.discover`
    (build ledger -> recurse -> retag origins -> build meta-graph -> merge
    cross-repo edges -> paired ADR 0065 write -> save ledger), but
    ``recurse_children`` is restricted to *stale_names* so only the stale
    subset is re-discovered (proportional refresh, ADR 0066 part 3 step 4).

    The whole sequence runs inside one :class:`WorkspaceLock`; a second
    concurrent root mutation blocks on the same PID lockfile. A child whose
    discovery raises is captured in ``RecurseResult.errors`` and skipped --
    its stale graph is left in place and it is marked stale for the next pass.
    """
    from weld._discover_empty_guard import enforce_nonempty_federated_write
    from weld._discover_federate import (
        merge_cross_repo_edges,
        retag_federated_origins_on_disk,
    )
    from weld._discover_sidecar import persist_sqlite_sidecar
    from weld._federation_basis import publish_root_graph
    from weld._discover_recurse import recurse_children
    from weld.federation_root import build_root_meta_graph
    from weld.workspace_state import (
        WorkspaceLock,
        build_workspace_state,
        save_workspace_state,
    )

    with WorkspaceLock(root):
        # Rebuild the ledger inside the lock so the recurse sees a state that
        # cannot have been mutated by a concurrent discover between selection
        # and refresh.
        state = build_workspace_state(root, config)
        recurse_result = recurse_children(
            root, config, state, incremental=None, safe=safe,
            names=stale_names,
        )
        # Re-inspect after child graphs were rewritten so the meta-graph and
        # cross-repo resolvers read the fresh child bytes.
        state = build_workspace_state(root, config)
        retag_federated_origins_on_disk(root, config, state)
        graph = build_root_meta_graph(root, config, state)
        graph = merge_cross_repo_edges(root, config, state, graph)

        target = root / ".weld" / "graph.json"
        enforce_nonempty_federated_write(target, graph, state, allow_empty=False)
        # ADR 0065 paired write + the ADR 0141 D1 basis this pass read: a
        # published root graph that records nothing is the state whose next
        # read condemns a dirty ``workspaces.yaml`` it cannot name (M1).
        publish_root_graph(root, graph, target)
        persist_sqlite_sidecar(target.parent, graph)  # ADR 0058 sidecar
        save_workspace_state(root, state)

    _emit_banner(
        stderr=stderr,
        refreshed=recurse_result.discovered,
        errors=recurse_result.errors,
        json_output=json_output,
        safe=safe,
    )
    return {
        "refreshed_children": list(recurse_result.discovered),
        "errors": dict(recurse_result.errors),
    }


def _emit_banner(
    *,
    stderr: IO[str],
    refreshed: list[str],
    errors: dict[str, str],
    json_output: bool,
    safe: bool,
) -> None:
    """One-line federated refresh notice unless suppressed (``--json`` / ``--safe``).

    Mirrors the ADR 0051 single-repo banner suppression: silent under
    ``--json`` (ADR 0040) and ``--safe`` (ADR 0051). When some children were
    refreshed it reports the count; when every selected child failed it still
    surfaces the failure so the read is not silently degraded.
    """
    if json_output or safe:
        return
    if refreshed:
        suffix = f"; {len(errors)} failed" if errors else ""
        emit(
            f"[weld] auto-refresh: refreshed {len(refreshed)} stale "
            f"child(ren){suffix}",
            stream=stderr,
        )
    elif errors:
        emit(
            f"[weld] auto-refresh: {len(errors)} stale child(ren) failed to "
            "refresh; serving last-known child graphs",
            stream=stderr,
        )
