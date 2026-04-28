"""Defensive validator for telemetry events.

Per ADR 0035 § "Strict allowlist event schema (v1)", every event passes
through this validator before write. Anything that fails validation is
silently dropped -- the writer never raises and never partially records.

Two layers:

- :func:`is_safe_string` is a per-string defensive check. It rejects any
  input that contains path separators, drive-letter prefixes, ``..`` or
  ``~`` indicators, email-like patterns, ASCII control characters,
  whitespace runs of length >= 4, digit runs of length >= 5 (in
  non-numeric fields), or strings longer than 96 characters.
- :func:`validate_event` is a full event check. It enforces the schema
  shape (required keys, types, ranges, enum membership) and applies
  :func:`is_safe_string` to every string field. Numeric fields like
  ``schema_version``, ``exit_code``, and ``duration_ms`` bypass the
  digit-run check because their integer values are intentional.

Trust boundary: the writer treats this module as a hard wall. If the
allowlist source upstream forgets to coerce a free-form string, the
event is rejected here. Tests prove every legitimate enum value passes
and every prohibited shape is rejected.
"""

from __future__ import annotations

import re
from typing import Final

from weld._telemetry_allowlist import CLI_FLAGS, CLI_COMMANDS, MCP_TOOLS


# ---------------------------------------------------------------------------
# Regex matchers.
# ---------------------------------------------------------------------------


_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_DRIVE_LETTER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]:")
_LONG_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s{4,}")
_LONG_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"\d{5,}")
_CONTROL_CHAR_RE: Final[re.Pattern[str]] = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f]"
)

# Punctuation we never expect in an enum-shaped string. JSON braces,
# quotes, colons, equals, and semicolons would only appear in leaked
# free-form values (exception messages, query strings, JSON blobs).
_DISALLOWED_PUNCTUATION: Final[frozenset[str]] = frozenset(
    set('{}[]"\'`:=;,<>?!*&%$#^|()')
)


error_kind_pattern: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
)
"""Strict pattern for the ``error_kind`` field. Class names only."""


_MAX_STRING_LEN: Final[int] = 96


# Schema enums.
_ALLOWED_SURFACES: Final[frozenset[str]] = frozenset({"cli", "mcp"})
_ALLOWED_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"ok", "error", "interrupted"}
)
_ALLOWED_PLATFORMS: Final[frozenset[str]] = frozenset(
    {"linux", "darwin", "win32", "cygwin", "freebsd", "openbsd", "netbsd",
     "aix", "sunos", "wasi", "emscripten", "ios", "android"}
)


# Required fields and their permitted Python types.
_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "ts",
    "weld_version",
    "surface",
    "command",
    "outcome",
    "exit_code",
    "duration_ms",
    "error_kind",
    "python_version",
    "platform",
    "flags",
)


def is_safe_string(value: str) -> bool:
    """Return True iff ``value`` is a defensible enum-shaped string.

    See module docstring for the full rule list. Empty strings and
    non-strings are rejected.
    """
    if not isinstance(value, str):
        return False
    if not value:
        return False
    if len(value) > _MAX_STRING_LEN:
        return False
    if "/" in value or "\\" in value:
        return False
    if "@" in value:
        return False
    if ".." in value or "~" in value:
        return False
    if _DRIVE_LETTER_RE.match(value):
        return False
    if _LONG_WHITESPACE_RE.search(value):
        return False
    if _LONG_DIGITS_RE.search(value):
        return False
    if _EMAIL_RE.search(value):
        return False
    if _CONTROL_CHAR_RE.search(value):
        return False
    if any(ch in _DISALLOWED_PUNCTUATION for ch in value):
        return False
    return True


def _is_safe_short_string(value: str, *, allow_long_digits: bool = False) -> bool:
    """Subset check for fields that may legitimately contain digit runs.

    ``allow_long_digits=True`` skips the digit-run rejection. The other
    rules still apply, so a "5.6.7" version string passes but a path
    string like "/var/log/12345" still fails.
    """
    if allow_long_digits:
        if not isinstance(value, str) or not value or len(value) > _MAX_STRING_LEN:
            return False
        if "/" in value or "\\" in value:
            return False
        if "@" in value:
            return False
        if ".." in value or "~" in value:
            return False
        if _DRIVE_LETTER_RE.match(value):
            return False
        if _LONG_WHITESPACE_RE.search(value):
            return False
        if _EMAIL_RE.search(value):
            return False
        if _CONTROL_CHAR_RE.search(value):
            return False
        return True
    return is_safe_string(value)


def _validate_ts(value: object) -> bool:
    if not isinstance(value, str):
        return False
    # ISO-8601 second precision, UTC. ``2026-04-28T14:03:11Z``.
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value))


def _validate_python_version(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", value))


def _validate_weld_version(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if not value or len(value) > _MAX_STRING_LEN:
        return False
    # Versions can have digits, dots, dashes, and PEP 440 segments.
    return bool(re.fullmatch(r"[0-9A-Za-z.+\-]+", value))


def validate_event(event: dict) -> dict | None:
    """Return ``event`` if every field passes, else ``None``.

    The check is strict: an unknown extra key, a missing required key,
    a wrong type, or a string that fails :func:`is_safe_string` all drop
    the event. This is the trust boundary referenced by ADR 0035 §
    "Failure-isolated writer".
    """
    if not isinstance(event, dict):
        return None

    # Required keys present.
    for key in _REQUIRED_FIELDS:
        if key not in event:
            return None

    # No unknown extras (caller can extend the schema later via a version bump).
    extra = set(event.keys()) - set(_REQUIRED_FIELDS)
    if extra:
        return None

    if event["schema_version"] != 1:
        return None

    if not _validate_ts(event["ts"]):
        return None

    if not _validate_weld_version(event["weld_version"]):
        return None

    surface = event["surface"]
    if surface not in _ALLOWED_SURFACES:
        return None

    command = event["command"]
    if not isinstance(command, str) or not is_safe_string(command):
        return None
    # Command must be in the per-surface allowlist (or "unknown").
    if surface == "cli":
        if command not in CLI_COMMANDS:
            return None
    else:
        if command not in MCP_TOOLS and command != "unknown":
            return None

    if event["outcome"] not in _ALLOWED_OUTCOMES:
        return None

    exit_code = event["exit_code"]
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return None
    if exit_code < -1 or exit_code > 255:
        return None

    duration_ms = event["duration_ms"]
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool):
        return None
    if duration_ms < 0:
        return None

    error_kind = event["error_kind"]
    if error_kind is not None:
        if not isinstance(error_kind, str):
            return None
        if not error_kind_pattern.fullmatch(error_kind):
            return None

    if not _validate_python_version(event["python_version"]):
        return None

    if event["platform"] not in _ALLOWED_PLATFORMS:
        return None

    flags = event["flags"]
    if not isinstance(flags, list):
        return None
    seen: set[str] = set()
    for f in flags:
        if not isinstance(f, str):
            return None
        if f in seen:
            return None
        seen.add(f)
        if f not in CLI_FLAGS:
            return None
        if not is_safe_string(f):
            return None
    # Stable order: caller is responsible, but verify so a leak through an
    # unsorted upstream cannot survive.
    if list(flags) != sorted(flags):
        return None

    return event
