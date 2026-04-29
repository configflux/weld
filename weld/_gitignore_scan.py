"""Minimal ``.gitignore`` reader for the federation nested-repo scanner.

The federation scanner in :mod:`weld.workspace` must not register a nested
git clone as a polyrepo child when the repo root's ``.gitignore`` already
declares that directory is not part of the repo (for example, the
publish-overlay clone at ``/public/``). Rather than shelling out to
``git check-ignore`` -- which requires git on ``PATH`` and makes the scanner
hard to test in isolation -- this module parses only the repo root's
``.gitignore`` with a deliberately conservative matcher.

Design notes
------------
* Only the root ``.gitignore`` is read. Nested ``.gitignore`` files are not
  honoured here; this is enough for the publish-overlay case and keeps the
  rule predictable.
* Negation (``!pattern``) and glob metacharacters (``*``, ``?``, ``[``) make
  the line ambiguous -- when in doubt we do NOT skip. Over-registering a
  federation child is recoverable; silently dropping a real one is not.
* A pattern only matters if it resolves to an existing directory under the
  root: a gitignore entry that names a non-existent path cannot mask a
  nested ``.git`` directory.

See tracked issue
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["load_root_gitignore_dirs"]


def load_root_gitignore_dirs(root: Path) -> frozenset[Path]:
    """Return absolute dir paths under ``root`` excluded by its ``.gitignore``.

    Supported pattern shapes:

    * ``/foo/`` or ``/foo`` -- anchored at the repo root
    * ``foo/`` -- directory at the repo root (no nested-tree walk)
    * ``foo/bar/`` -- directory at a specific sub-path

    Comments (``#``), blank lines, negation patterns (``!``), and entries
    containing glob metacharacters are skipped.
    """
    gi = root / ".gitignore"
    if not gi.is_file():
        return frozenset()
    try:
        text = gi.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    root_resolved = root.resolve()
    excluded: set[Path] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        # Drop inline trailing comments when preceded by whitespace+#.
        comment_idx = line.find(" #")
        if comment_idx != -1:
            line = line[:comment_idx].strip()
            if not line:
                continue
        # Conservative: bail out on any glob metacharacter.
        if any(c in line for c in "*?["):
            continue
        # Normalise: strip leading slash (anchor marker) and trailing slash.
        rel = line.lstrip("/").rstrip("/")
        if not rel or ".." in rel.split("/"):
            continue
        candidate = (root / rel).resolve()
        # Only record directories that actually live under ``root``.
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            continue
        if candidate.is_dir():
            excluded.add(candidate)
    return frozenset(excluded)
