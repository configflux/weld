"""Cross-repo resolver: compose topology.

Reads ``docker-compose.yml``, ``compose.yaml``, or ``compose.yml`` at
the workspace root and emits ``depends_on`` edges between services whose
images (or service names) map to registered children.

The resolver is pure: it reads the compose file via the filesystem path
exposed by :attr:`ResolverContext.workspace_root`, maps each service to a
child name, and emits one :class:`CrossRepoEdge` per ``depends_on`` entry
where both the source and target services resolve to present children.

Matching strategy (in order):

1. If the service declares an ``image`` key, strip the optional registry
   prefix and tag (``ghcr.io/org/repo-a:latest`` -> ``repo-a``), then
   look up the result in ``context.children``.
2. Fall back to the service name itself as the child name.

Services that do not resolve to any present child are silently ignored --
they represent third-party infrastructure (Redis, Postgres, etc.) that is
not part of the workspace.
"""

from __future__ import annotations

import os
from typing import Any

from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    register_resolver,
)
from weld.workspace import UNIT_SEPARATOR

# Compose file names searched in order of precedence.  The first match wins;
# later files are not merged (docker compose merges are a runtime concern,
# not relevant to static topology analysis).
_COMPOSE_FILENAMES: tuple[str, ...] = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yaml",
    "compose.yml",
)


def _strip_image_ref(image: str) -> str:
    """Strip registry prefix and tag from a Docker image reference.

    ``ghcr.io/org/repo-a:latest`` -> ``repo-a``
    ``repo-a:v1.2.3``            -> ``repo-a``
    ``repo-a``                    -> ``repo-a``
    """
    # Strip tag (everything after the last colon that is not part of a port).
    if ":" in image:
        image = image.rsplit(":", 1)[0]
    # Strip registry/org prefix: take the last path segment.
    if "/" in image:
        image = image.rsplit("/", 1)[-1]
    return image


def _find_compose_file(workspace_root: str) -> str | None:
    """Return the path to the first compose file found, or None."""
    for name in _COMPOSE_FILENAMES:
        path = os.path.join(workspace_root, name)
        if os.path.isfile(path):
            return path
    return None


def _parse_compose(path: str) -> dict[str, Any]:
    """Parse a compose file and return its content as a dict.

    Uses the repo-local minimal YAML parser to avoid external
    dependencies. Returns an empty dict if parsing fails.
    """
    from weld._yaml import parse_yaml

    with open(path) as f:
        text = f.read()
    result = parse_yaml(text)
    if isinstance(result, dict):
        return result
    return {}


def _resolve_service_to_child(
    service_name: str,
    service_config: Any,
    children: dict[str, Any],
) -> str | None:
    """Map a compose service to a child name, or return None."""
    if isinstance(service_config, dict):
        image = service_config.get("image")
        if image and isinstance(image, str):
            candidate = _strip_image_ref(image)
            if candidate in children:
                return candidate
    # Fallback: service name is the child name.
    if service_name in children:
        return service_name
    return None


def _extract_depends_on(service_config: Any) -> list[str]:
    """Extract the list of dependency service names from a service config.

    Handles both the list form::

        depends_on:
          - auth
          - cache

    and the map form::

        depends_on:
          auth:
            condition: service_healthy
    """
    if not isinstance(service_config, dict):
        return []
    deps = service_config.get("depends_on")
    if deps is None:
        return []
    if isinstance(deps, list):
        return [str(d) for d in deps]
    if isinstance(deps, dict):
        return sorted(deps.keys())
    return []


@register_resolver("compose_topology")
class ComposeTopologyResolver(CrossRepoResolver):
    """Emit ``depends_on`` edges from docker-compose service wiring."""

    name = "compose_topology"

    def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
        compose_path = _find_compose_file(context.workspace_root)
        if compose_path is None:
            return []

        compose = _parse_compose(compose_path)
        services = compose.get("services")
        if not isinstance(services, dict):
            return []

        # Build a mapping: service_name -> child_name (only for present children).
        children = dict(context.children)
        service_to_child: dict[str, str] = {}
        for svc_name, svc_config in sorted(services.items()):
            child = _resolve_service_to_child(svc_name, svc_config, children)
            if child is not None:
                service_to_child[svc_name] = child

        # Emit edges for each depends_on entry where both sides resolve.
        source_file = os.path.basename(compose_path)
        edges: list[CrossRepoEdge] = []
        for svc_name in sorted(services.keys()):
            if svc_name not in service_to_child:
                continue
            from_child = service_to_child[svc_name]
            svc_config = services[svc_name]
            for dep_name in sorted(_extract_depends_on(svc_config)):
                if dep_name not in service_to_child:
                    continue
                to_child = service_to_child[dep_name]
                if from_child == to_child:
                    # Self-dependency within the same child: skip.
                    continue
                edges.append(
                    CrossRepoEdge(
                        from_id=f"{from_child}{UNIT_SEPARATOR}repo:{from_child}",
                        to_id=f"{to_child}{UNIT_SEPARATOR}repo:{to_child}",
                        type="depends_on",
                        props={
                            "from_service": svc_name,
                            "to_service": dep_name,
                            "source_file": source_file,
                        },
                    )
                )

        return edges
