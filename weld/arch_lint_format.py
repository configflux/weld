"""Human-readable formatter for ``weld.arch_lint`` results.

Renders the lint result envelope with a top-of-output summary and
violations grouped per rule.  Rules are ordered signal-first
(``no-circular-deps`` and ``boundary-enforcement`` before
``strategy-coverage`` and ``orphan-detection``) so the most actionable
output appears first; the noisy ``orphan-detection`` block lands at the
bottom and is annotated with the suppressed count when applicable.
"""

from __future__ import annotations

from collections import OrderedDict

# Print order: highest signal first, noisiest last.
_RULE_PRINT_ORDER: tuple[str, ...] = (
    "no-circular-deps",
    "boundary-enforcement",
    "strategy-coverage",
    "orphan-detection",
)


def _ordered_rule_keys(rules_run: list[str]) -> list[str]:
    """Return rule ids ordered by print priority then declaration order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for rid in _RULE_PRINT_ORDER:
        if rid in rules_run and rid not in seen:
            ordered.append(rid)
            seen.add(rid)
    for rid in rules_run:
        if rid not in seen:
            ordered.append(rid)
            seen.add(rid)
    return ordered


def _group_violations(violations: list[dict]) -> "OrderedDict[str, list[dict]]":
    """Group violations by rule id, preserving per-rule input order."""
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for v in violations:
        groups.setdefault(v.get("rule", ""), []).append(v)
    return groups


def _summary_line(result: dict) -> str:
    """Build the single-line summary printed at the top of text output."""
    count = result.get("violation_count", 0)
    suppressed = result.get("suppressed_count", 0) or 0
    groups = _group_violations(result.get("violations") or [])
    if count == 0 and suppressed == 0:
        return "No architectural violations found."

    rules_run = result.get("rules_run") or []
    ordered = _ordered_rule_keys(list(rules_run))
    parts: list[str] = []
    for rid in ordered:
        rule_count = len(groups.get(rid, []))
        if rid == "orphan-detection" and suppressed:
            parts.append(
                f"{rule_count} {rid} (suppressed: {suppressed} doc/config/test)"
            )
        else:
            parts.append(f"{rule_count} {rid}")

    rules_run_count = len(rules_run)
    return (
        f"{count} violation(s) across {rules_run_count} rule(s): "
        + ", ".join(parts)
    )


def format_text(result: dict) -> str:
    """Render a ``lint()`` result as a human-readable report.

    The first line is always a summary line.  Violations follow,
    grouped by rule in print-priority order.  Runner-level warnings
    (unknown rule ids, custom-rule load issues) print last.
    """
    lines: list[str] = []
    rules_run = result.get("rules_run") or []
    if not rules_run:
        lines.append("No rules executed.")
    else:
        lines.append(_summary_line(result))

    groups = _group_violations(result.get("violations") or [])
    for rid in _ordered_rule_keys(list(rules_run)):
        bucket = groups.get(rid, [])
        if not bucket:
            continue
        lines.append("")
        lines.append(f"[{rid}] {len(bucket)} violation(s):")
        for v in bucket:
            lines.append(f"- {v.get('node_id', '')}: {v.get('message', '')}")

    suppressed = result.get("suppressed_count", 0) or 0
    if suppressed and not groups.get("orphan-detection"):
        lines.append("")
        lines.append(
            f"[orphan-detection] 0 visible violation(s); "
            f"{suppressed} suppressed (doc/config/test). "
            f"Use --include-noisy to see them."
        )

    for warning in result.get("warnings", []) or []:
        lines.append(f"Warning: {warning}")

    return "\n".join(lines) + "\n"
