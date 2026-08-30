"""Cross-repo resolver: package-dependency graph across repositories.

Joins a *manifest-declared* package dependency in one child repository to
the sibling child that *produces* that package, and emits a
``cross_repo:depends_on`` edge from the consuming repo node to the
producing repo node. This is the "schema library consumed via a package
dependency" polyrepo shape that the two shipped resolvers miss:
``service_graph`` matches an HTTP client's URL host against a sibling
name, and ``channel_binding`` matches event-channel topics -- neither
looks at ``<PackageReference>`` / ``pyproject.toml`` dependencies /
``go.mod`` requires (field-eval v0.23.1, Finding 06 "Related").

It is the manifest-level complement to ``package_import_resolver``:

* ``package_import_resolver`` joins *import evidence* -- a file node's
  ``imports_from`` list (populated from ``using`` / ``import`` statements)
  -- to a sibling's ``type=package`` node, edge ``depends_on`` between the
  two file/package nodes.
* ``package_graph`` (this module) joins *build-manifest declarations* --
  what a repo says it depends on and produces -- to the sibling *repo*
  node, edge ``cross_repo:depends_on`` between ``repo:<name>`` nodes. That
  repo-node target is exactly what ``wd impact "repo:<name>"`` reads, so
  wiring this resolver is what gives a schema-library repo node its
  inbound cross-repo edges -- and, per Finding 06 / ADR 0134, what stops
  ``wd impact`` reporting a fabricated ``Risk: LOW, 0 dependents`` and
  falling into ``result_unknown`` for a repo with no resolver wired.

Facts come from disk, not from the child graphs: discovery emits neither
the produced-package name nor the declared dependencies as graph data
(see :mod:`weld.cross_repo._package_manifest_scan`). The resolver reads
each present child's manifests off ``ResolverContext.workspace_root`` +
the child paths declared in ``workspaces.yaml`` -- the same disk-reading
pattern ``compose_topology`` uses for ``docker-compose.yml``.

Matching is a case-fold name equality (see
:func:`_package_manifest_scan.normalize_package_name`). ``confidence`` is
``inferred``: a manifest name match is real evidence of an intended
dependency, but no version pin is checked and no build is performed, so it
is not a ``definite`` static-truth declaration.
"""

from __future__ import annotations

import os

from weld.cross_repo._package_manifest_scan import (
    normalize_package_name,
    scan_child_manifests,
)
from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    register_resolver,
)
from weld.workspace import UNIT_SEPARATOR, load_workspaces_yaml

_EDGE_TYPE = "cross_repo:depends_on"


def _repo_node_id(child_name: str) -> str:
    """Return the federated ``repo:<name>`` node id for *child_name*."""
    return f"{child_name}{UNIT_SEPARATOR}repo:{child_name}"


def _load_child_paths(workspace_root: str) -> dict[str, str]:
    """Return ``{child_name: absolute_child_dir}`` from ``workspaces.yaml``.

    Returns an empty mapping when the config is absent or unparseable --
    a resolver that cannot locate the children emits nothing rather than
    raising. Only the child *paths* are needed; presence filtering is the
    caller's job (via ``context.children``).
    """
    config_path = os.path.join(workspace_root, ".weld", "workspaces.yaml")
    if not os.path.isfile(config_path):
        return {}
    try:
        config = load_workspaces_yaml(config_path)
    except Exception:  # noqa: BLE001 -- a bad config must not sink discovery
        return {}
    paths: dict[str, str] = {}
    for child in config.children:
        paths[child.name] = os.path.join(workspace_root, child.path)
    return paths


@register_resolver("package_graph")
class PackageGraphResolver(CrossRepoResolver):
    """Resolve manifest package dependencies to producing repo nodes.

    Registered under ``package_graph`` so it is selectable via
    ``cross_repo_strategies: [package_graph]`` in ``workspaces.yaml``.
    See the module docstring for the full join algorithm.
    """

    name = "package_graph"

    def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
        child_paths = _load_child_paths(context.workspace_root)
        if not child_paths:
            return []

        # Only scan children the framework reports as present; missing,
        # uninitialized, and corrupt children are filtered out of
        # ``context.children`` upstream.
        present = [name for name in child_paths if name in context.children]
        if not present:
            return []

        produced: dict[str, set[str]] = {}
        consumed: dict[str, set[str]] = {}
        for name in present:
            prod, cons = scan_child_manifests(child_paths[name])
            produced[name] = prod
            consumed[name] = cons

        # Build a producer index keyed by normalized package name so a
        # consumer lookup is a single dict hit. One name may be produced
        # by multiple children; an edge is emitted to each.
        producer_index: dict[str, list[str]] = {}
        for name in sorted(present):
            for pkg in produced[name]:
                producer_index.setdefault(normalize_package_name(pkg), []).append(
                    name
                )

        edges: list[CrossRepoEdge] = []
        for consumer in sorted(present):
            for dep in sorted(consumed[consumer]):
                key = normalize_package_name(dep)
                for producer in producer_index.get(key, []):
                    # No self-edge: a repo that declares and depends on
                    # its own name is not a cross-repo relationship.
                    if producer == consumer:
                        continue
                    edges.append(
                        CrossRepoEdge(
                            from_id=_repo_node_id(consumer),
                            to_id=_repo_node_id(producer),
                            type=_EDGE_TYPE,
                            props={
                                "source_strategy": "package_graph",
                                "confidence": "inferred",
                                "package": dep,
                            },
                        )
                    )

        edges.sort(key=lambda e: (e.from_id, e.to_id, e.props.get("package", "")))
        return edges
