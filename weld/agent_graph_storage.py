"""Persistent storage for the static Weld Agent Graph.

The Agent Graph lives beside the existing code graph at
``.weld/agent-graph.json``. This module owns only deterministic persistence
and metadata stamping; platform discovery and relationship extraction are
added by later Agent Graph tasks.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weld._git import get_git_sha
from weld.serializer import canonical_graph, dumps_graph
from weld.workspace_state import atomic_write_text

AGENT_GRAPH_FILENAME = "agent-graph.json"
AGENT_GRAPH_VERSION = 1

__all__ = [
    "AGENT_GRAPH_FILENAME",
    "AGENT_GRAPH_VERSION",
    "AgentGraphNotFoundError",
    "agent_graph_path",
    "build_agent_graph",
    "load_agent_graph",
    "write_agent_graph",
]


class AgentGraphNotFoundError(FileNotFoundError):
    """Raised when ``.weld/agent-graph.json`` has not been discovered yet."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def agent_graph_path(root: Path) -> Path:
    """Return the canonical Agent Graph path for *root*."""
    return root / ".weld" / AGENT_GRAPH_FILENAME


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_hashes(root: Path, discovered_from: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel_path in sorted(dict.fromkeys(discovered_from)):
        candidate = root / rel_path
        if candidate.is_file():
            hashes[rel_path] = _hash_file(candidate)
    return hashes


def build_agent_graph(
    *,
    root: Path,
    nodes: dict[str, dict],
    edges: list[dict],
    discovered_from: list[str],
    diagnostics: list[dict] | None = None,
    source_hashes: dict[str, str] | None = None,
    git_sha: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Build a canonical Agent Graph payload without writing it.

    ``source_hashes`` defaults to SHA-256 hashes for existing files listed in
    ``discovered_from``. Missing files are left for diagnostics producers; the
    storage layer stays side-effect free and deterministic.
    """
    unique_sources = sorted(dict.fromkeys(discovered_from))
    meta: dict[str, Any] = {
        "version": AGENT_GRAPH_VERSION,
        "updated_at": updated_at or _now(),
        "discovered_from": unique_sources,
        "source_hashes": copy.deepcopy(source_hashes)
        if source_hashes is not None
        else _source_hashes(root, unique_sources),
        "diagnostics": copy.deepcopy(diagnostics or []),
    }
    resolved_sha = git_sha if git_sha is not None else get_git_sha(root)
    if resolved_sha is not None:
        meta["git_sha"] = resolved_sha
    return canonical_graph({
        "meta": meta,
        "nodes": copy.deepcopy(nodes),
        "edges": copy.deepcopy(edges),
    })


def write_agent_graph(root: Path, graph: dict[str, Any]) -> Path:
    """Atomically write *graph* to ``.weld/agent-graph.json``."""
    path = agent_graph_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, dumps_graph(graph))
    return path


def load_agent_graph(root: Path) -> dict[str, Any]:
    """Load ``.weld/agent-graph.json`` or raise a clear missing-graph error."""
    path = agent_graph_path(root)
    if not path.is_file():
        raise AgentGraphNotFoundError(
            f"Agent Graph not found at {path}. "
            "Run `wd agents discover` to create .weld/agent-graph.json."
        )
    return canonical_graph(json.loads(path.read_text(encoding="utf-8")))
