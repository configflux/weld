"""Defense-in-depth guard against silent federated 0-node rewrites (ADR 0028).

The federated discover write path (ADR 0019) atomically replaces
``.weld/graph.json`` with whatever meta-graph the federation builder
produced. Under ``git worktree``-based isolation, a missing child path
fallback used to silently yield a 0-node meta-graph and clobber the
prior committed graph. This module provides the guard that refuses such
a write when the prior on-disk graph has >0 nodes, plus the
``--allow-empty`` opt-in for legitimate teardowns.

Kept in a dedicated module so :mod:`weld.discover` stays under the
repo's 400-line cap and so the guard can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

__all__ = [
    "EmptyFederatedGraphRefusedError",
    "enforce_nonempty_federated_write",
    "existing_node_count",
    "missing_child_names",
]


class EmptyFederatedGraphRefusedError(RuntimeError):
    """Raised when a federated discover would clobber a non-empty graph with 0 nodes.

    ADR 0028 §2: writing a 0-node federated meta-graph over an existing
    on-disk graph that has >0 nodes is almost always the symptom of an
    environmental problem (worktree, partial clone, renamed children) and
    not an intentional reset. The discover pipeline raises this exception
    instead of silently overwriting; ``--allow-empty`` (or
    ``allow_empty=True`` in the API) bypasses the guard.
    """


def existing_node_count(path: Path) -> int:
    """Return the node count of the JSON graph at *path*, or 0 on any read error.

    Used by the federated empty-graph guard. Returning 0 on parse/IO errors
    is intentional: when the prior graph cannot be read we have no
    evidence of a non-empty payload to protect, so the guard does not
    fire and the new graph is written normally (matching the legacy
    behaviour).

    A stderr warning surfaces unreadable prior graphs so the operator
    knows the guard is inactive for this run, even though the legacy
    silent-fallback behaviour is preserved.
    """
    if not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(
            f"[weld] warning: existing graph at {path} is unreadable "
            f"({type(exc).__name__}); empty-graph guard inactive for this run",
            file=sys.stderr,
        )
        return 0
    if not isinstance(payload, dict):
        return 0
    nodes = payload.get("nodes")
    if isinstance(nodes, dict):
        return len(nodes)
    if isinstance(nodes, list):
        return len(nodes)
    return 0


def missing_child_names(state) -> list[str]:  # noqa: ANN001 -- duck-typed WorkspaceState
    """Return the names of children whose ledger status is not ``present``."""
    out: list[str] = []
    children = getattr(state, "children", None) or {}
    for name in sorted(children):
        entry = children[name]
        status = getattr(entry, "status", None)
        if status != "present":
            out.append(name)
    return out


def enforce_nonempty_federated_write(
    target: Path,
    new_graph: dict,
    state,  # noqa: ANN001 -- duck-typed WorkspaceState
    *,
    allow_empty: bool,
) -> None:
    """Refuse to clobber a >0-node federated graph with a 0-node new graph.

    Runs immediately before the federated atomic write (ADR 0028 §2). When
    *allow_empty* is True the guard is bypassed -- the explicit opt-in for
    legitimate workspace tear-downs.
    """
    if allow_empty:
        return
    new_nodes = new_graph.get("nodes")
    new_count = (
        len(new_nodes) if isinstance(new_nodes, (dict, list)) else 0
    )
    if new_count > 0:
        return
    prior_count = existing_node_count(target)
    if prior_count == 0:
        return
    missing = missing_child_names(state)
    if missing:
        missing_summary = ", ".join(missing)
    else:
        missing_summary = "(none recorded -- check workspaces.yaml)"
    msg = (
        f"[weld] error: refusing to overwrite {target} ({prior_count} nodes) "
        f"with a 0-node federated meta-graph.\n"
        f"[weld] missing or unreachable children: {missing_summary}.\n"
        f"[weld] If this is intentional (e.g. tearing down the workspace), "
        f"re-run with --allow-empty to bypass this guard."
    )
    print(msg, file=sys.stderr)
    raise EmptyFederatedGraphRefusedError(msg)
