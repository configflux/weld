"""Helper types and pure functions for federated workspace graph access."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from weld._graph_schema import validate_dict_payload, validate_graph_shape
from weld._sqlite_reader import SqliteBackedGraph
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


@dataclass(frozen=True)
class PresentChild:
    """A child whose ``graph.json`` parses (bd sk3c).

    Returned by :func:`weld.federation_child_loader.probe_child_status`
    instead of a real :class:`Graph` / :class:`SqliteBackedGraph` -- the
    status probe only needs a parse-succeeded signal, never a queryable
    handle, so it never pays for one. Shaped like the three sentinels above
    (same field set, ``error`` always ``None``) so
    :meth:`weld.federation.FederatedGraph.children_status` can build every
    entry's payload the same way regardless of which of the four this is.
    """

    name: str
    path: str
    graph_path: str
    remote: str | None = None
    status: str = "present"
    error: str | None = None


#: ADR 0058: ``_load_child`` may now return a :class:`SqliteBackedGraph`
#: when the child's sidecar is fresh; callers that need the full
#: in-memory query state (``Graph.query``) must request the JSON path
#: explicitly via :func:`weld.federation` helpers.
ChildHandle: TypeAlias = Graph | SqliteBackedGraph
LoadedChild: TypeAlias = (
    Graph | SqliteBackedGraph | MissingChild | UninitializedChild | CorruptChild
)

#: Return type of :func:`weld.federation_child_loader.probe_child_status`
#: (bd sk3c): the four ``children_status()`` classifications, never a real
#: queryable handle -- see :class:`PresentChild`.
ChildStatusResult: TypeAlias = (
    PresentChild | MissingChild | UninitializedChild | CorruptChild
)


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
    """Validate a raw ``graph.json`` byte snapshot without re-reading the file.

    Also validates that the top level is a JSON object (see
    :func:`weld._graph_schema.validate_dict_payload`) and the minimal
    ``nodes``/``edges`` shape (see
    :func:`weld._graph_schema.validate_graph_shape`) so a syntactically
    valid but structurally wrong payload -- a bare list/scalar top level,
    or e.g. ``{"meta": {...}}`` alone -- raises a classifiable
    :class:`~weld._graph_schema.GraphShapeError` (a ``ValueError``, already
    part of every caller's caught exception tuple) here rather than
    escaping as an uncaught ``AttributeError``/``KeyError`` later, inside
    ``Graph._build_inverted_index``.
    """
    decoded = raw.decode("utf-8")
    data = json.loads(decoded)
    validate_dict_payload(data)
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
    validate_graph_shape(data)
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


def children_status_priority_key(name: str, payload: dict[str, object]) -> tuple[int, str]:
    """Total-order sort key implementing the ``children_status`` field rule.

    **The rule (bd sk3c, ADR 0082 amendment 2026-08-21): non-present states
    sort before ``present`` ones; alphabetical by child name within each
    class.** ``status`` values today are ``present`` / ``missing`` /
    ``uninitialized`` / ``corrupt``; anything that is not literally
    ``"present"`` -- including a hypothetical future state -- lands in the
    higher-priority class, so a new non-present status is surfaced by
    default rather than silently sorted last.

    Motivation: :func:`weld._read_budget.bound_dict_to_budget` drops the
    *tail* of whatever order it is handed once a workspace's
    ``children_status`` map no longer fits its reserve
    (``CHILDREN_STATUS_RESERVE_BYTES``). Pure alphabetical order (the
    pre-sk3c behavior) means a corrupt or missing child whose name happens
    to sort late is exactly as likely to be dropped as a boring ``present``
    entry -- so a workspace with enough children to hit the cap could drop
    the one entry an agent actually needed to see. Sorting the *source*
    (:meth:`weld.federation.FederatedGraph.children_status`) by this key
    means the cap's alphabetical-tail-drop naturally sheds ``present``
    entries first: every consumer of ``children_status()`` (the three MCP
    read tools via ``attach_children_status``, and
    :mod:`weld.viz.api`) gets the same order for free, from one
    implementation, rather than each needing its own priority-aware pass.

    Still a pure, deterministic total order (ADR 0012): a total order on
    ``(priority, name)`` with no wall-clock or randomness, so repeated calls
    over unchanged input are byte-identical.
    """
    return (0 if payload.get("status") != "present" else 1, name)


def order_children_status_by_priority(
    status: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Return *status* reordered by :func:`children_status_priority_key`."""
    return dict(
        sorted(status.items(), key=lambda item: children_status_priority_key(*item))
    )
