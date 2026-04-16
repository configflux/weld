"""Low-level rclpy scanning helpers for the ``ros2_topology`` strategy.

This module is intentionally private (leading underscore) and only
used by ``weld/strategies/ros2_topology.py``.  It mirrors the split
pattern established by ``_ros2_cpp.py`` so the strategy dispatcher
itself stays under the repo-wide 400-line budget.  Tests import
``ros2_topology`` directly; they do not import from here.

Unlike the C++ helper, the Python half walks the stdlib ``ast`` module
instead of token-level regexes: Python source is regular enough that
``ast`` is both simpler and safer.  The helpers cover:

- config loading for ``weld/languages/python_ros2.yaml``
- rclpy base-class classification (Node / LifecycleNode), matching
  both ``Node`` and fully qualified ``rclpy.node.Node`` forms
- runtime-name extraction from ``super().__init__("name")`` /
  ``Node.__init__(self, "name")`` super-ctor calls in ``__init__``
- message-type resolution from a positional class reference like
  ``std_msgs.msg.String`` -> ``("std_msgs/msg/String", iface_nid)``
- a per-file scanner that upserts ``ros_node`` nodes for rclpy Node
  subclasses, scans their method bodies for recognised call sites,
  and falls back to a module-scope ``symbol:py:<module>:<file>``
  sentinel for file-scope calls

 and the module docstring of ``ros2_topology.py``
for the full recognition contract.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld._yaml import parse_yaml
from weld.strategies import ros2_topology as _topo

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_py_ros2_config() -> dict:
    """Load ``weld/languages/python_ros2.yaml``; empty dict on any failure."""
    path = (
        Path(__file__).resolve().parent.parent
        / "languages" / "python_ros2.yaml"
    )
    if not path.is_file():
        return {}
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

# ---------------------------------------------------------------------------
# Module qualname / file caller sentinel
# ---------------------------------------------------------------------------

def _module_for(rel_path: str) -> str:
    p = Path(rel_path)
    parts: list[str] = []
    for part in p.parts[:-1]:
        if part in {"src", "demo_pkg"}:  # nothing special — keep generic
            parts.append(part)
        else:
            parts.append(part)
    parts.append(p.stem)
    # Collapse leading ``src`` and the ROS2 package-name/package-name
    # repetition so e.g. ``src/demo_pkg/demo_pkg/talker.py`` becomes
    # ``demo_pkg.talker``.  We keep the trailing stem and walk back
    # while dropping ``src`` and any duplicated adjacent segments.
    cleaned: list[str] = []
    for seg in parts:
        if seg == "src":
            continue
        if cleaned and cleaned[-1] == seg:
            continue
        cleaned.append(seg)
    return ".".join(cleaned) if cleaned else p.stem

def file_caller(nodes: dict, rel_path: str) -> str:
    """Create (if needed) and return a ``symbol:py:<module>:<file>`` id."""
    module = _module_for(rel_path)
    nid = f"symbol:py:{module}:<file>"
    nodes.setdefault(nid, {
        "type": "symbol", "label": module,
        "props": {
            "file": rel_path, "module": module, "qualname": "<file>",
            "language": "python", "scope": "module",
            "confidence": "inferred", "roles": ["implementation"],
            **_topo._DERIVED,
        },
    })
    return nid

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _dotted_name(node: ast.AST) -> str:
    """Return a dotted string for ``ast.Name`` / ``ast.Attribute`` chains.

    Returns ``""`` for anything that is not a simple dotted reference.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted_name(node.value)
        if not head:
            return ""
        return f"{head}.{node.attr}"
    return ""

