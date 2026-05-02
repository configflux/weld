"""Deterministic graph closure for source-backed language nodes."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_STRATEGY = "graph_closure"
_UNRESOLVED_PREFIX = "symbol:unresolved:"
_CODE_EXTS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".cs", ".java",
    ".cpp", ".cc", ".cxx", ".c", ".h", ".hh", ".hpp", ".hxx",
)
_LANG_BY_EXT = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "cpp",
    ".h": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
}
_CONTAINED_TYPES = frozenset([
    "symbol", "rpc", "channel", "ros_node", "ros_topic",
    "ros_service", "ros_action", "ros_parameter", "ros_interface",
])
_SAFE_ID_RE = re.compile(r"[^0-9A-Za-z_.:/-]+")


def close_graph(nodes: dict[str, dict], edges: list[dict]) -> None:
    """Add deterministic closure nodes/edges in-place."""
    path_index = _path_index(nodes)
    _link_source_backed_nodes(nodes, edges, path_index)
    module_index = _module_index(nodes, path_index)
    _link_imports(nodes, edges, path_index, module_index)
    _decorate_call_edges(nodes, edges)


def _path_index(nodes: dict[str, dict]) -> dict[str, str]:
    by_path: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        if node.get("type") != "file":
            continue
        rel_path = _node_file(node)
        if rel_path:
            by_path.setdefault(rel_path, []).append(node_id)
    result: dict[str, str] = {}
    for rel_path, ids in by_path.items():
        result[rel_path] = sorted(ids)[0]
    return result


def _module_index(nodes: dict[str, dict], path_index: dict[str, str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for rel_path, node_id in path_index.items():
        path = PurePosixPath(rel_path)
        ext = path.suffix.lower()
        if ext == ".py":
            _index_python_module(index, rel_path, node_id)
        elif ext in {".ts", ".tsx", ".js", ".jsx"}:
            _index_path_module(index, rel_path, node_id)
        elif ext in {".go", ".rs", ".java", ".cs"}:
            _index_path_module(index, rel_path, node_id)
        elif ext in {".cpp", ".cc", ".cxx", ".c", ".h", ".hh", ".hpp", ".hxx"}:
            _index_path_module(index, rel_path, node_id)
    for node_id, node in nodes.items():
        props = node.get("props") or {}
        for key in ("module", "namespace", "namespaces", "packages"):
            values = props.get(key)
            if isinstance(values, str) and values:
                index.setdefault(values, node_id)
            elif isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and value:
                        index.setdefault(value, node_id)
        packages = [v for v in props.get("packages", []) if isinstance(v, str)]
        exports = [v for v in props.get("exports", []) if isinstance(v, str)]
        for package in packages:
            for export in exports:
                index.setdefault(f"{package}.{export}", node_id)
    return index


def _index_python_module(index: dict[str, str], rel_path: str, node_id: str) -> None:
    path = PurePosixPath(rel_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if parts:
        index.setdefault(".".join(parts), node_id)
        index.setdefault("/".join(parts), node_id)


def _index_path_module(index: dict[str, str], rel_path: str, node_id: str) -> None:
    path = PurePosixPath(rel_path)
    stem_path = path.with_suffix("").as_posix()
    index.setdefault(stem_path, node_id)
    index.setdefault(stem_path.replace("/", "."), node_id)
    if path.stem == "index":
        parent = path.parent.as_posix()
        if parent != ".":
            index.setdefault(parent, node_id)
            index.setdefault(parent.replace("/", "."), node_id)


def _link_source_backed_nodes(
    nodes: dict[str, dict], edges: list[dict], path_index: dict[str, str],
) -> None:
    for node_id, node in list(nodes.items()):
        if node.get("type") not in _CONTAINED_TYPES:
            continue
        if node_id.startswith(_UNRESOLVED_PREFIX):
            continue
        rel_path = _node_file(node)
        if not rel_path:
            continue
        file_id = _ensure_file_anchor(nodes, path_index, rel_path, node)
        if file_id == node_id:
            continue
        _add_edge(edges, file_id, node_id, "contains", {
            "source_strategy": _STRATEGY,
            "confidence": "definite",
        })


def _link_imports(
    nodes: dict[str, dict],
    edges: list[dict],
    path_index: dict[str, str],
    module_index: dict[str, str],
) -> None:
    for node_id, node in list(nodes.items()):
        props = node.get("props") or {}
        imports = props.get("imports_from")
        if not isinstance(imports, list) or not imports:
            continue
        rel_path = _node_file(node)
        source_id = (
            node_id if node.get("type") == "file" else
            _ensure_file_anchor(nodes, path_index, rel_path, node) if rel_path else node_id
        )
        language = _language_for(node, rel_path)
        for raw in sorted({str(value) for value in imports if str(value).strip()}):
            normalized = _normalize_import(raw)
            if not normalized:
                continue
            target_id, resolution = _resolve_import(
                normalized, rel_path, language, path_index, module_index,
            )
            if target_id is None:
                package_name = _external_package_name(normalized, language)
                target_id = _ensure_package_node(nodes, package_name, language)
                resolution = "external"
            if target_id == source_id:
                continue
            _add_edge(edges, source_id, target_id, "depends_on", {
                "source_strategy": _STRATEGY,
                "confidence": "definite" if resolution != "external" else "inferred",
                "import_name": raw,
                "normalized_import": normalized,
                "language": language,
                "resolution": resolution,
            })


def _resolve_import(
    name: str,
    source_file: str,
    language: str,
    path_index: dict[str, str],
    module_index: dict[str, str],
) -> tuple[str | None, str]:
    if name.startswith(".") or "/" in name or _looks_like_file_import(name, language):
        target = _resolve_path_like_import(name, source_file, path_index)
        if target:
            return target, "local_path"
    for module_name in _module_candidates(name, language, source_file):
        if module_name in module_index:
            return module_index[module_name], "local_module"
    return None, "external"


def _resolve_path_like_import(
    name: str, source_file: str, path_index: dict[str, str],
) -> str | None:
    base = PurePosixPath(source_file).parent if source_file else PurePosixPath(".")
    raw = PurePosixPath(name)
    candidate = raw if raw.is_absolute() else base / raw
    rel = _clean_posix(candidate.as_posix())
    checks = [rel]
    if not PurePosixPath(rel).suffix:
        checks.extend(f"{rel}{ext}" for ext in _CODE_EXTS)
        checks.extend(f"{rel}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx"))
        checks.extend(f"{rel}/__init__.py" for _ in (0,))
    for item in checks:
        if item in path_index:
            return path_index[item]
    return None


def _decorate_call_edges(nodes: dict[str, dict], edges: list[dict]) -> None:
    for edge in edges:
        if edge.get("type") != "calls":
            continue
        props = edge.setdefault("props", {})
        target_id = str(edge.get("to") or "")
        resolved = not target_id.startswith(_UNRESOLVED_PREFIX)
        props.setdefault("resolved", resolved)
        props.setdefault("resolution", "resolved" if resolved else "unresolved")
        props.setdefault("raw", _raw_callee(target_id, nodes.get(target_id)))
        provenance = props.setdefault("provenance", {})
        if not isinstance(provenance, dict):
            provenance = {}
            props["provenance"] = provenance
        source = nodes.get(str(edge.get("from") or ""))
        source_props = source.get("props") if isinstance(source, dict) else {}
        if isinstance(source_props, dict):
            if source_props.get("file"):
                provenance.setdefault("file", source_props["file"])
            if isinstance(source_props.get("line"), int):
                provenance.setdefault("line", source_props["line"])


def _ensure_file_anchor(
    nodes: dict[str, dict],
    path_index: dict[str, str],
    rel_path: str,
    source_node: dict,
) -> str:
    if rel_path in path_index:
        return path_index[rel_path]
    node_id = _file_anchor_id(rel_path, nodes)
    language = _language_for(source_node, rel_path)
    nodes[node_id] = {
        "type": "file",
        "label": PurePosixPath(rel_path).stem,
        "props": {
            "file": rel_path,
            "language": language,
            "source_strategy": _STRATEGY,
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
        },
    }
    path_index[rel_path] = node_id
    return node_id


def _ensure_package_node(nodes: dict[str, dict], name: str, language: str) -> str:
    base_lang = _base_language(language)
    node_id = f"package:{base_lang}:{_slug(name)}"
    nodes.setdefault(node_id, {
        "type": "package",
        "label": name,
        "props": {
            "name": name,
            "language": base_lang,
            "external": True,
            "source_strategy": _STRATEGY,
            "authority": "external",
            "confidence": "inferred",
        },
    })
    return node_id


def _file_anchor_id(rel_path: str, nodes: dict[str, dict]) -> str:
    base = f"file:{PurePosixPath(rel_path).with_suffix('').as_posix()}"
    node_id = base
    counter = 2
    while node_id in nodes:
        counter += 1
        node_id = f"{base}:{counter}"
    return node_id


def _node_file(node: dict) -> str:
    props = node.get("props") or {}
    value = props.get("file")
    return str(value) if isinstance(value, str) and value else ""


def _language_for(node: dict, rel_path: str) -> str:
    props = node.get("props") or {}
    lang = props.get("language")
    if isinstance(lang, str) and lang:
        return _base_language(lang)
    return _LANG_BY_EXT.get(PurePosixPath(rel_path).suffix.lower(), "unknown")


def _base_language(language: str) -> str:
    if language == "python_ros2":
        return "python"
    if language == "cpp_ros2":
        return "cpp"
    return language


def _normalize_import(raw: str) -> str:
    value = raw.strip().strip("\"'`<>")
    if not value:
        return ""
    if value.startswith(("./", "../")):
        return value
    return _clean_posix(value)


def _module_key(name: str, language: str) -> str:
    value = name
    if language == "python":
        return value.replace("/", ".")
    if language in {"typescript", "go", "rust"}:
        return value.strip("/")
    if language == "java" and value.endswith(".*"):
        return value[:-2]
    return value


def _module_candidates(name: str, language: str, source_file: str) -> list[str]:
    value = _module_key(name, language)
    candidates = [value, value.replace("/", ".")]
    if language == "rust":
        rust = value.replace("::", ".").strip(".")
        for prefix in ("crate.", "self.", "super."):
            if rust.startswith(prefix):
                rust = rust[len(prefix):]
        candidates.extend([rust, rust.replace(".", "/")])
        parts = PurePosixPath(source_file).parts
        if parts and rust:
            candidates.extend([f"{parts[0]}.{rust}", f"{parts[0]}/{rust}"])
    return [c for c in dict.fromkeys(candidates) if c]


def _external_package_name(name: str, language: str) -> str:
    if language == "java":
        value = name[:-2] if name.endswith(".*") else name
        package, dot, _class = value.rpartition(".")
        return package if dot else value
    return name


def _looks_like_file_import(name: str, language: str) -> bool:
    if language not in {"cpp", "cpp_ros2"}:
        return False
    return PurePosixPath(name).suffix.lower() in _CODE_EXTS


def _clean_posix(value: str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(value).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _raw_callee(target_id: str, target_node: dict | None) -> str:
    if target_id.startswith(_UNRESOLVED_PREFIX):
        return target_id[len(_UNRESOLVED_PREFIX):]
    props = target_node.get("props") if isinstance(target_node, dict) else {}
    if isinstance(props, dict) and isinstance(props.get("qualname"), str):
        return str(props["qualname"]).rsplit(".", 1)[-1]
    return target_id.rsplit(":", 1)[-1]


def _slug(value: str) -> str:
    cleaned = _SAFE_ID_RE.sub("_", value.strip()).strip("._:/-")
    return cleaned or "unknown"


def _add_edge(edges: list[dict], src: str, dst: str, edge_type: str, props: dict) -> None:
    edges.append({"from": src, "to": dst, "type": edge_type, "props": props})
