"""Text rendering for the per-language trust block of ``wd stats``.

Split out of :mod:`weld._cli_render` so that module stays under the
400-line cap (AGENTS.md / CLAUDE.md line-count policy) and the trust
render lives next to nothing else -- it is the one piece of stats output
with its own advisory logic. The numbers it renders come from
:func:`weld._graph_stats_trust.compute_per_language_trust`; the floor it
flags against is the same constant ``wd doctor`` warns on, so the CLI
text and the doctor warning never disagree.
"""

from __future__ import annotations

from typing import Any, Mapping

from weld._doctor_trust import UNRESOLVED_RATIO_FLOOR


def stats_trust_lines(trust: Mapping[str, Any]) -> list[str]:
    """Render the per-language trust block for ``wd stats`` text output.

    One line per language with the three headline numbers (unresolved
    ratio, edge-resolution rate, description coverage), followed by an
    advisory ``!`` line for any language whose unresolved ratio crosses
    the same absolute floor ``wd doctor`` warns on. Returns an empty list
    when no language carries trust data so single-language non-symbol
    graphs print nothing extra.
    """
    if not trust:
        return []
    lines = ["  per_language_trust:"]
    flagged: list[str] = []
    for lang in sorted(trust):
        m = trust[lang]
        ratio = m.get("unresolved_symbol_ratio", 0.0)
        lines.append(
            f"    {lang}: unresolved {ratio:.0%} "
            f"({m.get('unresolved_symbols', 0)}/{m.get('symbols', 0)}), "
            f"edges_resolved {m.get('edge_resolution_rate', 0.0):.0%}, "
            f"described {m.get('description_coverage_pct', 0.0)}%"
        )
        if ratio > UNRESOLVED_RATIO_FLOOR:
            flagged.append(lang)
    for lang in flagged:
        lines.append(
            f"    ! {lang} unresolved ratio above "
            f"{UNRESOLVED_RATIO_FLOOR:.0%} floor -- agents should not "
            f"trust {lang} call/inherit edges"
        )
    return lines


__all__ = ["stats_trust_lines"]
