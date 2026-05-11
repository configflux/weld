"""Package and helper utilities for ``cpp_cmake`` (ADR 0057).

Splits out the ``find_package`` handler plus the shared utility helpers
(``_ensure_target``, ``_ensure_package_sentinel``, ``_join_path``,
``_is_unresolvable_token``, ``_bump_unresolved``) so the target-call
handler module stays under 400 lines.

All public names are imported back into :mod:`weld.strategies._cmake_targets`
to preserve the existing import surface; new code should use this
module directly.
"""

from __future__ import annotations

from weld._node_ids import file_id, package_id

__all__ = [
    "STRATEGY",
    "build_target_id",
    "bump_unresolved",
    "ensure_package_sentinel",
    "ensure_target",
    "file_node_id",
    "handle_find_package",
    "is_unresolvable_token",
    "join_path",
]

STRATEGY = "cpp_cmake"


def build_target_id(project: str, target: str) -> str:
    """Canonical build-target ID for a non-ROS CMake target.

    Distinct from the ROS2 form ``build-target:ros2:<pkg>:<target>``
    so the two strategies can coexist without ID collisions.
    """
    return f"build-target:cmake:{project}:{target}"


def file_node_id(rel: str) -> str:
    """File node ID for a CMake-relative source path (forward-slash form)."""
    return file_id(rel.replace("\\", "/"))


def ensure_target(
    nodes: dict, project: str, target: str, file_rel: str,
) -> str:
    """Return the build-target node ID, creating a stub if needed."""
    nid = build_target_id(project, target)
    nodes.setdefault(
        nid,
        {
            "type": "build-target",
            "label": f"cmake {project}:{target}",
            "props": {
                "file": file_rel,
                "target_name": target,
                "project_name": project,
                "source_strategy": STRATEGY,
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["build"],
                "unresolved_labels": [],
                "unresolved_labels_dropped": 0,
            },
        },
    )
    return nid


def is_unresolvable_token(token: str) -> bool:
    """A token that v1 cannot resolve to a concrete file/target.

    Anything containing a still-present ``${...}`` (variable that the
    file-scope expander could not resolve), a generator expression
    ``$<...>``, or a stray ``glob(...)`` falls through into
    ``unresolved_labels``.
    """
    if not token:
        return True
    return "${" in token or "$<" in token or token.startswith("glob")


def bump_unresolved(node: dict, labels: list[str]) -> None:
    """Record *labels* in the target node's ``unresolved_labels`` slot."""
    if not labels:
        return
    props = node["props"]
    existing = set(props.get("unresolved_labels", []))
    for lbl in labels:
        if lbl:
            existing.add(lbl)
    props["unresolved_labels"] = sorted(existing)
    props["unresolved_labels_dropped"] = len(existing)


def ensure_package_sentinel(
    nodes: dict, nid: str, name: str,
) -> None:
    """Idempotently create a ``package`` sentinel node for an external dep."""
    nodes.setdefault(
        nid,
        {
            "type": "package",
            "label": name,
            "props": {
                "name": name,
                "source_strategy": STRATEGY,
                "authority": "external",
                "confidence": "inferred",
                "roles": ["config"],
            },
        },
    )


def join_path(cmake_dir: str, src: str) -> str:
    """Join a CMakeLists.txt-relative source path to the repo-relative dir.

    Forward-slash output, no leading ``./``.
    """
    if not cmake_dir or cmake_dir == ".":
        rel = src
    else:
        rel = f"{cmake_dir}/{src}"
    parts: list[str] = []
    for segment in rel.split("/"):
        if not segment or segment == ".":
            continue
        parts.append(segment)
    return "/".join(parts)


def handle_find_package(
    args: list[str],
    project_nid: str,
    nodes: dict,
    edges: list,
    file_rel: str,
) -> None:
    """Emit a ``depends_on`` edge from the project to each package.

    ``find_package(<name>)``                  -> 1 definite edge to
                                                 ``package:cpp:<name>``.
    ``find_package(<name> COMPONENTS a b c)`` -> 1 definite edge to
                                                 ``package:cpp:<name>`` plus
                                                 one ``inferred`` edge per
                                                 component, mirroring the
                                                 ADR 0057 example for Boost.
    Anything before ``COMPONENTS`` (REQUIRED, QUIET, version number,
    EXACT, MODULE, CONFIG, NO_MODULE) is dropped.
    """
    if not args:
        return
    name = args[0]
    pkg_nid = package_id("cpp", name)
    ensure_package_sentinel(nodes, pkg_nid, name)
    edges.append(
        {
            "from": project_nid,
            "to": pkg_nid,
            "type": "depends_on",
            "props": {
                "source_strategy": STRATEGY,
                "confidence": "definite",
                "kind": "find_package",
                "file": file_rel,
            },
        }
    )
    components: list[str] = []
    in_components = False
    for tok in args[1:]:
        if tok == "COMPONENTS":
            in_components = True
            continue
        if tok in ("REQUIRED", "QUIET", "EXACT", "MODULE", "CONFIG",
                   "NO_MODULE", "NO_DEFAULT_PATH"):
            in_components = False
            continue
        if in_components:
            components.append(tok)
    for comp in components:
        comp_nid = package_id("cpp", f"{name}.{comp}")
        ensure_package_sentinel(nodes, comp_nid, f"{name}::{comp}")
        edges.append(
            {
                "from": project_nid,
                "to": comp_nid,
                "type": "depends_on",
                "props": {
                    "source_strategy": STRATEGY,
                    "confidence": "inferred",
                    "kind": "find_package_component",
                    "file": file_rel,
                    "package": name,
                    "component": comp,
                },
            }
        )
