"""The bounded interior-file walk behind the directory-COPY bridge.

``COPY ./app /service/app`` resolves to an on-disk *directory*. ADR 0045's
Layer C2 emits ``file:<dir> --contains--> file:<dir>/<child>`` for every
regular interior file, because without those edges a reverse-BFS from
``app/lib.py`` cannot reach the dockerfile: the only contains-edge out of the
dockerfile is to ``file:app``, and nothing links ``file:app/lib.py`` back to
it.

Split out of :mod:`weld.strategies.dockerfile` (bd bz5w9) rather than inlined
there: it is self-contained, has one caller, and its bounds -- the reasons a
walk rooted at a COPY source cannot run away -- are a paragraph in their own
right.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._helpers import is_excluded_dir_name

__all__ = ["walk_dir_children"]


def walk_dir_children(abs_dir: Path, root_resolved: Path) -> list[str]:
    """Return sorted repo-relative posix paths for files under *abs_dir*.

    *root_resolved* must be the caller's already-resolved discovery
    root; passing it in avoids re-running ``Path.resolve()`` once per
    walk in a loop.

    Bounds:

    * skips symlinks (file or dir) -- they would let the walk escape the
      repo root in surprising ways and the dockerfile build wouldn't
      have followed them anyway without an explicit ``COPY --link``.
    * honours :func:`is_excluded_dir_name` so vendored ``node_modules``
      / ``__pycache__`` / ``.git`` trees never balloon the contains-edge
      count.
    * yields only files whose resolved path stays strictly under *root*
      (defensive; symlink skip above already covers the common escape
      vector).
    * deterministic: results are returned in lexicographic order so
      golden bytes stay stable across filesystems.
    """
    children: list[str] = []
    # Manual recursion so we can prune excluded directories cheaply
    # without paying ``rglob`` cost on a vendored ``node_modules`` tree.
    stack: list[Path] = [abs_dir]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            name = entry.name
            if entry.is_dir():
                if is_excluded_dir_name(name):
                    continue
                stack.append(entry)
                continue
            if not entry.is_file():
                continue
            try:
                resolved = entry.resolve()
                rel = resolved.relative_to(root_resolved)
            except (OSError, ValueError):
                continue
            children.append(rel.as_posix())
    children.sort()
    return children
