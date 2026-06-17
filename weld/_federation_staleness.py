"""Federated child-staleness oracle (ADR 0066 part 1).

A polyrepo root (ADR 0011) is blind to child drift: the workspace ledger
records each child's ``head_sha`` and ``graph_sha256`` but never compares
them against the child's live state, and ``wd workspace status`` reports
only ``present`` / ``missing`` / ``uninitialized`` / ``corrupt``.

:func:`child_stale_info` is the single source of truth for child freshness.
It is consumed both by surfacing (``wd workspace status`` derived ``stale``
state and root ``wd stale`` aggregation -- ADR 0066 part 2) and, in a
follow-up, by the auto-recurse refresh selector (ADR 0066 part 3 / issue
00p8.3). Federated child freshness is the **single-repo** oracle (ADR 0017,
:func:`weld._staleness.compute_stale_info`) run per child, plus a thin
"ledger digest moved" check -- not a second freshness model.

Two design constraints from ADR 0066 are load-bearing here:

* The discovered-from SHA is read through the ADR 0065 sidecar seam
  :func:`weld._graph_meta_sidecar.load_graph_meta`, **not** child graph
  ``meta``. A child whose gitignored ``graph-meta.json`` was never fetched
  (fresh clone / CI artifact) yields no ``git_sha``; ``compute_stale_info``
  already treats that as ``source_stale`` -- the correct conservative
  result. Reading in-graph ``meta`` directly would silently see no SHA.
* The oracle is **read-only and failure-isolated**: any exception probing
  one child yields ``state="unknown"`` for that child and never raises into
  the caller. One unreadable child must not blind the root to the others.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from weld._graph_meta_sidecar import load_graph_meta
from weld._staleness import compute_stale_info
from weld._workspace_inspect import resolve_child_root
from weld.workspace_state import WorkspaceChildState

__all__ = [
    "child_stale_info",
    "freshness_by_name",
    "all_children_info",
    "augment_status_json",
    "child_status_token",
    "aggregate_root_stale",
    "stale_payload",
]

# Stored lifecycle states that already carry their own meaning and must
# never be reported as ``stale`` (ADR 0066 part 1 rule 1 / ADR 0011 §6).
_LIFECYCLE_SHORT_CIRCUIT: frozenset[str] = frozenset(
    {"missing", "uninitialized", "corrupt"},
)


def _graph_sha256(graph_path: Path) -> str | None:
    """Return the SHA-256 of *graph_path*'s bytes, or ``None`` if unreadable.

    Mirrors :func:`weld._workspace_inspect._graph_status` so the digest the
    oracle compares is computed identically to the one persisted in the
    ledger's ``graph_sha256`` field.
    """
    try:
        return hashlib.sha256(graph_path.read_bytes()).hexdigest()
    except OSError:
        return None


def _present_child_info(
    child_root: Path,
    child: WorkspaceChildState,
) -> dict:
    """Run the source + digest tiers for a ``present`` child."""
    graph_path = child_root / ".weld" / "graph.json"
    meta = load_graph_meta(graph_path)
    info = compute_stale_info(graph_path, meta)

    # Non-git child: ADR 0017 non-git path -- never stale.
    if info.get("reason") == "not a git repo":
        return {
            "state": "fresh",
            "stale": False,
            "reason": "not a git repo",
            "head_sha": None,
            "graph_sha": info.get("graph_sha"),
            "commits_behind": info.get("commits_behind", 0),
        }

    graph_sha = info.get("graph_sha")
    head_sha = info.get("current_sha")
    # compute_stale_info uses commits_behind == -1 as the "no discovered-from
    # SHA" sentinel. That is meaningless as a count, so normalise it to 0 for
    # the stable oracle dict (the ADR 0066 shape is a non-negative count).
    raw_behind = info.get("commits_behind", 0)
    commits_behind = raw_behind if isinstance(raw_behind, int) and raw_behind > 0 else 0

    # Tier 1 -- source staleness (primary).
    if info.get("source_stale"):
        reason = "unknown_sha" if graph_sha is None else "source_changed"
        return {
            "state": "stale",
            "stale": True,
            "reason": reason,
            "head_sha": head_sha,
            "graph_sha": graph_sha,
            "commits_behind": commits_behind,
        }

    # Tier 2 -- ledger-digest drift (secondary, ADR 0011 §5). Only report
    # drift when the ledger recorded a digest and it differs from the
    # current bytes. A null recorded digest cannot prove drift, so we never
    # invent a false "stale" from its absence (ADR 0066 §5: digest drift may
    # only over-report, never false-fresh -- here we stay conservative the
    # other way and only flag a *known* mismatch).
    recorded = child.graph_sha256
    current = _graph_sha256(graph_path)
    if recorded is not None and current is not None and recorded != current:
        return {
            "state": "stale",
            "stale": True,
            "reason": "graph_drift",
            "head_sha": head_sha,
            "graph_sha": graph_sha,
            "commits_behind": commits_behind,
        }

    return {
        "state": "fresh",
        "stale": False,
        "reason": "fresh",
        "head_sha": head_sha,
        "graph_sha": graph_sha,
        "commits_behind": commits_behind,
    }


def child_stale_info(root: Path | str, child: WorkspaceChildState) -> dict:
    """Return the staleness dict for one registered child (ADR 0066 part 1).

    The returned shape is stable and consumed by status/stale surfacing and
    the refresh selector::

        {"state": "stale", "stale": True, "reason": "source_changed",
         "head_sha": <child HEAD>, "graph_sha": <discovered-from SHA|None>,
         "commits_behind": <int>}

    ``reason`` is one of ``fresh``, ``source_changed``, ``graph_drift``,
    ``unknown_sha`` (no discovered-from SHA), ``not a git repo``, a
    passthrough lifecycle status (``missing`` / ``uninitialized`` /
    ``corrupt``), or ``unknown`` (probe error). Rules apply in order:

    1. **Lifecycle short-circuit.** ``missing`` / ``uninitialized`` /
       ``corrupt`` children are never ``stale``; the oracle passes the
       stored status straight through.
    2. **Source staleness (primary).** Read the discovered-from SHA through
       the ADR 0065 sidecar seam and run the single-repo oracle against the
       child's working tree.
    3. **Ledger-digest drift (secondary).** A ``present`` child whose
       ``graph.json`` bytes changed since the ledger recorded them is stale
       with ``reason="graph_drift"``.

    Read-only and failure-isolated: any exception yields ``state="unknown"``
    and never propagates.
    """
    # Rule 1 -- lifecycle short-circuit. These states are not "stale"; a
    # missing clone is absent, not behind.
    if child.status in _LIFECYCLE_SHORT_CIRCUIT:
        return {
            "state": child.status,
            "stale": False,
            "reason": child.status,
            "head_sha": child.head_sha,
            "graph_sha": None,
            "commits_behind": 0,
        }

    try:
        child_root = resolve_child_root(Path(root), _child_rel_path(child))
        return _present_child_info(child_root, child)
    except Exception as exc:  # noqa: BLE001 -- failure isolation is the contract
        return {
            "state": "unknown",
            "stale": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "head_sha": child.head_sha,
            "graph_sha": None,
            "commits_behind": 0,
        }


def _child_rel_path(child: WorkspaceChildState) -> str:
    """Recover the child's workspace-relative path from its ledger entry.

    The ledger stores ``graph_path`` as ``<rel>/.weld/graph.json`` (POSIX);
    stripping the trailing ``.weld/graph.json`` segments yields the child
    root relative to the workspace root, which :func:`resolve_child_root`
    needs to honour the ADR 0028 linked-worktree fallback.
    """
    graph_path = Path(child.graph_path)
    # graph_path == <rel>/.weld/graph.json -> parents[1] == <rel>
    parents = graph_path.parents
    if len(parents) >= 2:
        return parents[1].as_posix()
    return "."


# ---------------------------------------------------------------------------
# Part 2 -- surfacing: workspace status 'stale' + root wd stale aggregation.
#
# ``stale`` is a *derived* view (ADR 0066 §2): the four stored lifecycle
# states in workspace-state.json are unchanged; staleness is computed at
# render time by running the oracle over each ``present`` child. These
# helpers keep that logic out of weld/workspace_state.py (line cap).
# ---------------------------------------------------------------------------

def _child_from_dict(entry: dict) -> WorkspaceChildState:
    """Reconstruct the oracle's minimal child view from a ledger JSON entry."""
    return WorkspaceChildState(
        status=str(entry.get("status", "unknown")),
        head_sha=entry.get("head_sha"),
        head_ref=entry.get("head_ref"),
        is_dirty=bool(entry.get("is_dirty")),
        graph_path=str(entry.get("graph_path", "")),
        graph_sha256=entry.get("graph_sha256"),
        last_seen_utc=str(entry.get("last_seen_utc", "")),
    )


