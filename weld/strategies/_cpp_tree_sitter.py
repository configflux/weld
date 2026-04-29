"""C++ enrichments for the shared tree-sitter strategy."""

from __future__ import annotations

from pathlib import Path
import re

_MAIN_DEFINITION_RE = re.compile(
    r"\b(?:int|auto)\s+(?:[A-Za-z_][A-Za-z0-9_:]*\s+)*"
    r"(?:w?WinMain|main)\s*\([^;{]*\)[^{;]*\{",
    re.MULTILINE | re.DOTALL,
)
_STARTUP_EXPORTS = {"main", "WinMain", "wWinMain"}


def enrich_file_node(
    nodes: dict[str, dict],
    edges: list[dict],
    file_node_id: str,
    node_props: dict,
    symbols: dict[str, list[str]],
    source_text: str,
    source_strategy: str,
) -> None:
    """Add C++ startup entrypoint and runtime host nodes."""
    rel_path = str(node_props.get("file") or "")
    if is_startup_source(rel_path, source_text, symbols):
        _add_startup_nodes(nodes, edges, rel_path, source_text, source_strategy)


def is_startup_source(
    rel_path: str,
    source_text: str,
    symbols: dict[str, list[str]],
) -> bool:
    """Return True when a C++ source file defines the process entrypoint."""
    path = Path(rel_path)
    if path.suffix.lower() not in {".c", ".cc", ".cpp", ".cxx"}:
        return False
    exports = set(symbols.get("exports", []))
    return bool(exports & _STARTUP_EXPORTS and _MAIN_DEFINITION_RE.search(source_text))


def _add_startup_nodes(
    nodes: dict[str, dict],
    edges: list[dict],
    rel_path: str,
    source_text: str,
    source_strategy: str,
) -> None:
    base = Path(rel_path).with_suffix("").as_posix()
    entrypoint_id = f"entrypoint:{base}"
    boundary_id = f"boundary:{base}:process"
    kind, framework = _startup_kind(source_text)
    nodes.setdefault(
        entrypoint_id,
        {
            "type": "entrypoint",
            "label": Path(rel_path).stem,
            "props": {
                "file": rel_path,
                "kind": kind,
                "framework": framework,
                "language": "cpp",
                "source_strategy": source_strategy,
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["implementation"],
                "description": (
                    "C++ runtime startup entrypoint for application execution flow."
                ),
            },
        },
    )
    nodes.setdefault(
        boundary_id,
        {
            "type": "boundary",
            "label": f"{Path(rel_path).stem} process",
            "props": {
                "file": rel_path,
                "kind": "runtime_process",
                "framework": framework,
                "language": "cpp",
                "source_strategy": source_strategy,
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["implementation"],
                "description": "C++ runtime process boundary that starts the application.",
            },
        },
    )
    edges.append(_edge(boundary_id, entrypoint_id, "exposes", source_strategy))
    service_id = _owning_service_id(rel_path)
    if service_id:
        nodes.setdefault(service_id, _service_node(service_id, source_strategy))
        edges.append(_edge(service_id, entrypoint_id, "contains", source_strategy))
        edges.append(_edge(service_id, boundary_id, "contains", source_strategy))


def _startup_kind(source_text: str) -> tuple[str, str]:
    if "rclcpp::init" in source_text or "#include <rclcpp" in source_text:
        return "ros2_node", "ros2"
    if "grpc::ServerBuilder" in source_text or "#include <grpcpp" in source_text:
        return "server", "grpc"
    if "boost::asio" in source_text or "#include <boost/asio" in source_text:
        return "server", "boost-asio"
    return "main", "cpp"


def _owning_service_id(rel_path: str) -> str | None:
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0] == "services":
        return f"service:{parts[1]}"
    return None


def _service_node(service_id: str, source_strategy: str) -> dict:
    service_name = service_id.split(":", 1)[1]
    return {
        "type": "service",
        "label": f"{service_name} service",
        "props": {
            "language": "cpp",
            "source_strategy": source_strategy,
            "authority": "derived",
            "confidence": "inferred",
            "roles": ["implementation"],
            "description": (
                "Runtime service containing C++ startup and process boundaries."
            ),
        },
    }


def _edge(src: str, dst: str, edge_type: str, source_strategy: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": edge_type,
        "props": {
            "source_strategy": source_strategy,
            "confidence": "inferred",
        },
    }
