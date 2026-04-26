"""Bounded scan helpers for ``detect_frameworks`` (ADR 0027).

Three early-exit rules keep ``wd init`` bounded on large monorepos:

* **Per-file early exit** — handled by the caller; once a file has matched
  every framework relevant to its language, the caller stops scanning lines.
* **Per-language early exit** — once every framework that can be detected
  from a language family has been seen at least once anywhere in the repo,
  further files of that language are not opened.
* **Per-language sampling cap** — at most ``_MAX_FILES_PER_LANG`` files per
  language family are read. One positive hit per framework is sufficient,
  so sampling does not change the detected set on well-organised repos.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator

# Per-language extension families. A pattern is only scanned against files
# whose extension matches its language family, so a .go file is never
# scanned against Python ``from fastapi`` patterns.
_PY_EXTS: frozenset[str] = frozenset({".py"})
_JS_TS_EXTS: frozenset[str] = frozenset({".js", ".jsx", ".ts", ".tsx"})
_GO_EXTS: frozenset[str] = frozenset({".go"})

# Maximum files per language family that the bounded scan will yield. One
# positive hit per framework is sufficient; the cap bounds the worst case
# (e.g. 100k .py files) without affecting detection on real repos where any
# framework imports concentrate near top-level entry modules.
_MAX_FILES_PER_LANG: int = 1000

# Env var that allows operators to override the per-language sampling cap
# for forensic re-runs (e.g. adversarial repo layouts where the real entry
# module sits past the first 1000 no-import files). See bd-mil4 follow-up.
_CAP_ENV_VAR: str = "WELD_INIT_FRAMEWORK_CAP"


def _resolve_max_files_per_lang() -> int | None:
    """Return the effective per-language cap.

    ``None`` means unbounded. Any unset / empty / non-numeric / negative
    value silently falls back to the default ``_MAX_FILES_PER_LANG`` --
    this is an internal escape hatch, not a user-facing setting, so we
    do not warn on bad input.
    """
    raw = os.environ.get(_CAP_ENV_VAR)
    if raw is None or raw == "":
        return _MAX_FILES_PER_LANG
    try:
        value = int(raw)
    except ValueError:
        return _MAX_FILES_PER_LANG
    if value < 0:
        return _MAX_FILES_PER_LANG
    if value == 0:
        return None
    return value


# Frameworks per language family.
_LANG_FRAMEWORKS: dict[frozenset[str], set[str]] = {
    _PY_EXTS: {
        "FastAPI", "Django", "Flask", "SQLAlchemy", "Pydantic", "Prisma",
    },
    _JS_TS_EXTS: {"Express"},
    _GO_EXTS: {"Gin"},
}


def _lang_for_ext(ext: str) -> frozenset[str] | None:
    if ext in _PY_EXTS:
        return _PY_EXTS
    if ext in _JS_TS_EXTS:
        return _JS_TS_EXTS
    if ext in _GO_EXTS:
        return _GO_EXTS
    return None


def _patterns_for_ext(
    ext: str, all_patterns: Iterable[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Return patterns that can plausibly match files of ``ext``."""
    if ext in _PY_EXTS:
        return [
            (p, fw, s) for (p, fw, s) in all_patterns
            if p.startswith(("from ", "import "))
            and fw in _LANG_FRAMEWORKS[_PY_EXTS]
        ]
    if ext in _JS_TS_EXTS:
        return [(p, fw, s) for (p, fw, s) in all_patterns if fw == "Express"]
    if ext in _GO_EXTS:
        return [(p, fw, s) for (p, fw, s) in all_patterns if fw == "Gin"]
    return []


def iter_framework_scan_targets(
    files: Iterable[Path],
    all_patterns: list[tuple[str, str, str]],
) -> Iterator[tuple[Path, list[tuple[str, str, str]], set[str]]]:
    """Yield ``(file, relevant_patterns, outstanding)`` per scannable file.

    Applies per-language early exit and the per-language sampling cap. The
    caller is responsible for per-file early exit and for discarding
    detected frameworks from ``outstanding`` (a set shared across files of
    the same language so detection in one file silences scanning in
    later files).
    """
    lang_outstanding = {
        key: set(fws) for key, fws in _LANG_FRAMEWORKS.items()
    }
    lang_seen: dict[frozenset[str], int] = {
        key: 0 for key in _LANG_FRAMEWORKS
    }
    cap = _resolve_max_files_per_lang()
    for f in files:
        ext = f.suffix.lower()
        lang_key = _lang_for_ext(ext)
        if lang_key is None:
            continue
        outstanding = lang_outstanding[lang_key]
        if not outstanding:
            continue
        if cap is not None and lang_seen[lang_key] >= cap:
            continue
        lang_seen[lang_key] += 1
        relevant = [
            (p, fw, s) for (p, fw, s) in _patterns_for_ext(ext, all_patterns)
            if fw in outstanding
        ]
        if relevant:
            yield f, relevant, outstanding