def _callee_name(call: ast.Call) -> str | None:
    """Return the unqualified callee name for a ``Call`` node."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None

def base_is_ros_node(
    base: ast.AST, node_bases: list,
) -> tuple[bool, bool]:
    """Return ``(is_node, lifecycle)`` for a class base expression."""
    dotted = _dotted_name(base)
    if not dotted:
        return False, False
    tail = dotted.rsplit(".", 1)[-1]
    for entry in node_bases:
        if not isinstance(entry, dict):
            continue
        match = entry.get("match", "")
        tail_match = entry.get("tail", "")
        if not match and not tail_match:
            continue
        if dotted == match or dotted.endswith("." + match):
            return True, bool(entry.get("lifecycle", False))
        if tail_match and tail == tail_match:
            return True, bool(entry.get("lifecycle", False))
    return False, False

def resolve_interface(type_expr: ast.AST | None) -> tuple[str, str | None]:
    """Resolve a class reference like ``std_msgs.msg.String``.

    Returns ``(message_type, interface_nid)``.  Non-dotted or
    non-conforming expressions fall back to ``"<unresolved>"``.
    """
    if type_expr is None:
        return "<unresolved>", None
    dotted = _dotted_name(type_expr)
    if not dotted:
        return "<unresolved>", None
    parts = dotted.split(".")
    if len(parts) < 3:
        return "<unresolved>", None
    kind = parts[-2]
    if kind not in {"msg", "srv", "action"}:
        return "<unresolved>", None
    pkg = parts[-3]
    name = parts[-1]
    display = f"{pkg}/{kind}/{name}"
    return display, f"ros_interface:{display}"

def _string_literal_arg(args: list[ast.AST], index: int) -> str | None:
    if index >= len(args):
        return None
    node = args[index]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None

def extract_runtime_name(cls: ast.ClassDef) -> str | None:
    """Find a ``super().__init__("name")`` literal in ``__init__``."""
    for stmt in cls.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        if stmt.name != "__init__":
            continue
        for sub in ast.walk(stmt):
            if not isinstance(sub, ast.Call):
                continue
            name = _callee_name(sub)
            if name != "__init__":
                continue
            lit = _string_literal_arg(sub.args, 0)
            if lit is not None:
                return lit
    return None

# ---------------------------------------------------------------------------
# Per-call emission
# ---------------------------------------------------------------------------

def _emit_topic(
    spec: dict, call: ast.Call, owner_nid: str, owner_qn: str,
    nodes: dict, edges: list, rel_path: str, dyn: dict[str, int],
    callee: str,
) -> None:
    type_index = int(spec.get("type_index", 0))
    name_index = int(spec.get("name_index", 1))
    args = list(call.args)
    type_expr = args[type_index] if type_index < len(args) else None
    mt, iface_nid = resolve_interface(type_expr)
    literal = _string_literal_arg(args, name_index)
    if literal is not None:
        topic_name, dynamic = literal, False
    else:
        dyn[owner_qn] = dyn.get(owner_qn, 0) + 1
        topic_name = f"<dynamic>:{owner_qn}/{dyn[owner_qn]}"
        dynamic = True
    topic_nid = _topo._ensure_topic(
        nodes, edges, topic_name=topic_name, mt=mt, iface_nid=iface_nid,
        rel_path=rel_path, dynamic=dynamic,
    )
    _topo._append_edge(
        edges, owner_nid, topic_nid, spec["role"], rel_path, callee,
    )

def _emit_typed(
    spec: dict, kind: str, call: ast.Call, owner_nid: str,
    nodes: dict, edges: list, rel_path: str, callee: str,
) -> None:
    type_index = int(spec.get("type_index", 0))
    name_index = int(spec.get("name_index", 1))
    args = list(call.args)
    type_expr = args[type_index] if type_index < len(args) else None
    literal = _string_literal_arg(args, name_index)
    if literal is None:
        return
    mt, _iface = resolve_interface(type_expr)
    dst_nid = _topo._ensure_typed(
        nodes, kind=kind, name=literal, typed=mt, rel_path=rel_path,
    )
    _topo._append_edge(
        edges, owner_nid, dst_nid, spec["role"], rel_path, callee,
    )

def _emit_parameter(
    spec: dict, call: ast.Call, owner_nid: str, owner_qn: str,
    nodes: dict, edges: list, rel_path: str, callee: str,
) -> None:
    name_index = int(spec.get("name_index", 0))
    literal = _string_literal_arg(list(call.args), name_index)
    if literal is None:
        return
    param_nid = _topo._ensure_param(
        nodes, owner=owner_qn, name=literal, ptype=None,
        declared=bool(spec.get("declared", False)), rel_path=rel_path,
    )
    _topo._append_edge(
        edges, owner_nid, param_nid, "configures", rel_path, callee,
    )

def _scan_calls(
    body: list[ast.AST], *, owner_nid: str, owner_qn: str,
    calls_cfg: dict, nodes: dict, edges: list, rel_path: str,
    dyn: dict[str, int],
) -> None:
    """Walk *body* and emit ROS2 nodes / edges for every recognised call."""
    for stmt in body:
        for sub in ast.walk(stmt):
            if not isinstance(sub, ast.Call):
                continue
            callee = _callee_name(sub)
            if callee is None or callee not in calls_cfg:
                continue
            spec = calls_cfg[callee]
            kind = spec.get("kind")
            if kind == "topic":
                _emit_topic(
                    spec, sub, owner_nid, owner_qn,
                    nodes, edges, rel_path, dyn, callee,
                )
            elif kind in {"service", "action"}:
                _emit_typed(
                    spec, kind, sub, owner_nid,
                    nodes, edges, rel_path, callee,
                )
            elif kind == "parameter":
                _emit_parameter(
                    spec, sub, owner_nid, owner_qn,
                    nodes, edges, rel_path, callee,
                )

# ---------------------------------------------------------------------------
# File-level entrypoint
# ---------------------------------------------------------------------------

def extract_file(
    rel_path: str, text: str, config: dict, nodes: dict, edges: list,
) -> None:
    """Parse *text* and emit ROS2 nodes / edges for"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    node_bases = config.get("node_bases", [])
    calls_cfg = config.get("calls", {})
    dyn: dict[str, int] = {}
    module = _module_for(rel_path)
    consumed: list[ast.ClassDef] = []

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        is_node, lifecycle = False, False
        for base in node.bases:
            ok, life = base_is_ros_node(base, node_bases)
            if ok:
                is_node, lifecycle = True, lifecycle or life
        if not is_node:
            continue
        qualname = f"{module}.{node.name}"
        nid = f"ros_node:{qualname}"
        runtime_name = extract_runtime_name(node)
        _topo._upsert_node(
            nodes, nid=nid, qualname=qualname, rel_path=rel_path,
            lifecycle=lifecycle, runtime_name=runtime_name,
        )
        _scan_calls(
            node.body, owner_nid=nid, owner_qn=qualname,
            calls_cfg=calls_cfg, nodes=nodes, edges=edges,
            rel_path=rel_path, dyn=dyn,
        )
        consumed.append(node)

    # File-scope pass: re-scan everything that isn't inside a consumed
    # class so module-level / free-function calls still bind to a
    # caller sentinel.
    file_scope = [n for n in tree.body if n not in consumed]
    if not file_scope:
        return
    needs_file_caller = False
    for stmt in file_scope:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call):
                name = _callee_name(sub)
                if name and name in calls_cfg:
                    needs_file_caller = True
                    break
        if needs_file_caller:
            break
    if not needs_file_caller:
        return
    caller_nid = file_caller(nodes, rel_path)
    _scan_calls(
        file_scope, owner_nid=caller_nid, owner_qn=module,
        calls_cfg=calls_cfg, nodes=nodes, edges=edges,
        rel_path=rel_path, dyn=dyn,
    )
