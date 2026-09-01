"""The read-only view a cross-repo resolver is handed, and how it reads it.

Split out of :mod:`weld.cross_repo.base` (ADR 0137 work) so that module stays
under the 400-line cap. The two pieces here are its *input* side -- what a
resolver receives, and the one defensive accessor it reads child nodes through
-- while ``base`` keeps the output contract (:class:`CrossRepoEdge`), the
resolver ABC, the registry and the orchestrator.

Nothing here imports ``base``, which is what makes the re-export there safe:
the dependency runs one way, so ``from weld.cross_repo.base import
ResolverContext`` keeps working for every resolver and test that spells it
that way.
"""

from __future__ import annotations

import hashlib
from types import MappingProxyType
from typing import Any, Iterable, Mapping

__all__ = ["ResolverContext"]


def _iter_nodes(graph: object) -> Iterable[tuple[str, dict]]:
    """Yield ``(node_id, node)`` pairs from a child :class:`weld.graph.Graph`.

    Centralises the defensive ``getattr(graph, '_data', {}).get('nodes', {})``
    access shared by concrete resolvers. Production stores nodes at
    ``_data['nodes']`` keyed by id, each value shaped ``{type, label,
    props}`` (weld/graph.py:49). Objects without ``_data`` and graphs
    whose ``_data['nodes']`` is not a dict both degrade to ``[]`` so
    callers iterate zero pairs rather than raising. Returned in
    dict-insertion order; callers needing determinism must sort.
    Underscore-prefixed: internal coordination point, not in ``__all__``.
    """
    nodes = getattr(graph, "_data", {}).get("nodes", {})
    if not isinstance(nodes, dict):
        return []
    return list(nodes.items())


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
