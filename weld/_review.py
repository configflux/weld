"""Core review-queue logic (ADR 0055).

The review queue triages ``speculative`` edges. The Python surface here
is intentionally low-level so the CLI, the MCP tool, and the discover
post-process step share one source of truth.

Surface:

* :func:`mint_edge_id` -- stable 16-hex id from ``(from, to, type,
  source_strategy)``.
* :func:`list_pending` / :func:`show_edge` -- read operations.
* :func:`accept_edge` / :func:`reject_edge` / :func:`reset_decision` --
  state mutations. ``accept`` also promotes the graph edge in place.
* :func:`status_summary` -- counts plus stale-decision detection.
* :func:`detect_ghost_emit` -- warns when a strategy keeps re-emitting
  a rejected edge.
* :func:`apply_review_state` -- re-discovery contract: filters rejected
  edges and promotes accepted ones; called from
  :func:`weld._discover_postprocess.post_process`.
* :func:`current_reviewer` -- derives reviewer identity from
  git ``user.email`` or falls back to ``agent:<host>``.
"""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weld._review_state import (
    Decision,
    load_state,
    save_state,
)
from weld.contract import CONFIDENCE_VALUES
from weld.graph import Graph

_SPECULATIVE = "speculative"
_DEFINITE = "definite"


def mint_edge_id(edge: dict) -> str:
    """Return the stable 16-hex id for *edge* (ADR 0055).

    The id is derived from ``sha1(from + "\x00" + to + "\x00" + type +
    "\x00" + props.source_strategy)``; the first 16 hex chars are
    enough collision space for the review queue while keeping the id
    short enough to type. Missing ``source_strategy`` collapses to the
    empty string so edges without provenance still get a deterministic
    id (two such edges with the same key collide -- review-state will
    record one decision that covers both).
    """
    props = edge.get("props") if isinstance(edge.get("props"), dict) else {}
    source = str(props.get("source_strategy") or "")
    key = (
        f"{edge.get('from', '')}"
        f"\x00{edge.get('to', '')}"
        f"\x00{edge.get('type', '')}"
        f"\x00{source}"
    ).encode("utf-8")
    return hashlib.sha1(key, usedforsecurity=False).hexdigest()[:16]


