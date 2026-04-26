"""Helpers for the ``unused_skill`` audit check.

Static-graph correct, low-signal in practice: many real repos activate
skills via shared instruction surfaces (AGENTS.md, project conventions)
rather than explicit ``uses_skill`` edges. ``instruction_bodies`` returns
the lowercased text of all agent and instruction files so the audit can
suppress findings for skills mentioned in those surfaces.

``text_mentions_skill`` performs the actual suppression check. It uses a
word-boundary regex rather than a raw substring so short or common skill
names ('test', 'init', 'plan') don't get falsely suppressed by
incidental substrings inside larger words ('attestation', 'initialize',
'plantation') -- which would silence real orphans.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def instruction_bodies(
    assets: list[dict[str, Any]], root: Path | None,
) -> list[str]:
    """Return lowercased file bodies of all agent and instruction assets.

    Returns an empty list when *root* is None or no asset has a path.
    Files that fail to read are skipped silently — the goal is to err
    on the side of suppressing noise, not to surface I/O errors.
    """
    if root is None:
        return []
    bodies: list[str] = []
    seen: set[str] = set()
    for asset in assets:
        if asset["type"] not in {"agent", "instruction"}:
            continue
        path = asset.get("path") or ""
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            bodies.append((root / path).read_text(
                encoding="utf-8", errors="ignore",
            ).lower())
        except OSError:
            continue
    return bodies


def text_mentions_skill(name: str, bodies: list[str]) -> bool:
    """Return True if *name* appears as a whole word in any body.

    Word-bounded match (``\\b<name>\\b``, case-insensitive). Skill names
    are ``re.escape``-ed so hyphens and other regex metacharacters are
    treated literally. Empty *name* returns False so callers don't need
    to special-case it.
    """
    if not name:
        return False
    pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    return any(pattern.search(body) for body in bodies)
