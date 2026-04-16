"""Strategy: ROS2 ``.msg`` / ``.srv`` / ``.action`` interface extractor.

Parses ROS2 interface definition files and emits ``ros_interface`` nodes
plus ``contains`` edges from the owning ``ros_package``.

File formats (intentionally handled with a tiny line parser — these are
whitespace-delimited text files with a fixed grammar):

- ``.msg``
    ``<type> <field_name> [= <const>]``
    One field per line; blank lines and ``# ...`` comments are ignored.
    Arrays are expressed via ``type[]`` or ``type[N]`` in the field type.
    Emits one ``ros_interface:<pkg>/msg/<Name>`` node with ``fields``.

- ``.srv``
    A single ``---`` separator splits the file into request and response
    field blocks. Emits one ``ros_interface:<pkg>/srv/<Name>`` node with
    ``request_fields`` and ``response_fields``.

- ``.action``
    Two ``---`` separators split the file into goal, result, and
    feedback blocks. Emits the parent ``ros_interface:<pkg>/action/<Name>``
    node plus three sub-interface nodes
    ``ros_interface:<pkg>/action/<Name>_Goal``,
    ``_Result``, and ``_Feedback``. This matches the runtime types that
    ``rosidl`` generates for each ``.action`` file, so downstream
    consumers can reference the sub-interfaces directly.

Owning package resolution: the strategy derives ``<pkg>`` from the file
path. ROS2 convention is ``<pkg>/msg/Foo.msg``, ``<pkg>/srv/Bar.srv``,
``<pkg>/action/Baz.action``. If the immediate parent directory of the
interface file is ``msg``/``srv``/``action``, the grandparent directory
name is used as the package name; otherwise the parent directory name is
used as a best-effort fallback.

See the ROS2 connected-structure schema ADR.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

_STRATEGY = "ros2_interfaces"

_INTERFACE_EXTS: frozenset[str] = frozenset({".msg", ".srv", ".action"})
_SUB_INTERFACES: tuple[str, ...] = ("_Goal", "_Result", "_Feedback")

def _owning_package(iface_path: Path) -> str:
    """Return the ROS package name that owns *iface_path*.

    ROS2 convention places interface files at ``<pkg>/msg/Foo.msg``,
    ``<pkg>/srv/Bar.srv``, or ``<pkg>/action/Baz.action``. If the parent
    directory is ``msg``/``srv``/``action`` we use the grandparent as the
    package; otherwise we fall back to the immediate parent.
    """
    parent = iface_path.parent
    if parent.name in {"msg", "srv", "action"} and parent.parent.name:
        return parent.parent.name
    return parent.name or "unknown"

def _strip_comment(line: str) -> str:
    """Remove ``# ...`` trailing comments from a field line."""
    idx = line.find("#")
    if idx >= 0:
        line = line[:idx]
    return line.strip()

def _parse_field_block(block: str) -> list[dict]:
    """Parse a ``.msg``-style field block into a list of field records.

    Each record has ``type``, ``name``, and optionally ``default``. Lines
    that cannot be split into at least a ``<type> <name>`` pair are
    skipped silently — the extractor is tolerant of comments, blank
    lines, and partially-written files.
    """
    fields: list[dict] = []
    for raw in block.splitlines():
        line = _strip_comment(raw)
        if not line:
            continue
        # Default/constant values use '<type> <name> = <value>' form.
        default: str | None = None
        if "=" in line:
            lhs, _, rhs = line.partition("=")
            line = lhs.strip()
            default = rhs.strip()
        parts = line.split()
        if len(parts) < 2:
            continue
        field_type = parts[0]
        field_name = parts[1]
        entry: dict = {"type": field_type, "name": field_name}
        if default is not None:
            entry["default"] = default
        fields.append(entry)
    return fields

def _split_blocks(text: str) -> list[str]:
    """Split a ``.srv`` / ``.action`` body on ``---`` separator lines.

    Returns the list of block strings in file order. Lines consisting of
    three or more dashes (optionally surrounded by whitespace) are
    treated as separators. Blank lines inside blocks are preserved so
    ``_parse_field_block`` sees them unchanged.
    """
    blocks: list[list[str]] = [[]]
    for raw in text.splitlines():
        if raw.strip().startswith("---") and set(raw.strip()) == {"-"}:
            blocks.append([])
            continue
        blocks[-1].append(raw)
    return ["\n".join(b) for b in blocks]

def _pkg_nid(name: str) -> str:
    return f"ros_package:{name}"

def _ensure_package_sentinel(nodes: dict[str, dict], name: str) -> None:
    nid = _pkg_nid(name)
    nodes.setdefault(
        nid,
        {
            "type": "ros_package",
            "label": name,
            "props": {
                "source_strategy": _STRATEGY,
                "authority": "external",
                "confidence": "inferred",
                "roles": ["config"],
            },
        },
    )

