"""Strategy: ROS2 launch graph extractor (layer 6 of cortex-cpp-ros2).

Extracts the canonical
``LaunchDescription([Node(...)])`` shape from ``*.launch.py`` files and
emits a ``ros_node`` entry per Node entry with its runtime name,
package, and executable, plus the edges that tie each node back to its
owning ``ros_package`` and to any literal parameter dicts.

The explicit non-goal from the epic is general Python evaluation: we
walk the stdlib ``ast`` module only.  Non-literal kwargs (runtime
expressions, variables, function calls) are silently skipped so that
a single dynamic field does not destroy launch coverage for the rest
of the file.  Unparseable files are skipped without aborting the run.

Emitted vocabulary (all schema v3):

- ``ros_node:<package>/<name>`` — keyed by the Node's package +
  runtime name.  Props include ``package``, ``executable``,
  ``runtime_name``, ``file``, and the standard
  ``source_strategy`` / ``authority`` / ``confidence`` / ``roles``
  triple.  Launch is a derived authority — the C++/Python topology
  layers remain canonical for the class-name view of each node.
- ``ros_package:<package>`` — light external sentinel upserted per
  launch entry so launch files standalone still produce a join point.
- ``ros_parameter:<runtime_name>/<key>`` — one per dict-literal key in
  ``parameters=[{...}]``.  ``declared`` is always True for launch
  parameters because they are live when the launch file runs.
- ``file:<relpath>`` — light launch-file sentinel used as the
  orchestrator for ``orchestrates`` edges.

Edges:

- ``file:<launch> -> ros_node:<...>``  type=``orchestrates``
- ``ros_node:<...> -> ros_package:<pkg>``  type=``depends_on``
- ``ros_node:<...> -> ros_parameter:<...>``  type=``configures``
- ``ros_node:<...> -> ros_topic:<remap_to>``  type=``relates_to``
  (one per literal ``(from, to)`` tuple in ``remappings=[...]``; the
  edge props carry ``kind: "remap"`` and ``remap_from: <from_name>``)

See ADR ``docs/adrs/0016-kg-ros2-knowledge-graph.md`` and the layer-6
notes in ``cortex/strategies/ros2_topology.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from cortex.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

_STRATEGY = "ros2_launch"
_DERIVED = {"source_strategy": _STRATEGY, "authority": "derived"}
_EXTERNAL = {"source_strategy": _STRATEGY, "authority": "external"}
_CANONICAL = {"source_strategy": _STRATEGY, "authority": "canonical"}

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _string_literal(node: ast.AST | None) -> str | None:
    """Return a Python string literal value or ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None

def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    """Return the keyword argument AST for *name* on *call*, or None."""
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None

