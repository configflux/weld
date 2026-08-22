"""Strategy: domain-concept nodes from local issue-tracker dogfood-gap items.

Reads a JSON-lines issue store at the path given by ``source['path']``
(relative to the repo root) and emits one ``concept`` node per open issue
that carries the ``weld-dogfood-gap`` label. For each cited repo-relative
file path inside the issue description, a ``relates_to`` edge is added
from the concept node to every plausible spelling of that path's node id
(:func:`weld.strategies._target_ids.target_ids`); the discovery
post-processor drops the spellings that do not resolve. That rule is shared
with ``validator_targets`` rather than restated here: this strategy used to
keep its own copy, that copy predated the ADR 0041 ``file:`` rename, and so
every edge it emitted was swept as dangling and every concept node it minted
was an orphan.

Boundary and safety rules (see ADR 0037):

- Closed issues and issues without the dogfood-gap label are ignored.
- The strategy fails closed: if the issue file is missing, unreadable, or
  contains malformed lines, those rows are skipped silently and the rest
  of discovery proceeds.
- Cited paths are sanitized: absolute paths and paths that resolve
  outside the repo root via ``..`` traversal are dropped before edges
  are emitted, and only paths that exist in the worktree become edges.
- Concept slugs are bounded to ``[a-z0-9-]+`` and capped at 80 chars so a
  hostile issue title cannot produce arbitrary node ids.
- Only the trailing short-id segment of the issue id is stored as a
  prop; the full internal id is never written into the graph.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies._target_ids import target_ids

# Strip a leading "weld dogfood gap:" tag (case-insensitive, optional
# whitespace) from issue titles before slugifying. The label that drives
# this strategy is itself "weld-dogfood-gap"; the matching prefix in the
# title is the convention humans type when reporting one.
_TITLE_TAG_PREFIX = re.compile(r"^\s*weld\s+dogfood\s+gap\s*[:\-]\s*", re.IGNORECASE)

# Bounded, non-backtracking pattern for cited repo-relative file paths.
# Must contain at least one path separator and end in a recognizable
# extension. Bounded to 200 chars per match to prevent pathological
# inputs from doing more work than necessary.
_PATH_PATTERN = re.compile(
    r"\b([A-Za-z0-9_.][A-Za-z0-9_./-]{0,200}"
    r"\.(?:py|md|sh|yaml|yml|toml|json|txt|bzl|bazel))\b"
)

# Recognized label that promotes an issue into a concept node. Single
# source of truth so callers and tests agree.
DOGFOOD_GAP_LABEL = "weld-dogfood-gap"

# Cap for the slugified concept id so hostile or accidentally enormous
# issue titles cannot produce unbounded node ids.
_SLUG_MAX_LEN = 80


def _slugify_concept(title: str) -> str:
    """Reduce an issue title to a bounded, ascii-only slug.

    The leading dogfood-gap tag is stripped first. Non-ascii characters
    are unicode-normalized and dropped (they are useful in human prose
    but not in node ids). Anything that is not lower-case ascii letters,
    digits, or hyphen is collapsed into a single hyphen, leading and
    trailing hyphens are stripped, and the result is truncated.
    """
    stripped = _TITLE_TAG_PREFIX.sub("", title or "").strip()
    # Decompose accents and drop combining marks; keep ascii body only.
    normalized = unicodedata.normalize("NFKD", stripped)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    if not collapsed:
        return "untitled"
    return collapsed[:_SLUG_MAX_LEN]


def _cited_paths(root: Path, text: str) -> list[str]:
    """Return repo-relative file paths cited inside *text*.

    Each match is checked against the repository boundary: absolute
    paths, traversal paths, and paths that do not actually exist in the
    worktree are dropped. The remaining list is deduplicated while
    preserving the order of first appearance so output is deterministic.
    """
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    try:
        root_resolved = root.resolve()
    except OSError:
        return []
    for match in _PATH_PATTERN.finditer(text):
        candidate = match.group(1)
        # Reject absolute paths up front; resolve() would happily handle
        # them but they are never legitimate repo-relative citations.
        if candidate.startswith(("/", "\\")):
            continue
        # Reject obvious traversal before touching the filesystem.
        if ".." in Path(candidate).parts:
            continue
        full = (root / candidate)
        try:
            resolved = full.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        if not resolved.exists():
            continue
        # Normalize separators to posix for stable graph keys.
        rel = candidate.replace("\\", "/")
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return out


def _short_id(issue_id: str) -> str:
    """Return the trailing segment of a hyphen-separated issue id.

    The full id may carry repository-internal identifiers we deliberately
    do not want to leak into the graph (or into anything packaged for
    publication). Storing the trailing segment only keeps the node
    stable across a session while staying audit-safe.
    """
    if not issue_id:
        return ""
    return issue_id.rsplit("-", 1)[-1]


def _iter_issues(path: Path):
    """Yield parsed JSON-line issue objects from *path*.

    Malformed lines are skipped. ``OSError`` reading the file results in
    no yielded items; the caller treats that as an empty result.
    """
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                yield obj


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit concept nodes for open dogfood-gap issues; see module docstring."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    # The deployed location of the issue store is supplied by the
    # discovery config (see .weld/discover.yaml). The strategy itself
    # does not hard-code a default: an unconfigured caller gets an empty
    # result rather than picking up an implicit production path. This
    # keeps the production storage convention out of source as a literal
    # and lets each repository point at whatever location they keep
    # their issue feed under.
    rel = source.get("path")
    if not rel:
        return StrategyResult(nodes, edges, discovered_from)
    full = root / rel
    if not full.is_file():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(rel)

    for issue in _iter_issues(full):
        labels = issue.get("labels") or []
        if not isinstance(labels, list) or DOGFOOD_GAP_LABEL not in labels:
            continue
        status = (issue.get("status") or "").lower()
        if status not in ("open", "in_progress", "ready", "blocked"):
            # Closed/done items are intentionally excluded: the dogfood
            # signal is the unfixed gap, not the historical record.
            continue
        title = issue.get("title") or ""
        slug = _slugify_concept(title)
        nid = f"concept:{slug}"
        # Title collisions just merge into one node — that is the right
        # answer for the same concept reported twice.
        if nid in nodes:
            continue
        priority = issue.get("priority")
        nodes[nid] = {
            "type": "concept",
            "label": slug,
            "props": {
                "description": title,
                "status": status,
                "priority": priority if isinstance(priority, int) else None,
                "bd_short_id": _short_id(issue.get("id") or ""),
                "source_strategy": "concept_from_bd",
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["doc"],
            },
        }
        for cited in _cited_paths(root, issue.get("description") or ""):
            for target in target_ids(cited):
                edges.append(
                    {
                        "from": nid,
                        "to": target,
                        "type": "relates_to",
                        "props": {
                            "source_strategy": "concept_from_bd",
                            "authority": "derived",
                            "confidence": "inferred",
                            # ADR 0074: the issue store this edge was
                            # derived from, never the cited target -- so a
                            # clean-provenance edge into a dirtied target
                            # survives the incremental purge instead of
                            # being dropped by endpoint membership and never
                            # re-minted (the issue store rarely changes
                            # alongside an unrelated cited file; bd 57lra).
                            "provenance": {"file": rel},
                        },
                    }
                )

    return StrategyResult(nodes, edges, discovered_from)
