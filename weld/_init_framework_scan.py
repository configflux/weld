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
_RUST_EXTS: frozenset[str] = frozenset({".rs"})

# Maximum files per language family that the bounded scan will yield. One
# positive hit per framework is sufficient; the cap bounds the worst case
# (e.g. 100k .py files) without affecting detection on real repos where any
# framework imports concentrate near top-level entry modules.
_MAX_FILES_PER_LANG: int = 1000

# Env var that allows operators to override the per-language sampling cap
# for forensic re-runs (e.g. adversarial repo layouts where the real entry
# module sits past the first 1000 no-import files). See tracked issue follow-up.
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


# Frameworks per language family. ``HTTPClient`` is the synthetic
# outbound-HTTP-client framework (bd 0ssj): its import patterns
# (``import requests`` / ``from httpx`` ...) are Python, so it belongs to
# the Python family for per-language early-exit and sampling-cap purposes.
_LANG_FRAMEWORKS: dict[frozenset[str], set[str]] = {
    _PY_EXTS: {
        "FastAPI", "Django", "Flask", "SQLAlchemy", "Pydantic", "Prisma",
        "HTTPClient",
    },
    _JS_TS_EXTS: {"Express"},
    _GO_EXTS: {"Gin"},
    _RUST_EXTS: {"Axum"},
}


def _lang_for_ext(ext: str) -> frozenset[str] | None:
    if ext in _PY_EXTS:
        return _PY_EXTS
    if ext in _JS_TS_EXTS:
        return _JS_TS_EXTS
    if ext in _GO_EXTS:
        return _GO_EXTS
    if ext in _RUST_EXTS:
        return _RUST_EXTS
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
    if ext in _RUST_EXTS:
        return [(p, fw, s) for (p, fw, s) in all_patterns if fw == "Axum"]
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


def line_has_import(line: str, pattern: str) -> bool:
    """Return True when a source *line* declares the import *pattern*.

    Matched at the start of the stripped line so a framework name mentioned
    inside a string literal or a comment is not a detection.

    Go quoted-path patterns (``"github.com/gin-gonic/gin"``) appear inside an
    ``import`` block, so the line can legitimately start with a double quote.
    For ``.go`` files ``detect_frameworks`` pre-filters lines through
    :func:`weld._init_go_imports.iter_go_import_lines` (which strips block
    comments, raw strings and non-import lines); the substring check below is
    defense-in-depth for callers that bypass that pre-filter.

    Lives beside the bounded scan it serves rather than in
    :mod:`weld.init_detect`: both halves of "which patterns may this file
    match, and does this line match one" now read together, and the detector
    module keeps room under its line cap for the detectors themselves.
    """
    stripped = line.strip()
    is_go_quoted = pattern.startswith('"') and pattern.endswith('"')
    if is_go_quoted:
        if stripped.startswith(("#", "//")):
            return False
        return pattern in stripped
    if stripped.startswith(("#", "//", '"', "'", "(", "[")):
        return False
    if pattern.startswith(("from ", "import ", "use ")):
        # Python (from/import) and Rust (use) imports are start-of-line
        # declarations; a prefix match is precise (ADR 0071).
        return stripped.startswith(pattern)
    if "require(" in pattern:
        return (
            pattern in stripped
            and ("=" in stripped or stripped.startswith("require("))
        )
    return False
