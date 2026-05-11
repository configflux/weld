"""Persistence layer for the ``wd review`` queue (ADR 0055).

The sidecar lives at ``<root>/.weld/review-state.json`` and stores each
human/agent triage decision keyed by stable edge id. Schema v1:

  {
    "version": 1,
    "decisions": {
      "<edge-id>": {
        "decision": "accepted" | "rejected",
        "reason": "...",
        "reviewer": "...",
        "ts": "...",
        "edge_snapshot": {...}
      }
    }
  }

The on-disk file is gitignored by default. Teams that want shared triage
can remove the entry from ``.gitignore`` and commit it.

This module is intentionally side-effect-free beyond file I/O. Atomic
writes go through :func:`weld.workspace_state.atomic_write_text` (POSIX
rename) so a crash mid-write never produces a half-written file.

Security notes:

* ``state_path`` always returns ``<root>/.weld/review-state.json``; no
  user input feeds the relative segment. The file path is therefore
  bounded inside the project's ``.weld`` directory.
* Loading the file is exception-tolerant: a corrupt JSON file falls
  back to an empty state rather than raising, so the CLI continues to
  work and the user can re-run with a clean slate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weld.workspace_state import atomic_write_text

REVIEW_STATE_VERSION = 1
REVIEW_STATE_FILENAME = "review-state.json"


@dataclass
class Decision:
    """One triage decision, keyed by stable edge id."""

    decision: str  # "accepted" | "rejected"
    reason: str
    reviewer: str
    ts: str
    edge_snapshot: dict
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "decision": self.decision,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "ts": self.ts,
            "edge_snapshot": self.edge_snapshot,
        }
        if self.stale:
            out["stale"] = True
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Decision":
        return cls(
            decision=str(raw.get("decision", "")),
            reason=str(raw.get("reason", "")),
            reviewer=str(raw.get("reviewer", "")),
            ts=str(raw.get("ts", "")),
            edge_snapshot=raw.get("edge_snapshot") or {},
            stale=bool(raw.get("stale", False)),
        )


@dataclass
class ReviewState:
    """In-memory view of ``.weld/review-state.json`` (schema v1)."""

    version: int = REVIEW_STATE_VERSION
    decisions: dict[str, Decision] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "decisions": {k: v.to_dict() for k, v in self.decisions.items()},
        }


def state_path(root: Path | str) -> Path:
    """Return ``<root>/.weld/review-state.json`` (anchored inside ``.weld``).

    The relative path under ``root`` is hard-coded, so callers cannot
    redirect the writer to an arbitrary location by passing a crafted
    edge id or reviewer string.
    """
    return Path(root) / ".weld" / REVIEW_STATE_FILENAME


def load_state(root: Path | str) -> ReviewState:
    """Return the parsed state at *root*, or an empty state on miss/corrupt.

    A missing file is the first-run state and returns ``ReviewState()``.
    A corrupt JSON file is also treated as empty -- the CLI continues
    so the user can re-run with a clean slate (the alternative, a hard
    raise, would leave the user blocked).
    """
    path = state_path(root)
    if not path.exists():
        return ReviewState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ReviewState()
    if not isinstance(raw, dict):
        return ReviewState()
    decisions_raw = raw.get("decisions") or {}
    decisions: dict[str, Decision] = {}
    if isinstance(decisions_raw, dict):
        for eid, dec in decisions_raw.items():
            if isinstance(dec, dict):
                decisions[str(eid)] = Decision.from_dict(dec)
    return ReviewState(
        version=int(raw.get("version", REVIEW_STATE_VERSION)),
        decisions=decisions,
    )


def save_state(root: Path | str, state: ReviewState) -> None:
    """Write *state* to ``<root>/.weld/review-state.json`` atomically.

    Uses :func:`weld.workspace_state.atomic_write_text` for the same
    POSIX-rename guarantee every other ``.weld/*`` sidecar relies on.
    """
    path = state_path(root)
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(path, payload)


__all__ = [
    "REVIEW_STATE_VERSION",
    "REVIEW_STATE_FILENAME",
    "Decision",
    "ReviewState",
    "load_state",
    "save_state",
    "state_path",
]
