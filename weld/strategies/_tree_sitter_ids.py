"""Shared file-id helpers for the tree-sitter strategy cluster.

The :mod:`weld.strategies.tree_sitter` and
:mod:`weld.strategies.typescript_exports` strategies both build
``file:`` node ids from a repo-relative path plus an optional
``id_prefix`` config knob. The ADR 0041 § Layer 1 migration introduced
two near-identical helpers in each strategy (a canonical builder routed
through :func:`weld._node_ids.file_id` and a ``_legacy_*`` builder that
preserves the pre-migration shape under ``aliases``). Hosting them
here keeps each strategy file under the 400-line cap and ensures both
strategies mint the canonical id by the same rule.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id


def legacy_file_node_id(rel_path: str, id_prefix: str) -> str:
    """Return the pre-ADR-0041 ``file:`` id for *rel_path*.

    Recorded under ``aliases`` so external consumers (MCP transcripts,
    sidecar caches) keep resolving for one minor version per the
    ADR 0041 deprecation timeline.
    """
    p = Path(rel_path)
    stem = p.stem
    if id_prefix:
        parts = p.parts
        anchor_idx = None
        for i, part in enumerate(parts):
            if part == id_prefix:
                anchor_idx = i
        if anchor_idx is not None:
            sub_parts = list(parts[anchor_idx + 1:])
            if sub_parts:
                sub_parts[-1] = stem
            else:
                sub_parts = [stem]
            return f"file:{id_prefix}/{'/'.join(sub_parts)}"
        return f"file:{id_prefix}/{stem}"
    return f"file:{stem}"


def canonical_file_node_id(rel_path: str, id_prefix: str) -> str:
    """Return the ADR-0041 canonical ``file:`` id for *rel_path*.

    The id is the full repo-relative POSIX path without extension,
    routed through :func:`weld._node_ids.file_id`. The ``id_prefix``
    parameter is preserved so source entries that anchor children
    under a named scope still segment cleanly.
    """
    if id_prefix:
        parts = Path(rel_path).parts
        anchor_idx = None
        for i, part in enumerate(parts):
            if part == id_prefix:
                anchor_idx = i
        if anchor_idx is not None:
            sub_parts = list(parts[anchor_idx + 1:])
            if sub_parts:
                sub_parts[-1] = Path(sub_parts[-1]).stem
            else:
                sub_parts = [Path(rel_path).stem]
            sub_path = "/".join(sub_parts) if sub_parts else Path(rel_path).stem
            return _canonical_file_id(f"{id_prefix}/{sub_path}")
        return _canonical_file_id(f"{id_prefix}/{Path(rel_path).stem}")
    return _canonical_file_id(rel_path)


__all__ = [
    "legacy_file_node_id",
    "canonical_file_node_id",
]
