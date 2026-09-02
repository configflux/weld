"""Next.js detection for ``wd init`` (ADR 0142 D4, bd lrnx1.7).

Every other framework :func:`weld.init_detect.detect_frameworks` knows is
found by reading an *import line* -- ``import express``, ``use axum::``,
``"github.com/gin-gonic/gin"``. Next.js cannot be, and that is a fact about
the framework rather than a shortcut taken here: an app-router handler
(``app/api/orders/route.ts``) imports nothing from ``next`` at all. It exports
a function named ``GET`` in a file named ``route.ts``, and the framework does
the rest. A repository can be a whole Next.js application without one
``from "next"`` anywhere in it.

So detection keys on what such a repository *does* always have -- the two
markers ``create-next-app`` writes:

* a ``next.config.{js,mjs,cjs,ts,mts,cts}`` beside the app, and
* a ``next`` entry in a ``package.json``'s ``dependencies`` (or
  ``devDependencies``: a Next app that is only built in CI legitimately keeps
  it there).

Either alone is enough; the first hit in sorted order is reported, so the path
``wd init`` prints is stable across runs.

Bounded like the import scan it sits beside (ADR 0027): manifests are read
until the first hit and at most :data:`_MAX_MANIFESTS` of them, so a monorepo
with thousands of workspace packages and no Next.js pays a bounded cost. The
config-file check is a name match and reads nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

#: What ``detect_frameworks`` reports, and what
#: :func:`weld._init_framework_sources._add_ts_js_framework_sources` matches
#: on. Spelled the way the project spells itself.
NEXT_FRAMEWORK: str = "Next.js"

#: The strategy the framework maps to -- :mod:`weld.strategies.next`.
NEXT_STRATEGY: str = "next"

#: ``next.config`` in every extension Next.js loads it from.
_CONFIG_STEM: str = "next.config"
_CONFIG_SUFFIXES: frozenset[str] = frozenset(
    {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"}
)

#: The manifest key, and the two dependency tables it may appear under.
_PACKAGE_NAME: str = "next"
_DEPENDENCY_TABLES: tuple[str, ...] = ("dependencies", "devDependencies")

#: Manifest read cap, mirroring
#: :data:`weld._init_framework_scan._MAX_FILES_PER_LANG`. One positive hit is
#: sufficient, so the cap cannot change the answer on a repository whose
#: Next.js app is anywhere near its usual place.
_MAX_MANIFESTS: int = 1000


def _is_next_config(path: Path) -> bool:
    return path.stem == _CONFIG_STEM and path.suffix in _CONFIG_SUFFIXES


def _declares_next_dependency(path: Path) -> bool:
    """Return True when *path* is a ``package.json`` that depends on ``next``.

    A manifest that will not read or will not parse is not a detection: an
    unreadable file says nothing about the framework, and guessing from a
    broken one would wire a strategy on no evidence.
    """
    try:
        manifest = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return False
    if not isinstance(manifest, dict):
        return False
    for table in _DEPENDENCY_TABLES:
        deps = manifest.get(table)
        if isinstance(deps, dict) and _PACKAGE_NAME in deps:
            return True
    return False


def detect_next_framework(
    root: Path, files: list[Path],
) -> list[tuple[str, str, str]]:
    """Return ``[(framework, strategy, path)]`` when Next.js is present.

    The list is empty or holds exactly one entry -- the shape
    :func:`weld.init_detect.detect_frameworks` returns, so its caller folds
    this in without a second code path. The reported path is the marker that
    proved it, which is what ``wd init`` prints back to the user.
    """
    configs = sorted(
        path for path in files if _is_next_config(path)
    )
    manifests = sorted(
        path for path in files if path.name == "package.json"
    )
    if configs:
        return [_hit(root, configs[0])]
    for path in manifests[:_MAX_MANIFESTS]:
        if _declares_next_dependency(path):
            return [_hit(root, path)]
    return []


def _hit(root: Path, path: Path) -> tuple[str, str, str]:
    try:
        rel = str(path.relative_to(root))
    except ValueError:  # pragma: no cover - a file outside the scanned root
        rel = str(path)
    return (NEXT_FRAMEWORK, NEXT_STRATEGY, rel)


__all__ = ["NEXT_FRAMEWORK", "NEXT_STRATEGY", "detect_next_framework"]
