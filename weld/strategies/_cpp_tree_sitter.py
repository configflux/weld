"""C++ enrichments for the shared tree-sitter strategy."""

from __future__ import annotations

from pathlib import Path
import re

from weld.strategies._cpp_inherits import (
    emit_inheritance_edges,
    record_inheritance,
)
from weld.strategies._cpp_symbol_records import extract_symbol_records

_MAIN_DEFINITION_RE = re.compile(
    r"\b(?:int|auto)\s+(?:[A-Za-z_][A-Za-z0-9_:]*\s+)*"
    r"(?:w?WinMain|main)\s*\([^;{]*\)[^{;]*\{",
    re.MULTILINE | re.DOTALL,
)
_STARTUP_EXPORTS = {"main", "WinMain", "wWinMain"}

# ADR 0062: well-known path conventions for single-include / single-header
# C++ amalgamations. Detected at file-node minting time so the ranker can
# tiebreak the import surface ahead of modular peers on single-token
# navigation queries. See docs/adrs/0062-cpp-amalgamation-file-rank-boost.md.
_AMALGAMATION_DIR_NAMES = {"single_include", "single_header", "amalgamated"}


def build_caches(root: Path, language: str) -> dict | None:
    """Seed the per-discover cpp accumulator (bd bou8, ADR 0064 criterion 2).

    Returns ``None`` for non-cpp languages so the shared tree-sitter
    orchestrator can treat the result uniformly with the other
    per-language cache builders. The ``inheritance_records`` list is
    consumed by :func:`finalise` after every file has been visited.
    """
    if language != "cpp":
        return None
    return {"inheritance_records": []}


def enrich_file_node(
    nodes: dict[str, dict],
    edges: list[dict],
    file_node_id: str,
    node_props: dict,
    symbols: dict[str, list[str]],
    source_text: str,
    source_strategy: str,
    *,
    inheritance_records: list | None = None,
) -> None:
    """Add C++ startup entrypoint, runtime host nodes, and ADR 0057
    Wave 2 symbol records (forward-decl vs definition + template
    metadata).

    Per ADR 0062 also stamps ``props.amalgamation = True`` on file nodes
    whose path matches one of the well-known single-include / single-
    header conventions so the ranker can boost them on single-token
    navigation queries.

    ADR 0064 criterion 2 / bd bou8: when ``inheritance_records`` is
    provided (the shared accumulator seeded by :func:`build_caches`),
    every ``class Derived : public Base`` clause in *source_text* is
    staged for the post-pass that emits the ``inherits`` edge from the
    derived-class symbol node. The accumulator is consumed by
    :func:`finalise` after the tree-sitter file loop completes so
    project-wide same-name resolution sees every header.
    """
    rel_path = str(node_props.get("file") or "")
    _stamp_symbol_records(node_props, symbols, source_text)
    if is_amalgamation_path(rel_path):
        node_props["amalgamation"] = True
    if is_startup_source(rel_path, source_text, symbols):
        _add_startup_nodes(nodes, edges, rel_path, source_text, source_strategy)
    if inheritance_records is not None:
        record_inheritance(
            inheritance_records,
            rel_path=rel_path,
            source_text=source_text,
        )


def finalise(
    nodes: dict[str, dict],
    edges: list[dict],
    enricher_caches: dict | None,
    source_strategy: str,
) -> None:
    """Run C++ post-pass after the tree-sitter file loop completes.

    Resolves every staged ``(derived, base)`` record against the
    project-wide class index and emits one ``inherits`` edge per
    record, originating at the derived-class symbol node (ADR 0064
    criterion 2 / bd bou8). External / unresolved bases land on
    ``symbol:unresolved:<short>`` sentinels minted lazily so the graph
    stays referentially closed.
    """
    if not enricher_caches:
        return
    records = enricher_caches.get("inheritance_records") or []
    if not records:
        return
    emit_inheritance_edges(nodes, edges, records, source_strategy)


def is_amalgamation_path(rel_path: str) -> bool:
    """Return True when *rel_path* matches a single-include amalgamation.

    The whitelist (per ADR 0062) is intentionally small:

    * any path segment is one of ``single_include``, ``single_header``,
      ``amalgamated``;
    * the second-to-last path segment is ``dist`` and the last segment
      starts with ``single_`` (e.g. ``dist/single_header/foo.hpp``) or
      contains ``amalgam``;
    * the file basename contains ``amalgamation`` or ``amalgamated``
      (e.g. ``vendor/sqlite3.amalgamation.c``).

    Empty ``rel_path`` returns False so the caller can treat the result
    as a hard signal without a None branch.
    """
    if not rel_path:
        return False
    parts = Path(rel_path).parts
    if not parts:
        return False
    if any(p in _AMALGAMATION_DIR_NAMES for p in parts):
        return True
    basename = parts[-1].lower()
    if "amalgamation" in basename or "amalgamated" in basename:
        return True
    if len(parts) >= 2 and parts[-2] == "dist":
        last = parts[-1].lower()
        if last.startswith("single_") or "amalgam" in last:
            return True
    return False


def _stamp_symbol_records(
    node_props: dict,
    symbols: dict[str, list[str]],
    source_text: str,
) -> None:
    """Stamp ``props.symbol_records`` with structured per-export info.

    Drops records whose ``kind`` is None (the classifier could not
    decide). A non-empty list is the only signal the consumer needs;
    the empty list is omitted so callers can use ``"symbol_records" in
    node_props`` as a presence check.
    """
    exports = list(symbols.get("exports", []))
    if not exports:
        return
    classes = list(symbols.get("classes", []))
    records = extract_symbol_records(source_text, exports, classes)
    classified = [r for r in records if r.get("kind") is not None]
    if classified:
        node_props["symbol_records"] = classified


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
