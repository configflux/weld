"""Helper types and pure functions for federated workspace graph access."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from weld.graph import CHILD_SCHEMA_VERSION, Graph, SchemaVersionError
from weld.workspace import UNIT_SEPARATOR

DISPLAY_SEPARATOR = "::"

#: Default maximum number of parsed child graphs kept in memory.
DEFAULT_CACHE_MAXSIZE: int = 32


@dataclass(frozen=True)
class MissingChild:
    name: str
    path: str
    graph_path: str
    remote: str | None = None
    status: str = "missing"
    error: str | None = None


@dataclass(frozen=True)
class UninitializedChild:
    name: str
    path: str
    graph_path: str
    remote: str | None = None
    status: str = "uninitialized"
    error: str | None = None


@dataclass(frozen=True)
class CorruptChild:
    name: str
    path: str
    graph_path: str
    error: str
    remote: str | None = None
    status: str = "corrupt"


LoadedChild: TypeAlias = Graph | MissingChild | UninitializedChild | CorruptChild


def prefix_node_id(child_name: str, node_id: str) -> str:
    """Return the canonical federated ID for a child-local node ID."""
    return f"{child_name}{UNIT_SEPARATOR}{node_id}"


def split_prefixed_id(node_id: str) -> tuple[str, str] | None:
    """Split a canonical federated ID into ``(child_name, original_id)``."""
    if UNIT_SEPARATOR not in node_id:
        return None
    return node_id.split(UNIT_SEPARATOR, 1)


def render_display_id(node_id: str) -> str:
    """Render a human-friendly form of a federated ID for CLI JSON output."""
    parts = split_prefixed_id(node_id)
    if parts is None:
        return node_id
    child_name, original_id = parts
    return f"{child_name}{DISPLAY_SEPARATOR}{original_id}"


def load_graph_bytes(
    raw: bytes,
    *,
    graph_path: Path,
    max_supported_schema_version: int = CHILD_SCHEMA_VERSION,
) -> dict:
    """Validate a raw ``graph.json`` byte snapshot without re-reading the file."""
    decoded = raw.decode("utf-8")
    data = json.loads(decoded)
    if not isinstance(data, dict):
        raise ValueError("top-level graph payload must be a JSON object")
    meta = data.get("meta") or {}
    observed = meta.get("schema_version", CHILD_SCHEMA_VERSION)
    if not isinstance(observed, int):
        raise SchemaVersionError(
            f"graph.json at {graph_path} has non-integer meta.schema_version "
            f"{observed!r}; upgrade weld to read this artifact."
        )
    if observed > max_supported_schema_version:
        raise SchemaVersionError(
            f"graph.json at {graph_path} has schema_version {observed}; this "
            f"build of weld supports up to schema_version "
            f"{max_supported_schema_version}. Please upgrade weld to "
            f"read federated root graphs."
        )
    return data


class ChildGraphCache:
    """Bounded LRU cache for parsed child graph objects.

    Entries are keyed by ``(name, sha256_hex)`` so a graph whose content
    changed on disk (different sha256) is treated as a cache miss and
    re-parsed. The cache evicts the least-recently-used entry when
    ``maxsize`` is exceeded.

    This is intentionally *not* ``functools.lru_cache`` because:
    - entries are keyed by a composite ``(name, sha256)`` pair,
    - invalidation on sha256 mismatch must be explicit (the caller may
      pass a *different* sha256 for the same name when the file changed),
    - we need ``clear()`` and ``len()`` for tests and diagnostics.
    """

    def __init__(self, maxsize: int = DEFAULT_CACHE_MAXSIZE) -> None:
        self._maxsize = max(1, maxsize)
        # OrderedDict gives O(1) move-to-end for LRU refresh.
        self._store: OrderedDict[str, tuple[str, Any]] = OrderedDict()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def get(self, name: str, sha256_hex: str) -> Any | None:
        """Return the cached value if *name* is present with matching sha256.

        A sha256 mismatch is treated as a miss (stale entry); the caller
        is expected to reload from disk and ``put()`` the fresh value.
        """
        entry = self._store.get(name)
        if entry is None:
            return None
        stored_sha, value = entry
        if stored_sha != sha256_hex:
            return None
        # Refresh LRU position.
        self._store.move_to_end(name)
        return value

    def put(self, name: str, sha256_hex: str, value: Any) -> None:
        """Insert or update *name* with a new sha256 and value."""
        if name in self._store:
            del self._store[name]
        self._store[name] = (sha256_hex, value)
        # Evict oldest if over capacity.
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        """Drop all cached entries."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


def edge_key(edge: dict) -> str:
    """Return a deterministic sort/dedup key for an edge payload."""
    props = json.dumps(edge.get("props", {}), sort_keys=True, ensure_ascii=False)
    return "|".join((str(edge["from"]), str(edge["to"]), str(edge["type"]), props))


def sorted_edges(edges: list[dict] | tuple[dict, ...] | object) -> list[dict]:
    """Return edges in deterministic order."""
    return sorted(list(edges), key=edge_key)
