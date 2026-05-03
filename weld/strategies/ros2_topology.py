"""Strategy: ROS2 runtime topology extractor — C++ / Python dispatcher.

Emits the ``ros_node`` / ``ros_topic`` / ``ros_service`` /
``ros_action`` / ``ros_parameter`` nodes promised by ADR 0016 plus
``produces`` / ``consumes`` / ``exposes`` / ``configures`` edges by
scanning ``rclcpp`` / ``rclcpp_action`` / ``rclcpp_lifecycle`` call
sites in C++ sources and ``rclpy`` / ``rclpy.lifecycle`` /
``rclpy.action`` call sites in Python sources.

Hard cases — each exercised by ``weld_ros2_topology_cpp_test.py`` and
``weld_ros2_topology_py_test.py`` — include dynamic topic names
(``ros_topic:<dynamic>:<owner>/<n>``), message types resolved against
the ``<pkg>/msg|srv|action/<Name>`` convention (unknown shapes get
``message_type: "<unresolved>"``), composable nodes tagging
``composable: true``, ``LifecycleNode`` subclasses tagging
``lifecycle: true``, super-ctor name calls populating ``runtime_name``,
file-scope calls binding to the layer-1
``symbol:<lang>:<module>:<file>`` sentinel, and ``get_parameter``
without a prior declare still emitting ``declared: false``.

Low-level helpers live in ``weld/strategies/_ros2_cpp.py`` (C++ tokens)
and ``weld/strategies/_ros2_py.py`` (rclpy ``ast`` walker, bd
tracked project) so this dispatcher stays inside the 400-line budget.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld._graph_node_registry import ensure_node
from weld.strategies import _ros2_cpp as _cpp
from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

_STRATEGY = "ros2_topology"
_CPP_EXTS = frozenset({".cpp", ".cc", ".cxx", ".hpp", ".h", ".hh"})
_PY_EXTS = frozenset({".py"})
_DERIVED = {"source_strategy": _STRATEGY, "authority": "derived"}
_CANONICAL = {"source_strategy": _STRATEGY, "authority": "canonical"}

def _file_caller(nodes: dict, rel_path: str) -> str:
    """Create (if needed) and return a ``symbol:cpp:<module>:<file>`` id.

    Routed through ``ensure_node`` per ADR 0041 so two strategies that
    materialize the same file-level caller merge instead of clobbering.
    """
    p = Path(rel_path)
    parts = list(p.parts)
    parts[-1] = p.stem
    module = ".".join(parts) if parts else p.stem
    nid = f"symbol:cpp:{module}:<file>"
    ensure_node(
        nodes, nid, "symbol",
        source_strategy=_STRATEGY, source_path=rel_path, authority="derived",
        props={
            "name": module, "file": rel_path, "module": module,
            "qualname": "<file>", "language": "cpp", "scope": "module",
            "confidence": "inferred", "roles": ["implementation"],
        },
    )
    return nid

def _ensure_iface_sentinel(nodes: dict, iface_nid: str, mt: str) -> None:
    """Create a light ``ros_interface`` sentinel; richer data wins via merge."""
    kind = mt.split("/")[1] if mt and "/" in mt else "msg"
    ensure_node(
        nodes, iface_nid, "ros_interface",
        source_strategy=_STRATEGY, source_path=None, authority="external",
        props={
            "name": mt, "confidence": "inferred",
            "roles": ["config"], "interface_kind": kind,
        },
    )

def _ensure_topic(
    nodes: dict, edges: list, *, topic_name: str, mt: str,
    iface_nid: str | None, rel_path: str, dynamic: bool,
) -> str:
    nid = f"ros_topic:{topic_name}"
    ensure_node(
        nodes, nid, "ros_topic",
        source_strategy=_STRATEGY, source_path=rel_path, authority="derived",
        props={
            "name": topic_name, "file": rel_path, "message_type": mt,
            "dynamic": dynamic, "roles": ["implementation"],
            "confidence": "inferred" if dynamic else "definite",
        },
    )
    props = nodes[nid]["props"]
    if props.get("message_type") == "<unresolved>" and mt != "<unresolved>":
        props["message_type"] = mt
    if iface_nid is not None:
        _ensure_iface_sentinel(nodes, iface_nid, mt)
        edges.append({
            "from": nid, "to": iface_nid, "type": "implements",
            "props": {
                "source_strategy": _STRATEGY, "confidence": "inferred",
                "kind": "ros_message",
            },
        })
    return nid

def _ensure_typed(
    nodes: dict, *, kind: str, name: str, typed: str, rel_path: str,
) -> str:
    """Shared ``ros_service`` / ``ros_action`` helper (merge-safe)."""
    nid = f"ros_{kind}:{name}"
    type_prop = "service_type" if kind == "service" else "action_type"
    ensure_node(
        nodes, nid, f"ros_{kind}",
        source_strategy=_STRATEGY, source_path=rel_path, authority="derived",
        props={
            "name": name, "file": rel_path, type_prop: typed,
            "confidence": "definite", "roles": ["implementation"],
        },
    )
    props = nodes[nid]["props"]
    if props.get(type_prop) == "<unresolved>" and typed != "<unresolved>":
        props[type_prop] = typed
    return nid

def _ensure_param(
    nodes: dict, *, owner: str, name: str, ptype: str | None,
    declared: bool, rel_path: str,
) -> str:
    nid = f"ros_parameter:{owner}/{name}"
    incoming: dict = {
        "name": name, "file": rel_path, "declared": declared,
        "confidence": "definite" if declared else "inferred",
        "roles": ["config"],
    }
    if ptype:
        incoming["parameter_type"] = ptype
    ensure_node(
        nodes, nid, "ros_parameter",
        source_strategy=_STRATEGY, source_path=rel_path, authority="derived",
        props=incoming,
    )
    # Promote ``declared`` regardless of which side won the merge: a later
    # ``declare_parameter`` call must always upgrade an inferred sentinel.
    existing = nodes[nid]["props"]
    if declared and not existing.get("declared"):
        existing["declared"] = True
        existing["confidence"] = "definite"
        if ptype:
            existing["parameter_type"] = ptype
    return nid

def _append_edge(
    edges: list, src: str, dst: str, type_: str, rel_path: str, callee: str,
) -> None:
    edges.append({
        "from": src, "to": dst, "type": type_,
        "props": {
            "source_strategy": _STRATEGY, "confidence": "inferred",
            "file": rel_path, "callee": callee,
        },
    })

def _emit_topic_call(
    spec_role: str, first: str, templated: str, owner_nid: str,
    owner_qn: str, nodes: dict, edges: list, rel_path: str,
    dyn: dict[str, int], callee: str,
) -> None:
    mt, iface_nid = _cpp.resolve_interface(templated)
    literal = _cpp.extract_string_literal(first)
    if literal is not None:
        topic_name, dynamic = literal, False
    else:
        dyn[owner_qn] = dyn.get(owner_qn, 0) + 1
        topic_name = f"<dynamic>:{owner_qn}/{dyn[owner_qn]}"
        dynamic = True
    topic_nid = _ensure_topic(
        nodes, edges, topic_name=topic_name, mt=mt, iface_nid=iface_nid,
        rel_path=rel_path, dynamic=dynamic,
    )
    _append_edge(edges, owner_nid, topic_nid, spec_role, rel_path, callee)

def _scan_calls(
    src: str, *, owner_nid: str, owner_qn: str, calls_cfg: dict,
    nodes: dict, edges: list, rel_path: str, dyn: dict[str, int],
) -> None:
    """Walk *src* and emit ROS2 nodes / edges for every recognised call."""
    for m in _cpp.CALL_RE.finditer(src):
        callee = m.group(1)
        if callee not in calls_cfg:
            continue
        spec = calls_cfg[callee]
        kind, role = spec.get("kind"), spec.get("role")
        if _cpp.qualifier_namespace(src, m.start()) == "rclcpp_action":
            kind = "action"
            role = "exposes" if callee == "create_server" else "consumes"
        open_paren = m.end() - 1
        close_paren = _cpp.find_matching_paren(src, open_paren)
        if close_paren <= open_paren:
            continue
        args = _cpp.split_top_level_args(src[open_paren + 1:close_paren])
        if not args:
            continue
        templated = (m.group(2) or "").strip()
        first = args[0]
        if kind == "topic":
            _emit_topic_call(
                role, first, templated, owner_nid, owner_qn,
                nodes, edges, rel_path, dyn, callee,
            )
            continue
        if kind in {"service", "action"}:
            # Services take the name first; rclcpp_action::create_server
            # takes ``this`` first, so scan the first three args.
            scan_limit = 3 if kind == "action" else 1
            literal: str | None = None
            for a in args[:scan_limit]:
                lit = _cpp.extract_string_literal(a)
                if lit is not None:
                    literal = lit
                    break
            if literal is None:
                continue
            mt, _iface = _cpp.resolve_interface(templated)
            dst_nid = _ensure_typed(
                nodes, kind=kind, name=literal, typed=mt, rel_path=rel_path,
            )
            _append_edge(edges, owner_nid, dst_nid, role, rel_path, callee)
            continue
        if kind == "parameter":
            literal = _cpp.extract_string_literal(first)
            if literal is None:
                continue
            param_nid = _ensure_param(
                nodes, owner=owner_qn, name=literal,
                ptype=(templated or None),
                declared=bool(spec.get("declared", False)),
                rel_path=rel_path,
            )
            _append_edge(
                edges, owner_nid, param_nid, "configures", rel_path, callee,
            )

def _upsert_node(
    nodes: dict, *, nid: str, qualname: str, rel_path: str,
    lifecycle: bool, runtime_name: str | None,
) -> None:
    """Create or upgrade a ``ros_node`` entry.

    Layer 4 (``ros2_cmake``) may have registered a light hint node;
    this upgrades the props in place rather than clobbering.  Shared
    between the C++ and Python halves.
    """
    if nid in nodes:
        existing = nodes[nid]["props"]
        existing.setdefault("file", rel_path)
        if lifecycle:
            existing["lifecycle"] = True
        if runtime_name is not None:
            existing.setdefault("runtime_name", runtime_name)
        existing["confidence"] = "definite"
        existing["authority"] = "canonical"
        existing["source_strategy"] = _STRATEGY
        existing.setdefault("class_name", qualname)
        return
    props: dict = {
        "file": rel_path, "class_name": qualname,
        "lifecycle": lifecycle, "composable": False,
        "confidence": "definite", "roles": ["implementation"],
        **_CANONICAL,
    }
    if runtime_name is not None:
        props["runtime_name"] = runtime_name
    nodes[nid] = {"type": "ros_node", "label": qualname, "props": props}

def _handle_composable(
    src: str, config: dict, rel_path: str, nodes: dict,
) -> None:
    macro = config.get("composable_macro", "RCLCPP_COMPONENTS_REGISTER_NODE")
    pattern = rf"{re.escape(macro)}\s*\(\s*([A-Za-z_][A-Za-z0-9_:]*)\s*\)"
    for m in re.finditer(pattern, src):
        cls = m.group(1)
        nid = f"ros_node:{cls}"
        if nid in nodes:
            nodes[nid]["props"]["composable"] = True
            continue
        nodes[nid] = {
            "type": "ros_node", "label": cls,
            "props": {
                "file": rel_path, "class_name": cls,
                "composable": True, "lifecycle": False,
                "confidence": "inferred", "roles": ["implementation"],
                **_CANONICAL,
            },
        }

def _extract_cpp_file(
    rel_path: str, text: str, config: dict, nodes: dict, edges: list,
) -> None:
    src = _cpp.strip_comments(text)
    dyn: dict[str, int] = {}
    consumed: list[tuple[int, int]] = []
    node_bases = config.get("node_bases", [])
    calls_cfg = config.get("calls", {})
    for m in _cpp.CLASS_RE.finditer(src):
        is_node, lifecycle = _cpp.base_is_ros_node(m.group(2), node_bases)
        if not is_node:
            continue
        brace_open = src.find("{", m.end() - 1)
        if brace_open < 0:
            continue
        brace_close = _cpp.find_matching_brace(src, brace_open)
        ns_prefix = _cpp.enclosing_namespace(src, m.start())
        qualname = f"{ns_prefix}{m.group(1)}"
        nid = f"ros_node:{qualname}"
        body = src[brace_open + 1:brace_close]
        runtime_name = _cpp.extract_runtime_name(body)
        _upsert_node(
            nodes, nid=nid, qualname=qualname, rel_path=rel_path,
            lifecycle=lifecycle, runtime_name=runtime_name,
        )
        _scan_calls(
            body, owner_nid=nid, owner_qn=qualname, calls_cfg=calls_cfg,
            nodes=nodes, edges=edges, rel_path=rel_path, dyn=dyn,
        )
        consumed.append((brace_open, brace_close))
    # File-scope pass: splice out consumed class bodies and re-scan.
    parts: list[str] = []
    last = 0
    for start, end in sorted(consumed):
        parts.append(src[last:start])
        last = end + 1
    parts.append(src[last:])
    file_scope = "".join(parts)
    if any(callee in file_scope for callee in calls_cfg):
        file_caller = _file_caller(nodes, rel_path)
        _scan_calls(
            file_scope, owner_nid=file_caller,
            owner_qn=Path(rel_path).stem, calls_cfg=calls_cfg,
            nodes=nodes, edges=edges, rel_path=rel_path, dyn=dyn,
        )
    _handle_composable(src, config, rel_path, nodes)

def _resolve_sources(root: Path, pattern: str) -> list[Path]:
    if "**" in pattern:
        return filter_glob_results(root, sorted(root.glob(pattern)))
    parent = (root / pattern).parent
    if not parent.is_dir():
        return []
    return filter_glob_results(
        root, sorted(parent.glob(Path(pattern).name))
    )

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract the ROS2 runtime topology from C++ and Python source files."""
    from weld.strategies import _ros2_py as _py  # lazy: avoids import cycle
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []
    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)
    cpp_config: dict | None = None
    py_config: dict | None = None
    for path in _resolve_sources(root, pattern):
        if not path.is_file() or should_skip(path, excludes):
            continue
        suffix = path.suffix
        if suffix not in _CPP_EXTS and suffix not in _PY_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_path = str(path.relative_to(root))
        if suffix in _CPP_EXTS:
            if cpp_config is None:
                cpp_config = _cpp.load_cpp_ros2_config()
            if not cpp_config:
                continue
            discovered_from.append(rel_path)
            _extract_cpp_file(rel_path, text, cpp_config, nodes, edges)
        else:
            if py_config is None:
                py_config = _py.load_py_ros2_config()
            if not py_config:
                continue
            discovered_from.append(rel_path)
            _py.extract_file(rel_path, text, py_config, nodes, edges)
    return StrategyResult(nodes, edges, discovered_from)
