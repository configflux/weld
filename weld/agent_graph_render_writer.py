"""Render canonical Agent Graph assets to per-platform copies (ADR 0026).

This module implements the dry-run-by-default write contract specified in
ADR 0026. It enumerates ``canonical -> rendered`` pairs from the same
``.weld/agents.yaml`` sidecar that the discovery pipeline already
understands, produces deterministic rendered content with a provenance
header, and either reports a unified diff (default) or atomically writes
the result (opt-in ``--write``, with ``--force`` required to clobber an
existing rendered file whose bytes differ).

The module is read-only unless the caller invokes :func:`apply_plan`.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weld._yaml import parse_yaml
from weld.agent_graph_authority import SIDECAR_PATH
from weld.workspace_state import atomic_write_text

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)

_HASH_COMMENT_EXTS = {
    ".yaml", ".yml", ".toml", ".sh", ".bash", ".zsh", ".fish",
    ".py", ".rb", ".pl", ".tf", ".cfg", ".ini", ".conf", ".env",
}
_HTML_COMMENT_EXTS = {
    ".md", ".mdx", ".markdown", ".html", ".htm", ".xml",
}


@dataclass(frozen=True)
class RenderPair:
    """One canonical-to-rendered mapping declared in ``.weld/agents.yaml``."""

    canonical: str
    rendered: str
    name: str


@dataclass(frozen=True)
class PlannedRender:
    """Result of planning one render pair without touching the filesystem."""

    pair: RenderPair
    action: str            # "create" | "update" | "skip" | "error"
    reason: str             # short tag explaining action
    rendered_text: str      # what would be written; empty on error
    existing_text: str      # current bytes on disk; empty if missing
    diff: str               # unified diff between existing and rendered


def collect_pairs(root: Path) -> list[RenderPair]:
    """Enumerate canonical -> rendered pairs from ``.weld/agents.yaml``.

    Only mappings declared in the sidecar are considered. Frontmatter
    ``renders:`` on canonical files is intentionally not treated as a
    render target by this command; we keep the source of truth aligned
    with the existing static discovery that already drives the audit's
    description-level ``rendered_copy_drift`` check.
    """
    sidecar = root / SIDECAR_PATH
    if not sidecar.is_file():
        return []
    try:
        parsed = parse_yaml(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []
    if not isinstance(parsed, dict):
        return []
    agents = parsed.get("agents")
    if not isinstance(agents, dict):
        return []
    pairs: list[RenderPair] = []
    for name, config in sorted(agents.items(), key=lambda item: str(item[0])):
        if not isinstance(config, dict):
            continue
        canonical = _clean_path(config.get("canonical"))
        if not canonical:
            continue
        for rendered in _render_paths(config.get("renders")):
            pairs.append(RenderPair(
                canonical=canonical,
                rendered=rendered,
                name=str(name),
            ))
    return pairs


def render_text(canonical_path: str, rendered_path: str, canonical_body: str) -> str:
    """Return the rendered file contents for a canonical body.

    Strips any leading frontmatter from *canonical_body* (Markdown front-
    matter delimited by ``---`` lines) and prefixes a deterministic
    provenance header chosen by *rendered_path*'s extension.
    """
    body = _strip_frontmatter(canonical_body)
    header = _provenance_header(canonical_path, _ext_from_path(rendered_path))
    return _provenance_with(header, body)


def plan_pair(root: Path, pair: RenderPair) -> PlannedRender:
    """Plan one render pair without writing anything."""
    if not _within_root(root, pair.canonical) or not _within_root(root, pair.rendered):
        # Refuse paths that escape the repo root (e.g. ``../../etc/foo``).
        # Trusted as a defense in depth on top of the existing ``.weld/``
        # config trust boundary documented in ADR 0024.
        return PlannedRender(
            pair=pair,
            action="error",
            reason="path_escapes_root",
            rendered_text="",
            existing_text="",
            diff="",
        )
    canonical_path = root / pair.canonical
    if not canonical_path.is_file():
        return PlannedRender(
            pair=pair,
            action="error",
            reason="missing_canonical",
            rendered_text="",
            existing_text="",
            diff="",
        )
    canonical_body = canonical_path.read_text(encoding="utf-8")
    rendered_text = render_text(pair.canonical, pair.rendered, canonical_body)
    target_path = root / pair.rendered
    if target_path.is_file():
        try:
            existing = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            existing = ""
        if existing == rendered_text:
            action, reason = "skip", "in_sync"
        else:
            action, reason = "update", "drift"
    else:
        existing = ""
        action, reason = "create", "missing_target"
    diff = _unified_diff(pair.canonical, pair.rendered, existing, rendered_text)
    return PlannedRender(
        pair=pair,
        action=action,
        reason=reason,
        rendered_text=rendered_text,
        existing_text=existing,
        diff=diff,
    )


def plan_all(root: Path) -> list[PlannedRender]:
    """Plan every canonical -> rendered pair under *root*."""
    return [plan_pair(root, pair) for pair in collect_pairs(root)]


def apply_plan(
    plan: list[PlannedRender],
    root: Path,
    *,
    force: bool,
) -> tuple[list[PlannedRender], list[dict[str, str]]]:
    """Apply a render plan to disk.

    Returns ``(applied, refusals)``. *applied* is the list of plans whose
    files were written; *refusals* records every pair that could not be
    written (a planning error, or an existing target without ``force``).
    """
    applied: list[PlannedRender] = []
    refusals: list[dict[str, str]] = []
    for entry in plan:
        if entry.action == "error":
            refusals.append({
                "rendered": entry.pair.rendered,
                "canonical": entry.pair.canonical,
                "reason": entry.reason,
            })
            continue
        if entry.action == "skip":
            continue
        if entry.action == "update" and not force:
            refusals.append({
                "rendered": entry.pair.rendered,
                "canonical": entry.pair.canonical,
                "reason": "exists_no_force",
            })
            continue
        target = root / entry.pair.rendered
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, entry.rendered_text)
        applied.append(entry)
    return applied, refusals


def detect_content_drift(root: Path) -> list[dict[str, str]]:
    """Return rendered targets whose on-disk bytes differ from a fresh render.

    Used by ``wd agents audit`` to surface a content-level drift finding
    that complements the description-level ``rendered_copy_drift`` check.
    """
    findings: list[dict[str, str]] = []
    for entry in plan_all(root):
        if entry.action == "update":
            findings.append({
                "canonical": entry.pair.canonical,
                "rendered": entry.pair.rendered,
                "name": entry.pair.name,
            })
    return findings


# --- helpers ---------------------------------------------------------------


def _strip_frontmatter(body: str) -> str:
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return body
    return body[match.end():]


def _ext_from_path(path: str) -> str:
    return Path(path).suffix.lower()


def _provenance_header(canonical_path: str, ext: str) -> tuple[str, str]:
    line_a = f"Generated by Weld from {canonical_path}; do not edit by hand."
    line_b = "Run `wd agents render` to regenerate."
    if ext in _HASH_COMMENT_EXTS:
        return (f"# {line_a}", f"# {line_b}")
    if ext in _HTML_COMMENT_EXTS:
        return (f"<!-- {line_a} -->", f"<!-- {line_b} -->")
    return (f"<!-- {line_a} -->", f"<!-- {line_b} -->")


def _provenance_with(header: tuple[str, str], body: str) -> str:
    line_a, line_b = header
    body_text = body.lstrip("\n")
    return f"{line_a}\n{line_b}\n\n{body_text}"


def _unified_diff(
    canonical: str,
    rendered: str,
    existing: str,
    proposed: str,
) -> str:
    existing_lines = existing.splitlines(keepends=True) if existing else []
    proposed_lines = proposed.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        existing_lines,
        proposed_lines,
        fromfile=f"a/{rendered}",
        tofile=f"b/{rendered} (from {canonical})",
        n=3,
    )
    return "".join(diff_iter)


def _render_paths(value: Any) -> list[str]:
    paths: list[str] = []
    values = value if isinstance(value, list) else [value] if value else []
    for item in values:
        path: Any = None
        if isinstance(item, dict):
            for key in ("path", "file", "target", "render"):
                if key in item:
                    path = item[key]
                    break
        else:
            path = item
        cleaned = _clean_path(path)
        if cleaned and cleaned not in paths:
            paths.append(cleaned)
    return paths


def _clean_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    return cleaned[2:] if cleaned.startswith("./") else cleaned


def _within_root(root: Path, rel_path: str) -> bool:
    """Return True if ``root / rel_path`` resolves under *root*."""
    if not rel_path:
        return False
    try:
        resolved = (root / rel_path).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True
