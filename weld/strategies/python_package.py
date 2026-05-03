"""Strategy: ``package:python:<dotted_name>`` nodes for Python source dirs.

Closes the structural gap that ADR 0041 PR 3 papered over with a glob
allow-list: ``python_module`` emits
``file:`` anchors with outgoing ``contains`` edges (file -> exported
class / function), but no upstream strategy emits ``package:python:* ->
contains -> file:*``. The result was 134 orphan file anchors that the
``file-anchor-symmetry`` rule (ADR 0041 § Layer 3) had to ignore via a
broad allow-list.

This strategy walks each configured ``glob`` and groups matched ``*.py``
files by their containing directory:

- Directories that contain ``__init__.py`` become a real Python package.
  The package's dotted name is derived from the repo-relative directory
  path (``weld`` -> ``package:python:weld``;
  ``weld/strategies`` -> ``package:python:weld.strategies``).
- Directories without ``__init__.py`` (e.g. ``tools/``) are treated as
  flat namespaces. The configured ``package`` value (or, failing that,
  the basename of the directory) becomes the package name. This is the
  synthetic ``package:python:tools`` case the issue calls out.

The strategy emits one ``package`` node per discovered group plus one
``contains`` edge per file. It never emits its own ``file:`` nodes --
``python_module`` and ``python_callgraph`` are still the canonical
authorities for those. The pair-consistency rule therefore does not
need a new pair entry: this strategy is a *parent* layer, not a
member of the python_module/python_callgraph file-set pair.

Determinism: file lists are sorted and the package node is emitted
exactly once per directory, regardless of how many ``*.py`` files
match. The ``contains`` edge list is sorted by destination ID before
emission so repeated runs produce byte-identical graphs (ADR 0012 §3).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld._node_ids import package_id as _canonical_package_id
from weld.glob_match import walk_glob
from weld.strategies._helpers import StrategyResult, should_skip

_STRATEGY = "python_package"


def _dotted_name(rel_dir: str) -> str:
    """Convert a repo-relative POSIX dir path to a dotted package name.

    ``"weld"`` -> ``"weld"``; ``"weld/strategies"`` -> ``"weld.strategies"``;
    ``""`` -> ``""`` (caller must supply a fallback).
    """
    if not rel_dir or rel_dir == ".":
        return ""
    return rel_dir.replace("/", ".")


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit ``package:python:<name>`` nodes plus ``contains`` edges to files.

    Source schema:

    ``glob`` (required)
        Same semantics as ``python_module``: a path glob (``weld/*.py``,
        ``weld/**/*.py``, ``tools/*.py``).
    ``package`` (optional)
        Override the inferred package name. Used for synthetic packages
        like ``tools`` where no ``__init__.py`` exists.
    ``exclude`` (optional)
        List of patterns passed to ``should_skip`` (same semantics as
        the paired ``python_module`` strategy).
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude") or []
    explicit_package = (source.get("package") or "").strip()

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    matched: list[Path] = list(walk_glob(root, pattern, excludes=excludes))
    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # Group files by their containing directory so each directory yields
    # exactly one package node regardless of file count.
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for py in sorted(matched):
        if should_skip(py, excludes, root=root):
            continue
        try:
            rel = py.relative_to(root)
        except ValueError:
            continue
        rel_dir = rel.parent.as_posix()
        if rel_dir == ".":
            rel_dir = ""
        by_dir[rel_dir].append(py)

    for rel_dir in sorted(by_dir.keys()):
        files = by_dir[rel_dir]
        # Derive the package name. Priority:
        #   1. explicit ``package`` from source config (synthetic case)
        #   2. dotted form of the directory if it contains __init__.py
        #   3. dotted form of the directory unconditionally (last resort)
        has_init = any(p.name == "__init__.py" for p in files) or (
            (root / rel_dir / "__init__.py").is_file() if rel_dir else False
        )
        if explicit_package:
            pkg_name = explicit_package
        elif has_init and rel_dir:
            pkg_name = _dotted_name(rel_dir)
        elif rel_dir:
            # No __init__.py and no explicit override: fall back to the
            # dotted dir name. Caller is expected to supply ``package``
            # for non-package dirs (e.g. ``tools``); without it we still
            # emit something deterministic rather than dropping files
            # silently.
            pkg_name = _dotted_name(rel_dir)
        else:
            # Empty rel_dir means files at repo root. Skip; we do not
            # claim a "root" package for the whole repo.
            continue

        if not pkg_name:
            continue

        pkg_nid = _canonical_package_id("python", pkg_name)
        # Idempotent merge: same strategy run may already have populated
        # this node from a different glob entry. Last-write wins on
        # props since both invocations carry the same values.
        nodes[pkg_nid] = {
            "type": "package",
            "label": pkg_name,
            "props": {
                "name": pkg_name,
                "language": "python",
                "dir": rel_dir,
                "source_strategy": _STRATEGY,
                "authority": "derived",
                "confidence": "definite",
                "roles": ["package"],
                "synthetic": not has_init,
            },
        }
        if rel_dir:
            discovered_from.append(rel_dir.rstrip("/") + "/")

        # Sort children by canonical file ID so the edge list is
        # byte-identical across runs.
        children: list[tuple[str, str]] = []
        for py in files:
            rel_path = py.relative_to(root).as_posix()
            file_nid = _canonical_file_id(rel_path)
            children.append((file_nid, rel_path))
        children.sort()

        for file_nid, rel_path in children:
            edges.append(
                {
                    "from": pkg_nid,
                    "to": file_nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": _STRATEGY,
                        "confidence": "definite",
                    },
                }
            )

    return StrategyResult(nodes, edges, sorted(set(discovered_from)))
