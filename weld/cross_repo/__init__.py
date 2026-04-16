"""Cross-repo resolver framework for federated polyrepo workspaces.

A cross-repo resolver inspects child graphs that have been loaded by the
workspace root and emits typed edges that describe relationships spanning
repository boundaries (for example, an HTTP client in one child repo that
calls a FastAPI endpoint declared in another). Resolvers are plugins: each
one is a subclass of :class:`CrossRepoResolver` registered under a unique
name via :func:`register_resolver`, and the workspace root decides which
resolvers run via the ``cross_repo_strategies`` key in ``workspaces.yaml``.

The :mod:`weld.cross_repo.base` module owns the contract surface -- the
edge dataclass, resolver ABC, registry, context object, orchestrator, and
named errors. Concrete resolvers live in sibling modules (for example,
:mod:`weld.cross_repo.service_graph`) and are imported here for their
registration side effect so ``cross_repo_strategies`` lookups resolve
without callers having to import each resolver explicitly.
:mod:`weld.cross_repo.grpc_service_binding`) and are imported for their
:mod:`weld.cross_repo.compose_topology`) and are imported for their
registration side effect.
"""

# Concrete resolvers -- imported for their registration side effect.
import weld.cross_repo.grpc_service_binding as _grpc_service_binding  # noqa: F401

from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    MalformedEdgeError,
    ResolverContext,
    UnknownResolverError,
    get_resolver,
    register_resolver,
    resolver_names,
    run_resolvers,
)
from weld.cross_repo.overrides import (
    Override,
    OverrideParseError,
    apply_overrides,
    load_overrides,
)
from weld.cross_repo.incremental import (
    DriftResult,
    detect_drift,
    run_resolvers_incremental,
)

# Import concrete resolvers for their registration side effect. Adding a
# new resolver is a two-line change here: write the module, then import
# it below. The registry refuses duplicates, so accidentally importing
# the same resolver twice fails loudly rather than silently shadowing.
from weld.cross_repo import service_graph as _service_graph  # noqa: F401
# Concrete resolvers -- imported for their registration side effect.
from weld.cross_repo import compose_topology as _compose_topology  # noqa: F401

__all__ = [
    "CrossRepoEdge",
    "CrossRepoResolver",
    "DriftResult",
    "MalformedEdgeError",
    "Override",
    "OverrideParseError",
    "ResolverContext",
    "UnknownResolverError",
    "apply_overrides",
    "detect_drift",
    "get_resolver",
    "load_overrides",
    "register_resolver",
    "resolver_names",
    "run_resolvers",
    "run_resolvers_incremental",
]
