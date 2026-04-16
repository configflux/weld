"""Base classes and orchestration for cross-repo resolvers.

This module defines the plugin contract that concrete resolvers implement.
Every public symbol is re-exported from :mod:`weld.cross_repo` so callers
can write ``from weld.cross_repo import CrossRepoEdge`` rather than
reaching into the private base module.

The flow is:

1. The workspace root loads each child ``graph.json``, records the
   SHA-256 of the bytes it read, and assembles a :class:`ResolverContext`.
2. The root calls :func:`run_resolvers` with the context. The orchestrator
   iterates over ``context.cross_repo_strategies`` in YAML-declared order,
   looks each name up in the registry, instantiates the resolver, and
   collects its output edges.
3. Before committing edges, the orchestrator optionally re-hashes each
   child's bytes (passed in as ``post_run_child_hashes``). Any edge that
   references a child whose hash drifted is skipped with a warning --
   this prevents a TOCTOU race from producing stale cross-repo edges.
4. A resolver that raises is caught; the error is logged to stderr with
   the resolver's name, and the run continues with the remaining
   resolvers. This isolates a single broken resolver from bringing down
   the entire cross-repo pass.

Resolvers are pure functions of the context: they must not write to
disk, must not modify the child graphs they receive, and must produce
deterministic output for identical input so root ``graph.json`` remains
byte-identical across discover runs.
"""

from __future__ import annotations

import hashlib
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from weld.workspace import UNIT_SEPARATOR

