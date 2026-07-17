"""Broken-file-reference diagnostics for Agent Graph assets.

Extracted from ``agent_graph_metadata.py`` (which sat at the 400-line cap) so
future changes to the parsers or the reference-resolution logic have headroom.
Behavior is unchanged: given a discovered asset's file references, flag the
ones whose targets do not exist on disk, honoring the gitignored-regenerable-
runtime-artifact exclusion (ADR 0076 Mode A).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weld.agent_graph_metadata_utils import (
    AgentGraphReference,
    diagnostic,
    weld_ignored_runtime_refs,
)


def broken_file_diagnostics(
    root: Path,
    source_rel: str,
    refs: list[AgentGraphReference] | tuple[AgentGraphReference, ...],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    ignored_runtime = weld_ignored_runtime_refs(root)
    for reference in refs:
        if reference.target_type != "file":
            continue
        target = reference.target_path or reference.target_name
        if _resolve_file(root, source_rel, target) is None:
            # A reference to one of this repo's gitignored, regenerable weld
            # runtime artifacts (e.g. .weld/graph.json, ADR 0076 Mode A) is a
            # legitimate mention, not a broken reference: it is simply absent
            # in a checkout that has not run `wd discover` yet. Do not flag it.
            if target.strip().removeprefix("./") in ignored_runtime:
                continue
            diagnostics.append(diagnostic(
                "agent_graph_broken_reference",
                source_rel,
                f"Referenced file does not exist: {reference.target_name}",
                line=reference.line,
                reference=reference.target_name,
                raw=reference.raw,
            ))
    return diagnostics


def _resolve_file(root: Path, source_rel: str, target: str) -> str | None:
    candidates = []
    if not target.startswith("/"):
        candidates.append(root / target)
        candidates.append((root / source_rel).parent / target)
    for candidate in candidates:
        try:
            rel = candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if (root / rel).is_file():
            return rel
    return None