def _callee_tail(call: ast.Call) -> str | None:
    """Return the trailing attribute/name for a ``Call``'s func."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None

def _find_launch_list(tree: ast.Module) -> ast.List | None:
    """Return the first ``LaunchDescription([...])`` list literal found.

    The extractor walks the whole module because many real launch files
    wrap the description in a helper function, a conditional block, or
    an opaque decorator.  Returning the first match is enough for the
    canonical shape — anything more exotic falls outside the documented
    scope of this layer.
    """
    for sub in ast.walk(tree):
        if not isinstance(sub, ast.Call):
            continue
        if _callee_tail(sub) != "LaunchDescription":
            continue
        if not sub.args:
            continue
        first = sub.args[0]
        if isinstance(first, ast.List):
            return first
    return None

def _iter_node_calls(desc_list: ast.List) -> list[ast.Call]:
    """Yield every ``Node(...)`` Call directly inside ``desc_list``."""
    out: list[ast.Call] = []
    for elt in desc_list.elts:
        if isinstance(elt, ast.Call) and _callee_tail(elt) == "Node":
            out.append(elt)
    return out

# ---------------------------------------------------------------------------
# Node / edge emission
# ---------------------------------------------------------------------------

def _ensure_package(nodes: dict, pkg: str) -> str:
    nid = f"ros_package:{pkg}"
    nodes.setdefault(nid, {
        "type": "ros_package", "label": pkg,
        "props": {
            "confidence": "inferred", "roles": ["config"], **_EXTERNAL,
        },
    })
    return nid

def _ensure_file_sentinel(nodes: dict, rel_path: str) -> str:
    nid = f"file:{rel_path}"
    nodes.setdefault(nid, {
        "type": "file", "label": Path(rel_path).name,
        "props": {
            "file": rel_path,
            "confidence": "definite", "roles": ["config"], **_CANONICAL,
        },
    })
    return nid

def _emit_ros_node(
    nodes: dict, *, pkg: str, name: str, executable: str | None,
    rel_path: str,
) -> str:
    nid = f"ros_node:{pkg}/{name}"
    props: dict = {
        "file": rel_path, "package": pkg, "runtime_name": name,
        "confidence": "definite", "roles": ["implementation"],
        **_DERIVED,
    }
    if executable is not None:
        props["executable"] = executable
    nodes.setdefault(nid, {
        "type": "ros_node", "label": name, "props": props,
    })
    # If the node already exists (e.g. a later layer upserted it with
    # richer info), only fill in the launch-specific fields we own.
    existing = nodes[nid]["props"]
    existing.setdefault("package", pkg)
    existing.setdefault("runtime_name", name)
    if executable is not None:
        existing.setdefault("executable", executable)
    existing.setdefault("file", rel_path)
    return nid

def _emit_remappings(
    nodes: dict, edges: list, *, owner_nid: str,
    remap_node: ast.AST | None, rel_path: str,
) -> None:
    """Scan ``remappings=[(from, to), ...]`` for literal tuples.

    The launch-time remap rewires the topic that *owner_nid* talks to.
    We can represent that faithfully with a weak ``relates_to`` edge to
    a ``ros_topic`` sentinel whose id is the effective runtime name,
    tagged with ``remap_from`` on the edge so downstream consumers can
    reconstruct the original topic.  Non-literal tuples are skipped.
    """
    if not isinstance(remap_node, ast.List):
        return
    for entry in remap_node.elts:
        if not isinstance(entry, (ast.Tuple, ast.List)):
            continue
        if len(entry.elts) != 2:
            continue
        src = _string_literal(entry.elts[0])
        dst = _string_literal(entry.elts[1])
        if src is None or dst is None:
            continue
        topic_nid = f"ros_topic:{dst}"
        nodes.setdefault(topic_nid, {
            "type": "ros_topic", "label": dst,
            "props": {
                "file": rel_path, "name": dst,
                "message_type": "<unresolved>", "dynamic": False,
                "confidence": "inferred", "roles": ["config"],
                **_EXTERNAL,
            },
        })
        edges.append({
            "from": owner_nid, "to": topic_nid, "type": "relates_to",
            "props": {
                "source_strategy": _STRATEGY,
                "confidence": "definite",
                "kind": "remap",
                "remap_from": src,
                "file": rel_path,
            },
        })

def _emit_parameters(
    nodes: dict, edges: list, *, owner_nid: str, owner_name: str,
    params_node: ast.AST | None, rel_path: str,
) -> None:
    """Scan ``parameters=[...]`` for dict literals and emit configures."""
    if not isinstance(params_node, ast.List):
        return
    for entry in params_node.elts:
        if not isinstance(entry, ast.Dict):
            # Non-dict entries (file paths, variables, function calls)
            # are intentionally skipped — the epic forbids general
            # evaluation.
            continue
        for key_node in entry.keys:
            key = _string_literal(key_node)
            if key is None:
                continue
            param_nid = f"ros_parameter:{owner_name}/{key}"
            nodes.setdefault(param_nid, {
                "type": "ros_parameter", "label": key,
                "props": {
                    "file": rel_path, "name": key, "declared": True,
                    "confidence": "definite", "roles": ["config"],
                    **_DERIVED,
                },
            })
            # Promote to declared=True if a topology layer left it false.
            props = nodes[param_nid]["props"]
            if not props.get("declared"):
                props["declared"] = True
                props["confidence"] = "definite"
            edges.append({
                "from": owner_nid, "to": param_nid, "type": "configures",
                "props": {
                    "source_strategy": _STRATEGY,
                    "confidence": "definite",
                    "file": rel_path,
                },
            })

def _handle_node_call(
    call: ast.Call, *, file_nid: str, rel_path: str,
    nodes: dict, edges: list,
) -> None:
    pkg = _string_literal(_kwarg(call, "package"))
    runtime_name = _string_literal(_kwarg(call, "name"))
    executable = _string_literal(_kwarg(call, "executable"))
    # A Node entry is keyable iff we have a package and a human-readable
    # identifier — prefer the runtime name, fall back to the executable.
    if pkg is None:
        return
    key_name = runtime_name or executable
    if key_name is None:
        return
    if runtime_name is None:
        # Synthesise a runtime name from the executable so downstream
        # consumers always have a stable label.
        runtime_name = executable or key_name
    ros_node_nid = _emit_ros_node(
        nodes, pkg=pkg, name=key_name, executable=executable,
        rel_path=rel_path,
    )
    _ensure_package(nodes, pkg)
    edges.append({
        "from": file_nid, "to": ros_node_nid, "type": "orchestrates",
        "props": {
            "source_strategy": _STRATEGY,
            "confidence": "definite",
            "file": rel_path,
        },
    })
    edges.append({
        "from": ros_node_nid, "to": f"ros_package:{pkg}",
        "type": "depends_on",
        "props": {
            "source_strategy": _STRATEGY,
            "confidence": "definite",
            "kind": "launch_ros_node",
            "file": rel_path,
        },
    })
    _emit_parameters(
        nodes, edges, owner_nid=ros_node_nid, owner_name=key_name,
        params_node=_kwarg(call, "parameters"), rel_path=rel_path,
    )
    _emit_remappings(
        nodes, edges, owner_nid=ros_node_nid,
        remap_node=_kwarg(call, "remappings"), rel_path=rel_path,
    )

# ---------------------------------------------------------------------------
# File / strategy entrypoint
# ---------------------------------------------------------------------------

def _extract_launch_file(
    rel_path: str, text: str, nodes: dict, edges: list,
) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    desc_list = _find_launch_list(tree)
    if desc_list is None:
        return False
    file_nid = _ensure_file_sentinel(nodes, rel_path)
    for call in _iter_node_calls(desc_list):
        _handle_node_call(
            call, file_nid=file_nid, rel_path=rel_path,
            nodes=nodes, edges=edges,
        )
    return True

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
    """Extract ROS2 launch nodes and edges from ``*.launch.py`` files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []
    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)
    for path in _resolve_sources(root, pattern):
        if not path.is_file() or should_skip(path, excludes):
            continue
        if not path.name.endswith(".launch.py"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_path = str(path.relative_to(root))
        if _extract_launch_file(rel_path, text, nodes, edges):
            discovered_from.append(rel_path)
    return StrategyResult(nodes, edges, discovered_from)
