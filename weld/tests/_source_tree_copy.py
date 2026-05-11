"""Shared helper -- copy a bounded subset of the weld source tree.

This module exists so that wheel-build / pip-install smoke tests can each
take their own hermetic copy of the package source without

* duplicating the helper implementation, and
* dragging the full ``weld/`` tree (~10 MB, dominated by
  ``weld/tests/`` fixtures) into every temp dir.

Design (see bd 6h8b for the planning brief):

* Caller passes an **explicit allowlist** of subpath names under *src*.
  Anything not in the allowlist is not copied. This is the inverse of the
  pre-refactor approach that used ``shutil.ignore_patterns(...)`` -- a
  denylist that quietly grew over time and still copied tens of MB of
  test fixtures.
* Directory entries copy via :func:`shutil.copytree`; file entries copy
  via :func:`shutil.copy2` (metadata-preserving, deliberately not
  :func:`shutil.copy`).
* Names that do not exist under *src* are silently skipped. This lets
  the default allowlist carry an *optional* ``.weld`` entry without
  forcing every consumer to branch on ``Path.exists()``.

The helper is importable from Python tests and is also runnable as a
script (``python -m weld.tests._source_tree_copy SRC DEST weld pyproject.toml``)
so shell tests can stage a hermetic copy without depending on ``rsync``
availability in the Bazel build environment -- the deliberate reason
the earlier fw90 attempt used ``cp -rL`` rather than ``rsync``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Sequence

# Default allowlist (literal per planning brief, bd 6h8b):
# ``weld`` is the package source directory itself, ``pyproject.toml`` is
# the build metadata, and ``.weld`` carries the local graph + workspace
# config if the repo has been initialized. ``.weld`` is silently skipped
# when absent.
#
# This default fits a *publish-clone-style* layout where the package dir
# is a sibling of pyproject.toml. In this repo, pyproject.toml lives
# **inside** ``weld/`` and tests/ also lives inside ``weld/`` (~6 MB),
# so the two production consumers below build their own allowlist via
# :func:`wheel_build_allowlist` rather than relying on this default.
DEFAULT_ALLOWLIST: tuple[str, ...] = ("weld", "pyproject.toml", ".weld")

# Names under :func:`wheel_build_allowlist`'s *src* that pip wheel does
# not need and that would otherwise inflate the per-test copy footprint.
# Kept as a small, named constant rather than a regex so a future
# addition is obvious in a diff.
_WHEEL_BUILD_EXCLUSIONS: frozenset[str] = frozenset({
    "tests",       # ~6 MB of fixtures; not a wheel-installed package.
    "__pycache__", # dev artifact.
})


def wheel_build_allowlist(src: Path) -> tuple[str, ...]:
    """Return the allowlist of *src* children needed to build a wheel.

    Use this when *src* is the weld package source directory in this
    repo (i.e., ``REPO_ROOT/weld``). It enumerates the top-level entries
    of *src* and drops a small named set (``tests``, ``__pycache__``)
    that pip wheel never needs. Anything else under *src* -- modules,
    subpackages, ``pyproject.toml``, ``README.md`` -- is included.

    The result is sorted for deterministic test output and stable
    diffs in any debug logging that prints the allowlist.
    """
    return tuple(sorted(
        name for name in os.listdir(src)
        if name not in _WHEEL_BUILD_EXCLUSIONS
    ))


def copy_weld_source(
    src: Path,
    dest: Path,
    allowlist: Sequence[str] | None = None,
) -> None:
    """Copy each *allowlist* child of *src* into *dest*.

    Parameters
    ----------
    src:
        Source root. Children of this directory are addressed by name
        through *allowlist*.
    dest:
        Destination root. Created (and any missing parents) if it does
        not already exist.
    allowlist:
        Iterable of subpath names directly under *src* to copy. If
        ``None`` (the default), uses :data:`DEFAULT_ALLOWLIST`.

    Behavior
    --------
    * Directory entries copy recursively via :func:`shutil.copytree`.
    * File entries copy via :func:`shutil.copy2` (metadata-preserving).
    * Names that do not exist under *src* are silently skipped so the
      default ``.weld`` entry can be conditional and callers can pass
      "union of what I might need" without branching on filesystem
      state.
    * Symlinks and non-regular files are copied with the same semantics
      as :func:`shutil.copytree` / :func:`shutil.copy2` (no manual
      handling beyond what those primitives provide).
    """
    names = tuple(DEFAULT_ALLOWLIST if allowlist is None else allowlist)
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        entry = src / name
        target = dest / name
        if entry.is_dir():
            # ``dirs_exist_ok=False`` matches the pre-refactor behavior:
            # we expect dest to be freshly created per test. If a caller
            # ever reuses dest deliberately, they can pre-clean.
            shutil.copytree(entry, target)
        elif entry.is_file():
            shutil.copy2(entry, target)
        # else: missing entry -- silently skip (supports optional .weld).


def _main(argv: Sequence[str]) -> int:
    """CLI entry point: ``python -m weld.tests._source_tree_copy``.

    Lets shell tests stage a copy without depending on ``rsync``
    availability in the Bazel build environment. Format:

        python -m weld.tests._source_tree_copy <src> <dest> [name ...]

    With no trailing names, falls back to :data:`DEFAULT_ALLOWLIST`.
    """
    parser = argparse.ArgumentParser(
        prog="weld.tests._source_tree_copy",
        description=(
            "Copy a bounded subset of a source tree using an explicit "
            "allowlist of child names. Used by weld test infrastructure "
            "to stage hermetic wheel-build copies."
        ),
    )
    parser.add_argument("src", type=Path, help="Source root.")
    parser.add_argument("dest", type=Path, help="Destination root.")
    parser.add_argument(
        "names", nargs="*",
        help=(
            "Subpath names under <src> to copy. Empty list uses "
            "DEFAULT_ALLOWLIST."
        ),
    )
    ns = parser.parse_args(list(argv))
    allowlist = ns.names if ns.names else None
    copy_weld_source(ns.src, ns.dest, allowlist=allowlist)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
