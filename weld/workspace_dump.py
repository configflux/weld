"""Canonical, deterministic ``workspaces.yaml`` serializer.

The dumper is the ground truth for any round-trip: the same
:class:`~weld.workspace.WorkspaceConfig` always produces byte-identical
output. It is split out of :mod:`weld.workspace` (which re-exports
:func:`dump_workspaces_yaml`) so the schema/loader/validator module stays
within the line-count cap. The ``TYPE_CHECKING``-only import below points at
the dependency-free :mod:`weld._workspace_schema` leaf rather than
``weld.workspace`` -- pointing it back at ``workspace.py`` was still a
real edge in the discovery graph (the AST walker does not special-case a
``TYPE_CHECKING`` guard) and completed a 3-member import cycle with
``workspace_scan.py`` (bd 5038-zw6w4, ADR 0130 disposition #14).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from weld._workspace_schema import WorkspaceConfig

__all__ = ["dump_workspaces_yaml"]


def _yaml_scalar(value: object) -> str:
    """Emit a YAML scalar. Quotes strings that contain YAML-special chars."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    s = str(value)
    if s == "":
        return '""'
    # Characters that benefit from quoting in block scalars.
    unsafe = set(": #[]{},&*!|>'\"%@`")
    if any(c in s for c in unsafe) or s[0] in " -?":
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _emit_inline_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(_yaml_scalar(x) for x in items) + "]"


def dump_workspaces_yaml(cfg: WorkspaceConfig, path: Path | str) -> None:
    """Write ``cfg`` to ``path`` as canonical, deterministic YAML.

    The output is the ground truth for any round-trip; the same input config
    always produces byte-identical output. Children are emitted in the order
    stored on the config (callers typically sort before dumping), and each
    child's ``tags`` are emitted in sorted key order so hand-reordering the
    config does not create spurious diffs.
    """
    lines: list[str] = []
    lines.append(f"version: {cfg.version}")
    lines.append("scan:")
    lines.append(f"  max_depth: {cfg.scan.max_depth}")
    lines.append(
        f"  respect_gitignore: {_yaml_scalar(cfg.scan.respect_gitignore)}",
    )
    lines.append(f"  exclude_paths: {_emit_inline_list(cfg.scan.exclude_paths)}")
    if cfg.children:
        lines.append("children:")
        for child in cfg.children:
            lines.append(f"  - name: {_yaml_scalar(child.name)}")
            lines.append(f"    path: {_yaml_scalar(child.path)}")
            if child.tags:
                lines.append("    tags:")
                for key in sorted(child.tags):
                    lines.append(f"      {key}: {_yaml_scalar(child.tags[key])}")
            if child.remote:
                lines.append(f"    remote: {_yaml_scalar(child.remote)}")
    else:
        lines.append("children: []")
    lines.append(f"cross_repo_strategies: {_emit_inline_list(cfg.cross_repo_strategies)}")
    text = "\n".join(lines) + "\n"
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
