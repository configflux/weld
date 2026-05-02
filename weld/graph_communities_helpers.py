"""Stateless helpers for community-detection rendering.

These helpers classify nodes (language, file, hub-eligibility) and compute
ordered count summaries. They are factored out of ``graph_communities.py``
so that module can stay within the repo line-count cap while supporting
the projected-subgraph contract introduced in ADR 0039.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

_LANG_BY_SUFFIX = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".h": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".md": "markdown",
}

_LANG_ALIASES = {
    "c#": "csharp",
    "cs": "csharp",
    "c-sharp": "csharp",
    "c++": "cpp",
    "cplusplus": "cpp",
    "c-plus-plus": "cpp",
    "ts": "typescript",
    "js": "javascript",
}


def language_for(node_id: str, node: Mapping[str, Any]) -> str:
    """Best-effort language for a node, falling back to its file suffix."""
    props = node.get("props") or {}
    for key in ("language", "lang"):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_language(value)
    file_name = file_for(node_id, node)
    suffix = Path(file_name or "").suffix.lower()
    return _LANG_BY_SUFFIX.get(suffix, "unknown")


def normalize_language(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    return _LANG_ALIASES.get(normalized, normalized.replace("-", ""))


def file_for(node_id: str, node: Mapping[str, Any]) -> str:
    """Resolve the source file path associated with a node, if any."""
    props = node.get("props") or {}
    value = props.get("file") or props.get("path")
    if isinstance(value, str) and value:
        return value
    if node.get("type") == "file" and node_id.startswith("file:"):
        return node_id.removeprefix("file:")
    return ""


def is_unresolved_symbol(node_id: str, node: Mapping[str, Any]) -> bool:
    """Identify call-graph artefacts (unresolved symbols) per ADR 0039.

    Such nodes act as universal hubs because the AST resolver could not
    pin them to a specific definition. Excluding them from the projected
    subgraph keeps community detection from collapsing into one mega-cluster.
    """
    return node_id.startswith("symbol:unresolved:") or (
        node.get("type") == "symbol" and (node.get("props") or {}).get("resolved") is False
    )


def inc(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def sorted_counts(counts: Mapping[str, int]) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def dominant(counts: Mapping[str, int]) -> str:
    return next(iter(sorted_counts(counts)), "unknown")


def title_for(
    types: Mapping[str, int],
    languages: Mapping[str, int],
    hubs: list[dict[str, Any]],
) -> str:
    if hubs:
        return f"{dominant(types)} around {hubs[0]['label']}"
    return f"{dominant(languages)} {dominant(types)}"