def freshness_by_name(root: Path | str, state: dict) -> dict[str, dict]:
    """Run the oracle over every ``present`` child in a loaded ledger.

    Returns ``{child_name: oracle_dict}`` only for children whose stored
    status is ``present`` -- the only states the derived ``stale`` view
    applies to. ``missing`` / ``uninitialized`` / ``corrupt`` keep their
    stored status and are intentionally absent from the map (callers fall
    back to the stored status for those).
    """
    children = state.get("children")
    if not isinstance(children, dict):
        return {}
    out: dict[str, dict] = {}
    for name in sorted(children):
        entry = children[name]
        if isinstance(entry, dict) and entry.get("status") == "present":
            out[name] = child_stale_info(root, _child_from_dict(entry))
    return out


def all_children_info(root: Path | str, state: dict) -> dict[str, dict]:
    """Run the oracle over **every** registered child in a loaded ledger.

    Unlike :func:`freshness_by_name` (present-only, for the derived status
    view), this includes ``missing`` / ``uninitialized`` / ``corrupt``
    children -- the oracle short-circuits them to their lifecycle state with
    ``stale=false``. Used by root ``wd stale`` aggregation so its
    ``children`` array reports every child (AC: lifecycle states still
    surface and degrade gracefully).
    """
    children = state.get("children")
    if not isinstance(children, dict):
        return {}
    return {
        name: child_stale_info(root, _child_from_dict(children[name]))
        for name in sorted(children)
        if isinstance(children[name], dict)
    }