def current_reviewer() -> str:
    """Return the reviewer identity for new decisions.

    Order of resolution:

    1. ``GIT_AUTHOR_EMAIL`` env var (set by some CI runners).
    2. ``git config user.email`` (preferred; matches commit author).
    3. ``agent:<hostname>`` fallback for non-git contexts.
    """
    env_email = os.environ.get("GIT_AUTHOR_EMAIL")
    if env_email:
        return env_email.strip()
    try:
        out = subprocess.run(
            ["git", "config", "user.email"],
            check=False, capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return f"agent:{socket.gethostname()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _edge_summary(edge: dict, eid: str) -> dict[str, Any]:
    """Return the public render of an edge for list/show responses."""
    props = edge.get("props") or {}
    return {
        "review_id": eid,
        "from": edge.get("from"),
        "to": edge.get("to"),
        "type": edge.get("type"),
        "confidence": props.get("confidence"),
        "source_strategy": props.get("source_strategy"),
        "provenance": props.get("provenance"),
    }


def _speculative_edges(graph: Graph) -> list[dict]:
    """Return the subset of edges with ``confidence == speculative``."""
    return [
        e for e in graph.dump().get("edges", [])
        if (e.get("props") or {}).get("confidence") == _SPECULATIVE
    ]


def list_pending(
    root: Path | str,
    *,
    limit: int | None = None,
    type_filter: str | None = None,
    source_filter: str | None = None,
) -> dict:
    """Return speculative edges that have no review-state decision yet."""
    g = Graph(Path(root))
    g.load()
    state = load_state(root)
    decided = set(state.decisions.keys())
    out: list[dict] = []
    for edge in _speculative_edges(g):
        eid = mint_edge_id(edge)
        if eid in decided:
            continue
        if type_filter and edge.get("type") != type_filter:
            continue
        if source_filter:
            ss = (edge.get("props") or {}).get("source_strategy")
            if ss != source_filter:
                continue
        out.append(_edge_summary(edge, eid))
        if limit and len(out) >= limit:
            break
    return {"edges": out, "count": len(out)}


def show_edge(root: Path | str, edge_id: str) -> dict:
    """Return one edge identified by its stable id, including provenance."""
    g = Graph(Path(root))
    g.load()
    for edge in g.dump().get("edges", []):
        if mint_edge_id(edge) == edge_id:
            return _edge_summary(edge, edge_id)
    return {"error": f"no edge found with review_id={edge_id!r}"}


def _find_edge(graph: Graph, edge_id: str) -> dict | None:
    for edge in graph.dump().get("edges", []):
        if mint_edge_id(edge) == edge_id:
            return edge
    return None


def accept_edge(
    root: Path | str,
    edge_id: str,
    *,
    reason: str = "",
    reviewer: str | None = None,
) -> dict:
    """Promote ``speculative`` -> ``definite`` and persist the decision.

    Writes the new confidence to the graph file so downstream readers
    see the promotion immediately (no second discover needed).
    """
    root_path = Path(root)
    g = Graph(root_path)
    g.load()
    edge = _find_edge(g, edge_id)
    if edge is None:
        return {"error": f"no edge found with review_id={edge_id!r}"}
    props = edge.setdefault("props", {})
    props["confidence"] = _DEFINITE
    g.save()
    snapshot = {
        "from": edge.get("from"),
        "to": edge.get("to"),
        "type": edge.get("type"),
        "props": dict(props),
    }
    state = load_state(root_path)
    state.decisions[edge_id] = Decision(
        decision="accepted",
        reason=reason,
        reviewer=reviewer or current_reviewer(),
        ts=_now(),
        edge_snapshot=snapshot,
    )
    save_state(root_path, state)
    return {
        "review_id": edge_id,
        "decision": "accepted",
        "confidence": _DEFINITE,
    }


def reject_edge(
    root: Path | str,
    edge_id: str,
    *,
    reason: str = "",
    reviewer: str | None = None,
) -> dict:
    """Record a ``rejected`` decision; graph mutation happens at next discover.

    The edge stays in the graph until the next ``wd discover`` pass, which
    will filter it out via :func:`apply_review_state`.
    """
    root_path = Path(root)
    g = Graph(root_path)
    g.load()
    edge = _find_edge(g, edge_id)
    if edge is None:
        return {"error": f"no edge found with review_id={edge_id!r}"}
    snapshot = {
        "from": edge.get("from"),
        "to": edge.get("to"),
        "type": edge.get("type"),
        "props": dict(edge.get("props") or {}),
    }
    state = load_state(root_path)
    state.decisions[edge_id] = Decision(
        decision="rejected",
        reason=reason,
        reviewer=reviewer or current_reviewer(),
        ts=_now(),
        edge_snapshot=snapshot,
    )
    save_state(root_path, state)
    return {"review_id": edge_id, "decision": "rejected"}


def reset_decision(root: Path | str, edge_id: str) -> dict:
    """Drop the decision for *edge_id*; the edge returns to pending."""
    state = load_state(root)
    state.decisions.pop(edge_id, None)
    save_state(root, state)
    return {"review_id": edge_id, "decision": "pending"}


def _snapshot_diverged(decision: Decision, edge: dict) -> bool:
    """Return True when the edge no longer matches the decision snapshot."""
    snap = decision.edge_snapshot or {}
    if not snap:
        return False
    if snap.get("from") != edge.get("from"):
        return True
    if snap.get("to") != edge.get("to"):
        return True
    if snap.get("type") != edge.get("type"):
        return True
    cur_props = edge.get("props") or {}
    snap_props = snap.get("props") or {}
    # Compare a narrow set of fields that signal a meaningful drift:
    # confidence (the very field we triaged) and provenance (the
    # rendering source). Other fields can churn for unrelated reasons.
    for key in ("provenance", "source_strategy"):
        if cur_props.get(key) != snap_props.get(key):
            return True
    return False


def status_summary(root: Path | str) -> dict:
    """Return ``{pending, accepted, rejected, stale}`` counts.

    ``stale`` is the number of decisions whose ``edge_snapshot`` no
    longer matches the current edge (e.g., a provenance update). The
    decision still applies; staleness is an advisory flag.
    """
    g = Graph(Path(root))
    g.load()
    state = load_state(root)
    edges = g.dump().get("edges", [])
    edge_by_id: dict[str, dict] = {}
    for edge in edges:
        edge_by_id[mint_edge_id(edge)] = edge
    pending = 0
    accepted = 0
    rejected = 0
    stale = 0
    for edge in edges:
        if (edge.get("props") or {}).get("confidence") != _SPECULATIVE:
            continue
        eid = mint_edge_id(edge)
        if eid not in state.decisions:
            pending += 1
    for eid, dec in state.decisions.items():
        if dec.decision == "accepted":
            accepted += 1
        elif dec.decision == "rejected":
            rejected += 1
        cur = edge_by_id.get(eid)
        if cur is not None and _snapshot_diverged(dec, cur):
            stale += 1
    return {
        "pending": pending,
        "accepted": accepted,
        "rejected": rejected,
        "stale": stale,
    }


def detect_ghost_emit(root: Path | str) -> list[dict]:
    """Return rejected edges still present in the graph (ghost re-emits).

    Per ADR 0055, a strategy that keeps re-emitting a rejected edge is
    a bug. This helper surfaces them so the user can fix the strategy
    or accept the edge with a justified reason.
    """
    g = Graph(Path(root))
    g.load()
    state = load_state(root)
    rejected = {
        eid for eid, d in state.decisions.items() if d.decision == "rejected"
    }
    ghosts: list[dict] = []
    for edge in g.dump().get("edges", []):
        eid = mint_edge_id(edge)
        if eid in rejected:
            ghosts.append(_edge_summary(edge, eid))
    return ghosts


def apply_review_state(root: Path | str, edges: list[dict]) -> list[dict]:
    """Filter rejected edges and promote accepted ones (re-discovery contract).

    Used by :func:`weld._discover_postprocess.post_process` to honor
    review decisions on every discovery run. The function returns a new
    list -- callers replace ``edges`` with this result.
    """
    state = load_state(root)
    if not state.decisions:
        return list(edges)
    out: list[dict] = []
    for edge in edges:
        eid = mint_edge_id(edge)
        dec = state.decisions.get(eid)
        if dec is None:
            out.append(edge)
            continue
        if dec.decision == "rejected":
            continue
        if dec.decision == "accepted":
            new_props = dict(edge.get("props") or {})
            if new_props.get("confidence") in CONFIDENCE_VALUES:
                new_props["confidence"] = _DEFINITE
            out.append({**edge, "props": new_props})
            continue
        out.append(edge)
    return out


__all__ = [
    "accept_edge",
    "apply_review_state",
    "current_reviewer",
    "detect_ghost_emit",
    "list_pending",
    "mint_edge_id",
    "reject_edge",
    "reset_decision",
    "show_edge",
    "status_summary",
]
