"""Output formatting helpers for ``wd doctor``.

Factored out of ``weld/doctor.py`` to keep the main entry point under
the 400-line cap. Owns the section ordering, the per-line render, the
``Status:`` footer, and the suppression filter.

The :class:`weld.doctor.CheckResult` dataclass is duck-typed -- this
module only relies on the ``level``, ``message``, ``section`` and
``note_id`` attributes -- so importing back into ``doctor.py`` does not
create a cycle.
"""

from __future__ import annotations

from typing import Iterable, Sequence


# Section names render in this order. Any result with an unknown section
# falls back to the end.
_SECTION_ORDER: tuple[str, ...] = (
    "Project",
    "Config",
    "Graph",
    "Schema",
    "Nodes",
    "Edges",
    "Strategies",
    "Agent Graph",
    "Optional",
    "MCP",
)


def section_key(section: str) -> tuple[int, str]:
    """Sort key for ``_SECTION_ORDER`` with unknown sections last."""
    try:
        return (_SECTION_ORDER.index(section), "")
    except ValueError:
        return (len(_SECTION_ORDER), section)


def status_line(results: Sequence) -> str:
    """Build the ``Status:`` footer line.

    Counts the four levels separately. Verdict precedence:
    ``errors`` > ``warnings`` > ``notes`` > ``OK``. Notes never raise the
    exit code, but they DO show up in the headline when no warning or
    error is present so the user can spot recommendations at a glance.
    """
    n_ok = sum(1 for r in results if r.level == "ok")
    n_note = sum(1 for r in results if r.level == "note")
    n_warn = sum(1 for r in results if r.level == "warn")
    n_fail = sum(1 for r in results if r.level == "fail")
    if n_fail:
        verdict = "errors"
    elif n_warn:
        verdict = "warnings"
    elif n_note:
        verdict = "notes"
    else:
        verdict = "OK"
    note_suffix = "" if n_note == 1 else "s"
    warn_suffix = "" if n_warn == 1 else "s"
    fail_suffix = "" if n_fail == 1 else "s"
    return (
        f"Status: {verdict} -- {n_ok} ok, "
        f"{n_note} note{note_suffix}, "
        f"{n_warn} warning{warn_suffix}, "
        f"{n_fail} error{fail_suffix}"
    )


def format_line(result) -> str:
    """Render a single :class:`CheckResult` row.

    ``note``-level rows with a ``note_id`` get an inline ``(id: <id>)``
    prefix so the user can copy the id directly into ``--ack <id>``.
    """
    if result.level == "note" and getattr(result, "note_id", None):
        return f"  [{result.level:4s}] (id: {result.note_id}) {result.message}"
    return f"  [{result.level:4s}] {result.message}"


def format_results(results: Sequence) -> str:
    """Format results grouped by section with a Status summary footer."""
    by_section: dict[str, list] = {}
    for r in results:
        by_section.setdefault(r.section, []).append(r)

    lines: list[str] = []
    sections = sorted(by_section.keys(), key=section_key)
    for section in sections:
        lines.append(f"[{section}]")
        for r in by_section[section]:
            lines.append(format_line(r))
    if lines:
        lines.append("")
    lines.append(status_line(results))
    return "\n".join(lines)


def apply_suppressions(results: Iterable, suppressed: set[str]) -> list:
    """Drop notes whose ``note_id`` appears in ``suppressed``."""
    if not suppressed:
        return list(results)
    return [
        r
        for r in results
        if not (
            r.level == "note"
            and getattr(r, "note_id", None) in suppressed
        )
    ]
