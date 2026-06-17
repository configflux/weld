"""Loud warning for a discover run that resolves to zero sources.

A single-repo ``wd discover`` whose ``.weld/discover.yaml`` is absent (the
empty-config default) or resolves to ``sources: []`` silently produces a
0-node graph and exits 0. When the tree is actually full of recognized
source files, that is almost always an un-initialised checkout -- the
operator forgot ``wd init`` -- and the silent empty graph then lets
``tools/tier_check.py`` report vacuous passes against nothing
(bd cdzq).

This module emits one ``[weld] warning:`` line (the same prefix the
tree-sitter grammar-miss path uses, so operators can grep for either
source uniformly) when, and only when, zero sources are configured *and*
the tree contains files whose extensions ``wd init`` recognizes. A
genuinely empty or non-code directory stays silent so the warning keeps
its signal.

The recognized-file probe reuses ``wd init``'s own detector
(:func:`weld.init_detect.scan_files` + :func:`detect_languages`) so the
"is there code here?" judgement cannot drift from what ``wd init`` would
actually scaffold.

Kept in a dedicated module so :mod:`weld.discover` stays under the
repo's 400-line cap and so the guard can be unit-tested in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["no_sources_warning", "warn_if_no_sources"]

_MESSAGE = "no sources configured; run 'wd init' (discover produced a 0-node graph)"


def no_sources_warning(root: Path, sources: list) -> str | None:
    """Return the no-sources warning text, or ``None`` when not applicable.

    Fires only when *sources* is empty (nothing to extract) *and* the
    tree under *root* holds at least one file whose extension ``wd init``
    maps to a language. Returns ``None`` otherwise -- a configured corpus
    or a non-code tree never warns.

    The detector import is local so a discovery run that never hits the
    empty-config path pays no import cost.
    """
    if sources:
        return None
    from weld.init_detect import detect_languages, scan_files

    try:
        files = scan_files(root)
    except OSError:
        # If the tree cannot be walked we have no evidence of source to
        # protect; stay silent rather than emit a misleading warning.
        return None
    if not detect_languages(files):
        return None
    return _MESSAGE


def warn_if_no_sources(root: Path, sources: list) -> None:
    """Print the no-sources warning to stderr when applicable.

    Thin wrapper over :func:`no_sources_warning` so :mod:`weld.discover`
    adds a single call. Uses the shared ``[weld] warning:`` prefix.
    """
    msg = no_sources_warning(root, sources)
    if msg is not None:
        print(f"[weld] warning: {msg}", file=sys.stderr)
