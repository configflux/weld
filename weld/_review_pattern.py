"""``--pattern`` DSL for bulk review operations (ADR 0055).

The DSL is small and bounded for security:

* whitespace-separated tokens
* each token is ``<field><op><value>`` where ``<op>`` is ``=`` (equality)
  or ``~`` (regex match)
* fields: ``type``, ``source``, ``target``, ``from``
* regex tokens are length-bounded (``MAX_REGEX_LEN``) to prevent ReDoS /
  OOM on a maliciously crafted pattern
* unknown fields or operators raise :class:`PatternError`

Examples::

    type=calls
    source=python_callgraph
    type=calls target~^symbol:weld\\.
    from~^file:.*\\.py$

This module is deliberately pure: no file I/O, no globals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Pattern as RePattern

# Bound regex inputs hard to thwart a ReDoS / catastrophic-backtracking
# pattern. 256 chars is enough for any sane review filter while
# preventing the matcher from spending unbounded time on a crafted
# pattern. The unit test pins this bound so it cannot drift.
MAX_REGEX_LEN = 256

# Bound the maximum number of tokens in one pattern to avoid pathological
# growth (we still accept dozens of clauses without trouble).
MAX_TOKENS = 16

# ADR 0055 § Security: cap the size of the string we feed the matcher.
# Node IDs in this codebase are typically well under 200 chars; clamping
# to 1024 chars at match time bounds worst-case backtracking even when
# the user feeds an exotic regex through the length-capped DSL.
MAX_MATCH_LEN = 1024

# Reject regex patterns whose nested quantifier shape is a textbook
# catastrophic backtracker (e.g. ``(a+)+``, ``(a*)*``, ``(a+)*``).
# Combined with ``MAX_REGEX_LEN`` and ``MAX_MATCH_LEN`` this keeps the
# DSL bounded against the standard ReDoS classes.
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*?][^)]*\)\s*[+*?{]")


_VALID_FIELDS: frozenset[str] = frozenset({"type", "source", "target", "from"})


class PatternError(ValueError):
    """Raised when a ``--pattern`` argument is malformed or unsafe."""


@dataclass
class Pattern:
    """Parsed pattern: equality clauses and regex clauses combined with AND.

    ``equality`` maps a field name (``type`` / ``source`` / ``target`` /
    ``from``) to the required literal value. ``regex`` maps the same
    field names to compiled :class:`re.Pattern`. Match is AND across
    every clause; an empty pattern matches every edge.
    """

    equality: dict[str, str] = field(default_factory=dict)
    regex: dict[str, RePattern[str]] = field(default_factory=dict)


def parse_pattern(text: str) -> Pattern:
    """Parse a DSL string. Empty or whitespace-only input matches all.

    Raises :class:`PatternError` for unknown fields, unknown operators,
    overlong regex tokens, or invalid regex syntax. Tokens are
    whitespace-separated; values may not contain whitespace (the DSL
    is deliberately small).
    """
    if not text or not text.strip():
        return Pattern()
    tokens = text.split()
    if len(tokens) > MAX_TOKENS:
        raise PatternError(
            f"--pattern accepts at most {MAX_TOKENS} clauses; got "
            f"{len(tokens)}.",
        )
    pat = Pattern()
    for tok in tokens:
        _parse_token(tok, pat)
    return pat


def _parse_token(tok: str, pat: Pattern) -> None:
    if "=" in tok and ("~" not in tok or tok.index("=") < tok.index("~")):
        field_name, _, value = tok.partition("=")
        _check_field(field_name)
        pat.equality[field_name] = value
        return
    if "~" in tok:
        field_name, _, value = tok.partition("~")
        _check_field(field_name)
        if len(value) > MAX_REGEX_LEN:
            raise PatternError(
                f"--pattern regex for {field_name!r} exceeds "
                f"{MAX_REGEX_LEN} characters.",
            )
        if _NESTED_QUANTIFIER.search(value):
            raise PatternError(
                f"--pattern regex for {field_name!r} contains a nested "
                "quantifier (e.g. '(a+)+'); rejected as ReDoS-unsafe.",
            )
        try:
            pat.regex[field_name] = re.compile(value)
        except re.error as exc:
            raise PatternError(
                f"--pattern regex for {field_name!r} is invalid: {exc}",
            ) from exc
        return
    raise PatternError(
        f"--pattern token {tok!r}: expected <field>=<value> or "
        f"<field>~<regex>.",
    )


def _check_field(name: str) -> None:
    if name not in _VALID_FIELDS:
        raise PatternError(
            f"--pattern field {name!r} is not supported. "
            f"Valid fields: {sorted(_VALID_FIELDS)}.",
        )


def match(pat: Pattern, edge: dict) -> bool:
    """Return True when *edge* satisfies every clause in *pat*.

    An empty pattern (no equality, no regex) matches every edge -- this
    is the "list everything" shortcut the CLI uses internally. Regex
    targets are clamped to :data:`MAX_MATCH_LEN` so an oversize edge id
    plus a pathological regex (already filtered for nested quantifiers
    at parse time) cannot stall the matcher.
    """
    fields = _edge_fields(edge)
    for key, value in pat.equality.items():
        if fields.get(key) != value:
            return False
    for key, rx in pat.regex.items():
        target = (fields.get(key) or "")[:MAX_MATCH_LEN]
        if not rx.search(target):
            return False
    return True


def _edge_fields(edge: dict) -> dict[str, str]:
    props = edge.get("props") or {}
    return {
        "type": str(edge.get("type") or ""),
        "from": str(edge.get("from") or ""),
        "target": str(edge.get("to") or ""),
        "source": str(props.get("source_strategy") or ""),
    }


__all__ = [
    "MAX_MATCH_LEN",
    "MAX_REGEX_LEN",
    "MAX_TOKENS",
    "Pattern",
    "PatternError",
    "match",
    "parse_pattern",
]