def _interface_node(
    *,
    nid: str,
    label: str,
    rel_path: str,
    pkg: str,
    kind: str,
    extra_props: dict,
) -> dict:
    props: dict = {
        "file": rel_path,
        "package": pkg,
        "interface_kind": kind,
        "source_strategy": _STRATEGY,
        "authority": "canonical",
        "confidence": "definite",
        "roles": ["config"],
    }
    props.update(extra_props)
    return {"type": "ros_interface", "label": label, "props": props}

def _emit_msg(
    iface_path: Path,
    rel_path: str,
    pkg: str,
    nodes: dict,
    edges: list,
) -> None:
    name = iface_path.stem
    fields = _parse_field_block(iface_path.read_text(encoding="utf-8", errors="replace"))
    nid = f"ros_interface:{pkg}/msg/{name}"
    nodes[nid] = _interface_node(
        nid=nid,
        label=f"{pkg}/msg/{name}",
        rel_path=rel_path,
        pkg=pkg,
        kind="msg",
        extra_props={"fields": fields},
    )
    _link_package(pkg, nid, nodes, edges)

def _emit_srv(
    iface_path: Path,
    rel_path: str,
    pkg: str,
    nodes: dict,
    edges: list,
) -> None:
    name = iface_path.stem
    text = iface_path.read_text(encoding="utf-8", errors="replace")
    blocks = _split_blocks(text)
    request_fields = _parse_field_block(blocks[0]) if blocks else []
    response_fields = _parse_field_block(blocks[1]) if len(blocks) > 1 else []
    nid = f"ros_interface:{pkg}/srv/{name}"
    nodes[nid] = _interface_node(
        nid=nid,
        label=f"{pkg}/srv/{name}",
        rel_path=rel_path,
        pkg=pkg,
        kind="srv",
        extra_props={
            "request_fields": request_fields,
            "response_fields": response_fields,
        },
    )
    _link_package(pkg, nid, nodes, edges)

def _emit_action(
    iface_path: Path,
    rel_path: str,
    pkg: str,
    nodes: dict,
    edges: list,
) -> None:
    name = iface_path.stem
    text = iface_path.read_text(encoding="utf-8", errors="replace")
    blocks = _split_blocks(text)
    goal_fields = _parse_field_block(blocks[0]) if blocks else []
    result_fields = _parse_field_block(blocks[1]) if len(blocks) > 1 else []
    feedback_fields = _parse_field_block(blocks[2]) if len(blocks) > 2 else []

    base_label = f"{pkg}/action/{name}"
    parent_nid = f"ros_interface:{base_label}"
    nodes[parent_nid] = _interface_node(
        nid=parent_nid,
        label=base_label,
        rel_path=rel_path,
        pkg=pkg,
        kind="action",
        extra_props={
            "goal_fields": goal_fields,
            "result_fields": result_fields,
            "feedback_fields": feedback_fields,
        },
    )
    _link_package(pkg, parent_nid, nodes, edges)

    sub_field_map = {
        "_Goal": goal_fields,
        "_Result": result_fields,
        "_Feedback": feedback_fields,
    }
    for suffix in _SUB_INTERFACES:
        sub_label = f"{base_label}{suffix}"
        sub_nid = f"ros_interface:{sub_label}"
        nodes[sub_nid] = _interface_node(
            nid=sub_nid,
            label=sub_label,
            rel_path=rel_path,
            pkg=pkg,
            kind=f"action{suffix}",
            extra_props={"fields": sub_field_map[suffix]},
        )
        # The owning package also contains each generated sub-interface,
        # matching how rosidl exposes them to downstream consumers.
        _link_package(pkg, sub_nid, nodes, edges)
        # And the parent ros_interface contains its three sub-interfaces.
        edges.append(
            {
                "from": parent_nid,
                "to": sub_nid,
                "type": "contains",
                "props": {
                    "source_strategy": _STRATEGY,
                    "confidence": "definite",
                    "kind": "action_sub_interface",
                },
            }
        )

def _link_package(pkg: str, iface_nid: str, nodes: dict, edges: list) -> None:
    _ensure_package_sentinel(nodes, pkg)
    edges.append(
        {
            "from": _pkg_nid(pkg),
            "to": iface_nid,
            "type": "contains",
            "props": {
                "source_strategy": _STRATEGY,
                "confidence": "definite",
            },
        }
    )

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract ``ros_interface`` nodes from ``.msg``/``.srv``/``.action``."""
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

    for iface in matched:
        if not iface.is_file():
            continue
        if should_skip(iface, excludes):
            continue
        if iface.suffix not in _INTERFACE_EXTS:
            continue

        rel_path = str(iface.relative_to(root))
        discovered_from.append(rel_path)

        pkg = _owning_package(iface)

        try:
            if iface.suffix == ".msg":
                _emit_msg(iface, rel_path, pkg, nodes, edges)
            elif iface.suffix == ".srv":
                _emit_srv(iface, rel_path, pkg, nodes, edges)
            elif iface.suffix == ".action":
                _emit_action(iface, rel_path, pkg, nodes, edges)
        except OSError:
            continue

    return StrategyResult(nodes, edges, discovered_from)