def child_status_token(stored_status: str, info: dict | None) -> str:
    """Return the *display* status for one child (ADR 0066 §2).

    A ``present`` child the oracle reports ``stale`` renders as ``stale``;
    every other case shows the stored lifecycle status.
    """
    if info is not None and info.get("stale"):
        return "stale"
    return stored_status


def augment_status_json(root: Path | str, state: dict) -> dict:
    """Return *state* with a ``freshness`` object added to each present child.

    Pure with respect to *state*: a deep-ish copy is built so the stored
    ledger payload is never mutated. ``present`` children gain
    ``freshness`` (the oracle dict, carrying ``state`` / ``reason`` /
    ``commits_behind``); the stored ``status`` field is left intact so the
    four stored states stay authoritative. Non-present children are copied
    through unchanged.
    """
    fresh = freshness_by_name(root, state)
    children = state.get("children")
    if not isinstance(children, dict):
        return dict(state)
    new_children: dict[str, object] = {}
    for name, entry in children.items():
        if isinstance(entry, dict) and name in fresh:
            merged = dict(entry)
            merged["freshness"] = fresh[name]
            new_children[name] = merged
        else:
            new_children[name] = entry
    result = dict(state)
    result["children"] = new_children
    return result


def aggregate_root_stale(root: Path | str, root_info: dict, state: dict) -> dict:
    """Fold per-child staleness into the root ``wd stale`` payload (ADR 0066 §2).

    *root_info* is the root meta-graph's own :func:`compute_stale_info`
    result; *state* is the loaded ledger. Returns a new payload that
    preserves the root's own ``source_stale`` / ``sha_behind`` under
    explicit ``root_*`` keys, adds a ``children`` array (every registered
    child, with ``missing`` / ``uninitialized`` / ``corrupt`` reported by
    their lifecycle state and never ``stale``), and sets top-level
    ``stale = root_stale OR any(child.stale)`` so the agent gate fires on
    child drift. Children that error during probing surface as
    ``state="unknown"`` and are never counted ``stale`` (failure isolation).
    """
    infos = all_children_info(root, state)
    children_payload = [
        {
            "name": name,
            "state": info.get("state"),
            "reason": info.get("reason"),
            "commits_behind": info.get("commits_behind", 0),
        }
        for name, info in sorted(infos.items())
    ]
    any_child_stale = any(info.get("stale") for info in infos.values())
    root_stale = bool(root_info.get("source_stale") or root_info.get("sha_behind"))
    payload = dict(root_info)
    # Preserve the root's own signals under explicit keys so the federated
    # top-level ``stale`` does not conflate root drift with child drift.
    payload["root_source_stale"] = root_info.get("source_stale", False)
    payload["root_sha_behind"] = root_info.get("sha_behind", False)
    payload["children"] = children_payload
    payload["stale"] = root_stale or any_child_stale
    return payload


def stale_payload(root: Path | str, root_info: dict) -> dict:
    """Return the ``wd stale`` payload, federated-aware (ADR 0066 §2).

    *root_info* is the root graph's own :func:`compute_stale_info` result
    (i.e. ``Graph.stale()``). At a **single repo** it is returned unchanged.
    At a **federated root** (``workspaces.yaml`` present) the child oracle
    is folded in via :func:`aggregate_root_stale`.

    The ledger is rebuilt live from the workspace config rather than read
    from a possibly-stale ``workspace-state.json``, so a child that just
    appeared or whose graph just changed is seen immediately. Building it is
    read-only (git + file-stat per child). Any failure rebuilding the ledger
    is isolated: the plain root payload is returned so ``wd stale`` never
    crashes on a federated root with an unreadable child registry.
    """
    from weld.workspace_state import build_workspace_state, load_workspace_config

    try:
        config = load_workspace_config(root)
    except Exception:  # noqa: BLE001 -- a broken registry must not crash stale
        return root_info
    if config is None:
        return root_info
    try:
        state = build_workspace_state(root, config).to_dict()
    except Exception:  # noqa: BLE001 -- failure isolation (ADR 0066 part 1)
        return root_info
    return aggregate_root_stale(root, root_info, state)
