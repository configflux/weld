"""Strategy: ROS2 ``package.xml`` extractor.

Parses ROS2 manifest files (``package.xml``) using stdlib
``xml.etree.ElementTree`` and emits:

- one ``ros_package`` node per manifest, keyed ``package:ros2:<slug>``
  (per ADR 0041 § Layer 1), with props
  ``{version, description, maintainer, license, build_type}``.
  ``build_type`` is read from ``<export><build_type>...</build_type></export>``
  and is typically ``ament_cmake``, ``ament_python``, or ``cmake``. The
  legacy ``ros_package:<name>`` shape is preserved on each node's
  ``props.aliases`` list for one minor version.
- ``depends_on`` edges from the package to every referenced dependency
  (``<depend>``, ``<build_depend>``, ``<exec_depend>``, ``<test_depend>``,
  ``<buildtool_depend>``) as ``ros_package`` sentinel nodes. Sentinels are
  shared across manifests so that cross-package wiring in the same workspace
  resolves naturally.
- ``contains`` edges from the package to every immediate file under its
  containing directory (recorded as ``file:<rel_posix_no_ext>`` sentinels
  per ADR 0041 § Layer 1). The strategy does not enumerate subdirectories
  — neighbouring strategies (``ros2_cmake``, tree_sitter) will attach
  deeper detail.

See the ROS2 connected-structure schema ADR.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from weld._graph_node_registry import ensure_node
from weld._node_ids import file_id, package_id
from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

_DEPEND_TAGS: tuple[str, ...] = (
    "depend",
    "build_depend",
    "exec_depend",
    "test_depend",
    "buildtool_depend",
    "run_depend",  # legacy / catkin compatibility
)

_STRATEGY = "ros2_package"

def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()

def _build_type(root: ET.Element) -> str:
    export = root.find("export")
    if export is None:
        return ""
    bt = export.find("build_type")
    return _text(bt)

def _package_node_id(name: str) -> str:
    """Canonical ROS2 package ID per ADR 0041 (``package:ros2:<slug>``)."""
    return package_id("ros2", name)


def _legacy_package_id(name: str) -> str:
    """Pre-ADR-0041 ID shape; recorded under ``aliases`` for compat."""
    return f"ros_package:{name}"

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract ``ros_package`` nodes from package.xml manifests."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    if "**" in pattern:
        matched = filter_glob_results(root, sorted(root.glob(pattern)))
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return StrategyResult(nodes, edges, discovered_from)
        matched = filter_glob_results(
            root, sorted(parent.glob(Path(pattern).name))
        )

    for manifest in matched:
        if not manifest.is_file():
            continue
        if should_skip(manifest, excludes):
            continue

        rel_path = str(manifest.relative_to(root))
        discovered_from.append(rel_path)

        try:
            tree = ET.parse(manifest)
        except (OSError, ET.ParseError):
            continue
        xml_root = tree.getroot()

        name = _text(xml_root.find("name"))
        if not name:
            continue

        nid = _package_node_id(name)
        ensure_node(
            nodes,
            nid,
            "ros_package",
            source_strategy=_STRATEGY,
            source_path=rel_path,
            authority="canonical",
            props={
                "name": name,
                "file": rel_path,
                "version": _text(xml_root.find("version")),
                "description": _text(xml_root.find("description")),
                "maintainer": _text(xml_root.find("maintainer")),
                "license": _text(xml_root.find("license")),
                "build_type": _build_type(xml_root),
                "confidence": "definite",
                "roles": ["config"],
                "aliases": [_legacy_package_id(name)],
            },
        )

        # Deduplicate dep names per package so we do not emit duplicate
        # depends_on edges when the same dep is listed under several tags.
        seen_deps: set[str] = set()
        for tag in _DEPEND_TAGS:
            for dep in xml_root.findall(tag):
                dep_name = _text(dep)
                if not dep_name or dep_name in seen_deps:
                    continue
                seen_deps.add(dep_name)
                dep_nid = _package_node_id(dep_name)
                # Create / merge a sentinel node for the dependency. A later
                # manifest claiming canonical authority for the same package
                # wins via ensure_node's authority precedence.
                ensure_node(
                    nodes,
                    dep_nid,
                    "ros_package",
                    source_strategy=_STRATEGY,
                    source_path=rel_path,
                    authority="external",
                    props={
                        "name": dep_name,
                        "confidence": "inferred",
                        "roles": ["config"],
                        "aliases": [_legacy_package_id(dep_name)],
                    },
                )
                edges.append(
                    {
                        "from": nid,
                        "to": dep_nid,
                        "type": "depends_on",
                        "props": {
                            "source_strategy": _STRATEGY,
                            "confidence": "definite",
                            "kind": tag,
                        },
                    }
                )

        # contains edges: package -> immediate files in its directory.
        pkg_dir = manifest.parent
        try:
            children = sorted(pkg_dir.iterdir())
        except OSError:
            children = []
        for child in children:
            if not child.is_file():
                continue
            rel_child = str(child.relative_to(root))
            file_nid = file_id(rel_child)
            legacy_file_nid = f"file:{rel_child}"
            ensure_node(
                nodes,
                file_nid,
                "file",
                source_strategy=_STRATEGY,
                source_path=rel_child,
                authority="canonical",
                props={
                    "name": child.name,
                    "file": rel_child,
                    "confidence": "definite",
                    "roles": ["config"],
                    "aliases": (
                        [legacy_file_nid] if legacy_file_nid != file_nid else []
                    ),
                },
            )
            edges.append(
                {
                    "from": nid,
                    "to": file_nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": _STRATEGY,
                        "confidence": "definite",
                    },
                }
            )

    return StrategyResult(nodes, edges, discovered_from)
