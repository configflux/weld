"""Text rendering for the agent-direct enrichment plan (ADR 0098).

Split from :mod:`weld._enrich_agent_direct` the way
:mod:`weld._first_run_render` is split from
:mod:`weld._first_run_enrich`: the builder owns the data, this module
owns how it reads on a terminal. Pure -- one plan in, one string out.

Everything here renders a field the ``--json`` payload also carries, so
the two surfaces cannot say different things. The one thing that must
survive rendering byte-for-byte is the ``wd add-node`` template: it is
meant to be copy-pasted, so it is emitted on its own unwrapped line.
"""

from __future__ import annotations

import textwrap

_WIDTH = 78


def _para(text: str, indent: str = "", first: str | None = None) -> str:
    """Wrap one paragraph to the terminal width.

    *indent* prefixes continuation lines; *first* overrides the prefix of
    the first line (used for bullets, so wrapped text hangs under the
    text rather than under the marker).
    """
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=indent if first is None else first,
        subsequent_indent=indent,
    )


def _heading(title: str) -> str:
    return f"{title}\n{'-' * len(title)}"


def _pending_lines(plan: dict) -> list[str]:
    """Render the pending-node table, or say plainly that there is none."""
    counts = plan["counts"]
    total = counts["pending_total"]
    if total == 0:
        # Two different empties. Saying "already enriched" when the filter
        # simply matched nothing would send the reader looking for records
        # that do not exist.
        scope = counts["scope_total"]
        body = (
            f"Nothing pending: all {scope} node(s) in scope already carry a "
            "complete enrichment record. Pass --force to list them anyway "
            "and re-enrich."
            if scope
            else "Nothing in scope: no node matched. Check --type / --node, "
            "or run wd stats to see what the graph holds."
        )
        return [_heading("Pending nodes"), "", _para(body)]
    remaining = counts["remaining"]
    header = f"Pending nodes ({counts['returned']} of {total} listed"
    header += (
        f"; {remaining} more not listed -- raise --limit)"
        if remaining
        else ")"
    )
    lines = [_heading(header), ""]
    for item in plan["pending"]:
        source = item["file"] or "(no source file -- read its neighborhood)"
        lines.append(f"  {item['id']}")
        lines.append(
            f"      type={item['type']}  label={item['label']}  source={source}"
        )
    return lines


def _contract_lines(contract: dict) -> list[str]:
    """Render the record contract: what a write must carry, and why."""
    # Declaration order, not sorted: "provider" then "model" reads the way the
    # record is written. The builder's dict is a literal, so this is stable.
    recommended = ", ".join(
        f'"{key}": "{value}"' for key, value in contract["recommended"].items()
    )
    return [
        _heading("The record contract"),
        "",
        _para(
            "Every write must set props.enrichment with all required "
            "fields non-empty: " + ", ".join(contract["required_fields"]) + "."
        ),
        "",
        f"  Recommended for this path:  {recommended}",
        "  timestamp:                  ISO-8601 UTC, e.g. 2026-01-31T14:05:00+00:00",
        "  Optional:                   " + ", ".join(contract["optional_fields"]),
        "  Mirror to top-level props:  " + ", ".join(contract["mirrored_to_top_level"]),
        "",
        _para(contract["rejection"]),
        "",
        _para(contract["persistence"]),
    ]


def render_plan(plan: dict) -> str:
    """Render *plan* as the human-facing ``wd enrich --agent-direct`` output."""
    blocks: list[str] = [
        _heading("wd enrich --agent-direct"),
        "",
        _para(plan["preamble"]),
        "",
        *_pending_lines(plan),
        "",
        *_contract_lines(plan["record_contract"]),
        "",
        _heading("Write it back"),
        "",
        # Unwrapped on purpose: this line is meant to be copy-pasted.
        "  " + plan["command_template"],
        "",
        _heading("Verify"),
        "",
    ]
    blocks.extend(f"  {step}" for step in plan["verification"])
    blocks.extend(["", _heading("Notes"), ""])
    for note in plan["notes"]:
        blocks.append(_para(note, indent="    ", first="  - "))
        blocks.append("")
    return "\n".join(blocks).rstrip("\n") + "\n"


__all__ = ["render_plan"]