__all__ = [
    "CrossRepoEdge",
    "CrossRepoResolver",
    "MalformedEdgeError",
    "ResolverContext",
    "UnknownResolverError",
    "get_resolver",
    "register_resolver",
    "resolver_names",
    "run_resolvers",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MalformedEdgeError(ValueError):
    """A resolver produced an edge payload that does not satisfy the contract.

    Raised by :meth:`CrossRepoEdge.from_mapping` when a dict-shaped edge is
    missing required keys or carries an invalid ``props`` type. The error
    message identifies the offending field so failures from
    third-party resolvers are actionable without reading the framework code.
    """


class UnknownResolverError(KeyError):
    """A resolver name was requested that is not in the registry.

    Raised by :func:`get_resolver` and bubbled up by :func:`run_resolvers`
    when ``cross_repo_strategies`` contains a name no resolver has
    registered. The error message lists the known names so typos surface
    with a clear next step.
    """


# ---------------------------------------------------------------------------
# Typed edge output contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossRepoEdge:
    """A single typed edge emitted by a cross-repo resolver.

    ``from_id`` and ``to_id`` are canonical federated IDs -- that is,
    ``<child-name>\\x1f<node-id>`` where the separator is the ASCII Unit
    Separator documented in ADR 0011 § 7. ``type`` is a short tag such as
    ``invokes`` or ``depends_on`` that names the relationship. ``props``
    carries resolver-specific metadata (host, port, path, matched symbol)
    and is always a plain mapping of strings to JSON-serializable values.

    The class is frozen so that edges returned by a resolver cannot be
    mutated by the orchestrator between collection and commit. Callers
    that need to serialize an edge should go through :meth:`to_dict`, which
    returns a fresh dict using the wire field names (``from`` / ``to``)
    that the root ``graph.json`` expects.
    """

    from_id: str
    to_id: str
    type: str
    props: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serializable representation of this edge."""
        return {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.type,
            "props": dict(self.props),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CrossRepoEdge":
        """Validate and construct an edge from a loose mapping.

        Used by resolvers that choose to work in dict form internally but
        want the framework to enforce the typed output contract at the
        boundary. Raises :class:`MalformedEdgeError` on any contract
        violation -- the caller must not see partial results.
        """
        required = ("from", "to", "type")
        missing = [key for key in required if key not in payload]
        if missing:
            raise MalformedEdgeError(
                f"cross-repo edge is missing required keys: {missing}",
            )
        props = payload.get("props", {})
        if props is None:
            props = {}
        if not isinstance(props, Mapping):
            raise MalformedEdgeError(
                f"cross-repo edge 'props' must be a mapping, "
                f"got {type(props).__name__}",
            )
        return cls(
            from_id=str(payload["from"]),
            to_id=str(payload["to"]),
            type=str(payload["type"]),
            props=dict(props),
        )


# ---------------------------------------------------------------------------
# Resolver context
# ---------------------------------------------------------------------------


class ResolverContext:
    """Read-only handle into the data a resolver needs to run.

    A resolver receives one context per root-discover pass. The context
    provides:

    * ``workspace_root`` -- filesystem path of the workspace root, useful
      for resolvers that need to read root-level files such as
      ``docker-compose.yaml``. Not used for reading child graphs; those
      come in pre-loaded via ``children``.
    * ``cross_repo_strategies`` -- the ordered list of strategy names
      declared in ``workspaces.yaml``. Resolvers rarely inspect this
      directly, but it is exposed so introspection tooling can observe
      the active set without re-parsing the YAML.
    * ``children`` -- a read-only mapping from child name to the loaded
      :class:`weld.graph.Graph` (or compatible stub). Only children
      whose status is ``present`` appear here; missing, uninitialized,
      and corrupt children are filtered out by the caller so resolvers
      can iterate without repeatedly checking sentinel types.
    * ``child_hashes`` -- a parallel mapping from child name to the
      SHA-256 of the bytes the caller loaded for that child. Resolvers
      use this to record the exact child byte identity they consumed;
      the orchestrator re-checks it before committing edges.

    The mapping is wrapped in :class:`types.MappingProxyType` so resolvers
    cannot mutate the shared view. Graphs themselves are not copied --
    the caller is responsible for passing in objects that are safe to
    share across resolvers.
    """

    __slots__ = (
        "workspace_root",
        "cross_repo_strategies",
        "children",
        "child_hashes",
    )

    def __init__(
        self,
        *,
        workspace_root: str,
        cross_repo_strategies: list[str],
        children: Mapping[str, Any],
        child_hashes: Mapping[str, str],
    ) -> None:
        self.workspace_root = workspace_root
        self.cross_repo_strategies = tuple(cross_repo_strategies)
        self.children = MappingProxyType(dict(children))
        self.child_hashes = MappingProxyType(dict(child_hashes))

    @staticmethod
    def hash_bytes(raw: bytes) -> str:
        """Return the SHA-256 hex digest of a byte snapshot.

        Exposed on the class so callers and resolvers use the same digest
        algorithm when recording and comparing child-graph identity.
        """
        return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Resolver ABC + registry
# ---------------------------------------------------------------------------


class CrossRepoResolver(ABC):
    """Abstract base class for cross-repo resolvers.

    Concrete resolvers subclass this, set the ``name`` class attribute to
    the unique identifier that appears in ``cross_repo_strategies``, and
    implement :meth:`resolve`. The ``name`` must match the string passed
    to the :func:`register_resolver` decorator -- this is enforced at
    registration time so a typo cannot silently shadow the wrong resolver.
    """

    name: str = ""

    @abstractmethod
    def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
        """Return the list of cross-repo edges this resolver produces.

        Implementations must not mutate ``context`` or any of the
        :class:`weld.graph.Graph` instances it exposes. Output ordering
        should be stable for identical input so root ``graph.json``
        remains byte-identical across discover runs; the caller sorts
        the aggregate edge list by a deterministic key before writing.
        """


_REGISTRY: dict[str, type[CrossRepoResolver]] = {}


def register_resolver(name: str):
    """Decorator that registers a resolver class under ``name``.

    Raises :class:`ValueError` if another resolver is already registered
    under the same name, or if the decorated class's ``name`` attribute
    disagrees with the decorator argument. The intent is that a resolver's
    identity lives in exactly one place (the decorator call); the class
    attribute exists only to make the name reachable from a resolver
    instance at runtime.
    """

    def _decorator(cls: type[CrossRepoResolver]) -> type[CrossRepoResolver]:
        if not isinstance(cls, type) or not issubclass(cls, CrossRepoResolver):
            raise ValueError(
                f"register_resolver can only decorate CrossRepoResolver "
                f"subclasses; got {cls!r}",
            )
        if getattr(cls, "name", "") != name:
            raise ValueError(
                f"resolver {cls.__name__}.name={cls.name!r} does not match "
                f"registration name {name!r}; align them to avoid confusion",
            )
        if name in _REGISTRY:
            raise ValueError(
                f"resolver name {name!r} is already registered by "
                f"{_REGISTRY[name].__name__}",
            )
        _REGISTRY[name] = cls
        return cls

    return _decorator


def get_resolver(name: str) -> type[CrossRepoResolver]:
    """Return the resolver class registered under ``name``.

    Raises :class:`UnknownResolverError` if no such resolver exists. The
    message lists the known names so a typo in ``workspaces.yaml`` fails
    loudly with an actionable hint.
    """
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise UnknownResolverError(
            f"unknown cross-repo resolver: {name!r}. Known: {known}",
        )
    return _REGISTRY[name]


def resolver_names() -> list[str]:
    """Return the sorted list of registered resolver names."""
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _edge_touches_child(edge: CrossRepoEdge, child_name: str) -> bool:
    """Return True when ``edge`` references ``child_name`` on either side."""
    prefix = f"{child_name}{UNIT_SEPARATOR}"
    return edge.from_id.startswith(prefix) or edge.to_id.startswith(prefix)


def run_resolvers(
    context: ResolverContext,
    *,
    post_run_child_hashes: Mapping[str, str] | None = None,
) -> list[CrossRepoEdge]:
    """Invoke each resolver named in ``context.cross_repo_strategies``.

    Invocation happens in the order the strategies appear in the YAML
    config; the orchestrator does not sort or deduplicate the list.

    A resolver that raises is caught -- the exception type and message
    are written to stderr tagged with the resolver's name, and execution
    continues with the next resolver. This isolates a single broken
    resolver from bringing down the entire cross-repo pass.

    After all resolvers complete, if ``post_run_child_hashes`` is
    provided, any edge that references a child whose hash differs from
    the hash recorded on the context is dropped with a warning. This is
    the TOCTOU mitigation described in ADR 0011 § 5: the caller computes
    a fresh hash of each child's bytes after the resolvers run, and
    the orchestrator refuses to commit edges that were computed against
    stale data.

    Returns the aggregate list of edges in the order they were produced.
    Callers that need deterministic byte-identical output should sort
    the list through :func:`weld.federation_support.sorted_edges` before
    writing to disk.
    """
    all_edges: list[CrossRepoEdge] = []
    for name in context.cross_repo_strategies:
        cls = get_resolver(name)  # raises UnknownResolverError
        try:
            resolver = cls()
            produced = list(resolver.resolve(context))
        except Exception as exc:  # noqa: BLE001 -- isolation is the point
            print(
                f"[weld] warning: cross-repo resolver {name!r} raised "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        all_edges.extend(produced)

    if post_run_child_hashes is None:
        return all_edges

    drifted: set[str] = set()
    for child_name, original_hash in context.child_hashes.items():
        observed = post_run_child_hashes.get(child_name)
        if observed is not None and observed != original_hash:
            drifted.add(child_name)

    if not drifted:
        return all_edges

    filtered: list[CrossRepoEdge] = []
    dropped = 0
    for edge in all_edges:
        if any(_edge_touches_child(edge, child) for child in drifted):
            dropped += 1
            continue
        filtered.append(edge)

    if dropped:
        for child in sorted(drifted):
            print(
                f"[weld] warning: child {child!r} drifted during cross-repo "
                f"resolution; dropping edges referencing it",
                file=sys.stderr,
            )
    return filtered
