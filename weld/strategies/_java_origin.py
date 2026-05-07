"""Java origin classification helpers (ADR 0042).

The tree-sitter Java strategy sees ``import`` declarations as fully
qualified type names (``java.util.List``,
``org.springframework.web.bind.annotation.GetMapping``). These helpers
combine that syntax with Maven project metadata so package nodes can
carry a stable ``props.origin`` value.

Detection rules (per ADR 0042 §"Per-language detection rules"):

* **stdlib** -- imports whose first dotted segment is ``java``,
  ``javax``, or ``jdk``. These are the canonical JDK stdlib roots
  (``java.*`` core, ``javax.*`` extensions, ``jdk.*`` JDK-internal /
  incubator modules).
* **external** -- imports whose package matches a ``<dependency>``
  ``<groupId>`` declared in any ``pom.xml`` under the project root.
  Matched by groupId prefix (e.g. groupId ``org.springframework``
  classifies ``org.springframework.web.bind.annotation.GetMapping`` as
  external). Maven artifacts do not always publish under their groupId
  namespace, but the groupId is the best-available static signal
  without invoking the build system.
* **project** -- imports under the project's package namespace,
  derived from the project's own ``<groupId>`` declaration. The
  project groupId always wins over a dependency groupId of the same
  name (a project depending on itself is still ``project``).
* **unresolved** -- everything else.

Gradle (``build.gradle`` / ``build.gradle.kts``) parsing is not
implemented in this module; tracked as a separate
``weld-dogfood-gap`` follow-up.

The helpers are pure: filesystem reads are bounded to discovery-time
``rglob('pom.xml')`` and never write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
import xml.etree.ElementTree as ET

Origin = Literal["project", "stdlib", "external", "unresolved"]

#: First-segment prefixes that always classify as JDK stdlib. ``java.*``
#: is the core JDK; ``javax.*`` covers extensions historically split
#: out (``javax.crypto``, ``javax.xml``...); ``jdk.*`` covers
#: JDK-internal / incubator modules exposed since the Java module
#: system landed.
_JAVA_STDLIB_ROOTS: frozenset[str] = frozenset({"java", "javax", "jdk"})


def load_pom_metadata(root: Path) -> dict[str, frozenset[str]]:
    """Return external dependency groupIds and project groupIds from poms.

    Walks the project tree for ``pom.xml`` files and collects:

    * ``project_groupids``: the ``<project><groupId>`` of each pom
      (top-level groupId, not nested ``<dependency>`` ones). These
      define the project's own package namespace roots.
    * ``dependency_groupids``: the ``<groupId>`` of every
      ``<dependency>`` inside a ``<dependencies>`` block. These define
      the third-party namespace roots.

    Both sets are returned even if either is empty so the caller can
    treat the result as a single immutable mapping.
    """
    project_groupids: set[str] = set()
    dependency_groupids: set[str] = set()
    for pom in _iter_pom_files(root):
        project = _parse_xml(pom)
        if project is None:
            continue
        project_gid = _direct_child_text(project, "groupId")
        if project_gid:
            project_groupids.add(project_gid)
        # Inherit from <parent><groupId> when no project-level groupId
        # is declared (Maven inheritance contract).
        if not project_gid:
            parent = _direct_child(project, "parent")
            if parent is not None:
                inherited = _direct_child_text(parent, "groupId")
                if inherited:
                    project_groupids.add(inherited)
        for dep in _iter_dependencies(project):
            dep_gid = _direct_child_text(dep, "groupId")
            if dep_gid:
                dependency_groupids.add(dep_gid)
    return {
        "project_groupids": frozenset(project_groupids),
        "dependency_groupids": frozenset(dependency_groupids),
    }


def classify_import_package(
    package_name: str,
    *,
    project_groupids: frozenset[str],
    dependency_groupids: frozenset[str],
) -> Origin:
    """Return the ADR 0042 origin for a Java import's package portion.

    *package_name* is the dotted package (everything to the left of
    the final ``.<TypeName>`` in an import); the same string the Java
    enricher uses to mint the ``package:java:...`` node.

    The classification order is intentional:

    1. ``project_groupids`` first -- a project that vendored a
       same-named external dep is still its own code.
    2. ``stdlib`` second -- the JDK roots are reserved and cannot
       legally collide with a project package.
    3. ``dependency_groupids`` third -- third-party Maven coordinates.
    4. ``unresolved`` last -- best-available default per ADR 0042.
    """
    name = package_name.strip()
    if not name:
        return "unresolved"
    if _matches_any_prefix(name, project_groupids):
        return "project"
    if _first_segment(name) in _JAVA_STDLIB_ROOTS:
        return "stdlib"
    if _matches_any_prefix(name, dependency_groupids):
        return "external"
    return "unresolved"


def _iter_pom_files(root: Path) -> list[Path]:
    try:
        return sorted(root.rglob("pom.xml"))
    except OSError:
        return []


def _parse_xml(path: Path) -> ET.Element | None:
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return None


def _local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _direct_child(parent: ET.Element, name: str) -> ET.Element | None:
    """Return the first direct child whose local name equals *name*."""
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_child_text(parent: ET.Element, name: str) -> str:
    child = _direct_child(parent, name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _iter_dependencies(project: ET.Element) -> list[ET.Element]:
    """Yield ``<dependency>`` elements declared under ``<dependencies>``.

    Both top-level ``<dependencies>`` and
    ``<dependencyManagement><dependencies>`` are traversed; both are
    real declarations of third-party coordinates from the project's
    point of view.
    """
    out: list[ET.Element] = []
    for elem in project.iter():
        if _local_name(elem.tag) != "dependencies":
            continue
        for child in list(elem):
            if _local_name(child.tag) == "dependency":
                out.append(child)
    return out


def _first_segment(name: str) -> str:
    return name.split(".", 1)[0]


def _matches_any_prefix(name: str, prefixes: frozenset[str]) -> bool:
    return any(
        name == prefix or name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


__all__ = [
    "Origin",
    "classify_import_package",
    "load_pom_metadata",
]
