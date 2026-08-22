"""Package and helper utilities for ``cpp_cmake`` (ADR 0057).

Splits out the ``find_package`` handler plus the shared utility helpers
(``_ensure_target``, ``_ensure_package_sentinel``, ``_join_path``,
``_is_unresolvable_token``, ``_bump_unresolved``) so the target-call
handler module stays under 400 lines.

All public names are imported back into :mod:`weld.strategies._cmake_targets`
to preserve the existing import surface; new code should use this
module directly.

bd tuuve: ``ensure_package_sentinel``'s external dependency LEAF and
:func:`weld.strategies.cpp_cmake._ensure_project_node`'s PROJECT node
share the identical ``package:cpp:<name>`` id space -- cpp_cmake has no
dedicated URL scheme for its leaves, unlike ``cpp_conan``/``cpp_vcpkg``'s
``package://conan/``/``package://vcpkg/``. See ``ensure_package_sentinel``'s
docstring for why that is a deliberate choice, not an oversight left
unfixed, and how the ADR 0103 ``claim_supersedes`` veto keeps the shared
namespace safe.
"""

from __future__ import annotations

from weld._discover_node_merge import claim_supersedes
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
    """Claim a ``package`` sentinel node for an external dep at *nid*.

    Shared by :func:`handle_find_package` (both the base package and each
    ``COMPONENTS`` entry) and
    :func:`weld.strategies._cmake_targets.handle_target_link_libraries`'s
    external-library branch -- every non-in-project ``package:cpp:<name>``
    leaf this strategy mints goes through here.

    Uses the ADR 0103 confidence-ranked veto
    (:func:`weld._discover_node_merge.claim_supersedes`), not a blind
    ``nodes.setdefault``. This id space is shared with
    :func:`weld.strategies.cpp_cmake._ensure_project_node`'s PROJECT node
    (no dedicated URL scheme here, unlike ``cpp_conan``/``cpp_vcpkg`` --
    see the module docstring for why that is deliberate), so a project can
    collide on this exact id with one of its own, or a sibling's, declared
    dependencies. Before bd tuuve this call was a plain ``setdefault``: a
    strategy's own multi-file ``nodes`` accumulation for one glob is built
    in a single :func:`weld.strategies.cpp_cmake.extract` call, entirely
    inside this one strategy, so the ADR 0103 veto the orchestrator applies
    in :mod:`weld.discover` never saw the collision -- it only compares the
    ONE value each source entry's ``StrategyResult`` already settled on
    across DIFFERENT source entries, never across files within one. Whichever
    file :func:`weld.strategies._glob_resolve.resolve_glob` happened to walk
    first silently won outright, discarding the other claim's entire prop
    set -- including, when a project lost to a same-named sentinel, its
    ``props.file`` anchor. A file-less, ``authority: "external"`` node then
    became reachable by
    :func:`weld._discover_external_package_purge.emptied_external_package_node_ids`,
    so an unrelated sibling file's deletion could purge the clobbered id
    outright even though the real project's own CMakeLists.txt was
    untouched -- confirmed empirically against the real
    ``_discover_single_repo`` orchestrator, full and incremental, both file
    orders.

    ``claim_supersedes`` makes the outcome order-independent instead: this
    sentinel's ``confidence: "inferred"`` can never overwrite a project's
    ``confidence: "definite"`` claim on the same id in either processing
    order, while a project claimed *after* an existing sentinel still
    correctly upgrades it.
    """
    candidate = {
        "type": "package",
        "label": name,
        "props": {
            "name": name,
            "source_strategy": STRATEGY,
            "authority": "external",
            "confidence": "inferred",
            "roles": ["config"],
        },
    }
    if claim_supersedes(nodes.get(nid), candidate):
        nodes[nid] = candidate


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
