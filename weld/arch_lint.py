"""Architectural linting over the discovered graph.

This module runs a small set of pluggable rules against ``.weld/graph.json``
and reports violations as a stable JSON envelope or human-readable text.
Each rule iterates the graph once and yields zero or more violations. The
CLI exits ``0`` when no *visible* violations were found and ``1`` when any
non-suppressed violation was reported -- suitable for use as a CI gate.

Built-in rules:

* ``orphan-detection`` -- flags nodes with neither incoming nor outgoing
  edges.  By default suppresses doc/config/test node types because those
  are almost always intentional leaves; ``--include-noisy`` restores the
  broad sweep.
* ``strategy-coverage`` -- reads ``.weld/discover.yaml`` and flags source
  entries whose glob patterns match zero files (stale or misconfigured
  discovery config).
* ``no-circular-deps`` -- detects strongly connected components (cycles)
  via Tarjan's algorithm.
* ``boundary-enforcement`` -- flags edges crossing layer boundaries
  without a ``topology.allowed_cross_layer`` entry in ``discover.yaml``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from weld.arch_lint_boundary import rule_boundary_enforcement
from weld.arch_lint_custom import (
    CUSTOM_RULES_FILENAME,
    CustomRule,
    load_custom_rules,
)
from weld.arch_lint_cycles import rule_no_circular_deps
from weld.arch_lint_format import format_text
from weld.arch_lint_orphan import detect_orphans, rule_orphan_detection
from weld.graph import Graph

ARCH_LINT_VERSION = 1

ORPHAN_RULE_ID = "orphan-detection"

@dataclass(frozen=True)
class Violation:
    """A single architectural violation reported by a rule.

    The ``to_dict`` shape is part of the stable JSON contract; callers
    (CI scripts, editors, the MCP server) may rely on it.
    """

    rule: str
    node_id: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "node_id": self.node_id,
            "message": self.message,
            "severity": self.severity,
        }

# Most rules take a graph-data dict (as returned by ``Graph.dump()``) and
# yield violations.  A rule that also needs the project root (e.g. to
# resolve file globs against the file system) sets ``needs_root=True`` and
# accepts ``(data, root)`` instead of just ``(data)``.
RuleFn = Callable[[dict], Iterable[Violation]]
RootRuleFn = Callable[[dict, Path], Iterable[Violation]]

@dataclass(frozen=True)
class Rule:
    """A named architectural rule."""

    rule_id: str
    description: str
    check: RuleFn | RootRuleFn
    needs_root: bool = False

# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------

def _rule_strategy_coverage(data: dict, root: Path) -> Iterable[Violation]:
    """Flag source entries in discover.yaml whose globs match zero files."""
    config_path = root / ".weld" / "discover.yaml"
    if not config_path.is_file():
        return

    from weld._yaml import parse_yaml

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return
    config = parse_yaml(text)
    sources = config.get("sources", []) if isinstance(config, dict) else []

    unmatched: list[tuple[str, str]] = []  # (pattern, strategy)

    for source in sources:
        if not isinstance(source, dict):
            continue
        strategy = source.get("strategy", "<unknown>")

        glob_pattern = source.get("glob")
        if glob_pattern:
            if "**" in str(glob_pattern):
                matched = list(root.glob(str(glob_pattern)))
            else:
                parent = (root / str(glob_pattern)).parent
                if parent.is_dir():
                    matched = list(
                        parent.glob(Path(str(glob_pattern)).name)
                    )
                else:
                    matched = []
            if not matched:
                unmatched.append((str(glob_pattern), str(strategy)))
            continue

        file_list = source.get("files", [])
        if file_list:
            missing = [
                f for f in file_list
                if not (root / str(f)).is_file()
            ]
            if len(missing) == len(file_list):
                pattern = f"files:{file_list}"
                unmatched.append((pattern, str(strategy)))

    for pattern, strategy in sorted(unmatched, key=lambda t: t[0]):
        yield Violation(
            rule="strategy-coverage",
            node_id=pattern,
            message=(
                f"source entry {pattern!r} (strategy: {strategy}) "
                f"matched zero files; stale or misconfigured"
            ),
            severity="warning",
        )

_RULES: tuple[Rule, ...] = (
    Rule(
        rule_id=ORPHAN_RULE_ID,
        description=(
            "Flag nodes that have neither incoming nor outgoing edges "
            "(dead code candidates). Default suppresses doc/config/test."
        ),
        check=rule_orphan_detection,
    ),
    Rule(
        rule_id="strategy-coverage",
        description=(
            "Flag source entries in discover.yaml whose glob patterns "
            "match zero files (stale or misconfigured config)."
        ),
        check=_rule_strategy_coverage,
        needs_root=True,
    ),
    Rule(rule_id="no-circular-deps", description="Detect circular dependencies via SCC analysis.", check=rule_no_circular_deps),
    Rule(rule_id="boundary-enforcement",
         description="Flag edges crossing layer boundaries without topology declaration.",
         check=rule_boundary_enforcement, needs_root=True),
)

def available_rule_ids(rules: Iterable[Rule] | None = None) -> list[str]:
    """Return the registered rule ids in declaration order."""
    registry = _RULES if rules is None else rules
    return [rule.rule_id for rule in registry]

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _custom_rules_path(graph: Graph) -> Path | None:
    graph_path = getattr(graph, "_path", None)
    if isinstance(graph_path, Path):
        return graph_path.parent / CUSTOM_RULES_FILENAME
    return None

def _adapt_custom_rule(custom_rule: CustomRule) -> Rule:
    def check(data: dict) -> Iterable[Violation]:
        for violation in custom_rule.check(data):
            yield Violation(
                rule=violation.rule,
                node_id=violation.node_id,
                message=violation.message,
                severity=violation.severity,
            )

    return Rule(
        rule_id=custom_rule.rule_id,
        description=custom_rule.description,
        check=check,
    )

def _run_rule(
    rule: Rule, data: dict, root: Path, *, include_noisy: bool
) -> tuple[list[dict], int]:
    """Run a single rule and return (violation dicts, suppressed_count)."""
    if rule.rule_id == ORPHAN_RULE_ID:
        violations, suppressed = detect_orphans(
            data, include_noisy=include_noisy
        )
        return [v.to_dict() for v in violations], suppressed
    if rule.needs_root:
        results = rule.check(data, root)
    else:
        results = rule.check(data)
    return [v.to_dict() for v in results], 0

def lint(
    graph: Graph,
    *,
    rule_ids: Iterable[str] | None = None,
    root: Path | None = None,
    include_noisy: bool = False,
) -> dict:
    """Run architectural rules against *graph* and return a result envelope.

    Custom edge-deny rules are loaded from ``.weld/lint-rules.yaml`` when
    present and are selected by ``rule_ids`` just like built-in rules.

    ``{
        "arch_lint_version": 1,
        "rules_run": [...rule ids actually executed...],
        "violations": [...Violation.to_dict()...],
        "violation_count": <int>,
        "suppressed_count": <int -- orphans hidden by default suppression>,
        "warnings": [...runner-level warnings...],
    }``

    *rule_ids* selects a subset of the registered rules. Unknown ids are
    reported as warnings rather than errors so callers can discover
    available rules incrementally.

    *root* is the project root directory.  When ``None`` it is derived
    from the graph's backing path.

    *include_noisy* disables the orphan-detection default suppression of
    ``doc``, ``config``, and ``test`` node types so callers get the broad
    sweep.  Has no effect on other rules.
    """
    data = graph.dump()
    custom_path = _custom_rules_path(graph)
    custom_rules: list[Rule] = []
    custom_warnings: list[str] = []
    if custom_path is not None:
        custom_specs, custom_warnings = load_custom_rules(
            custom_path, available_rule_ids()
        )
        custom_rules = [_adapt_custom_rule(rule) for rule in custom_specs]
    rules = [*_RULES, *custom_rules]
    if root is None:
        # Graph stores its path as root/.weld/graph.json
        root = graph._path.parent.parent

    selected, warnings = _select_rules(rule_ids, rules)
    violations: list[dict] = []
    suppressed_count = 0
    for rule in selected:
        rule_violations, rule_suppressed = _run_rule(
            rule, data, root, include_noisy=include_noisy
        )
        violations.extend(rule_violations)
        suppressed_count += rule_suppressed

    return {
        "arch_lint_version": ARCH_LINT_VERSION,
        "rules_run": [rule.rule_id for rule in selected],
        "violations": violations,
        "violation_count": len(violations),
        "suppressed_count": suppressed_count,
        "warnings": [*custom_warnings, *warnings],
    }

def _select_rules(
    rule_ids: Iterable[str] | None,
    rules: Iterable[Rule],
) -> tuple[list[Rule], list[str]]:
    """Resolve a rule-id filter to concrete rules + warnings for unknown ids."""
    registry = list(rules)
    if rule_ids is None:
        return registry, []

    requested = list(rule_ids)
    known = {rule.rule_id: rule for rule in registry}
    selected: list[Rule] = []
    seen: set[str] = set()
    warnings: list[str] = []
    for rid in requested:
        if rid in seen:
            continue
        seen.add(rid)
        rule = known.get(rid)
        if rule is None:
            warnings.append(
                f"unknown rule id {rid!r}; available: "
                f"{', '.join(available_rule_ids(registry))}"
            )
            continue
        selected.append(rule)
    return selected, warnings

# Re-export ``format_text`` so that existing callers and tests continue
# to import it from ``weld.arch_lint``.
__all__ = [
    "ARCH_LINT_VERSION",
    "Rule",
    "Violation",
    "available_rule_ids",
    "format_text",
    "lint",
    "main",
]

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd lint``.

    Exit code is ``0`` when no *visible* violations were reported and
    ``1`` when any non-suppressed violation fired.  Suppressed orphans
    alone never raise the exit code -- they are reported only in the
    summary line.
    """
    parser = argparse.ArgumentParser(
        prog="wd lint",
        description=(
            "Lint the graph for architectural violations (dead code, layer "
            "inversion, missing metadata). Loads .weld/lint-rules.yaml when "
            "present. Exits non-zero on visible violations."
        ),
    )
    parser.add_argument(
        "--rule",
        action="append",
        default=None,
        metavar="RULE_ID",
        help=(
            "Run only the named rule (may be repeated). Default: run every "
            f"registered rule. Available: {', '.join(available_rule_ids())}."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the stable JSON envelope instead of human-readable text.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root containing .weld/graph.json (default: cwd).",
    )
    parser.add_argument(
        "--include-noisy",
        action="store_true",
        help=(
            "Disable the orphan-detection default suppression of "
            "doc/config/test nodes; surface every orphan."
        ),
    )
    args = parser.parse_args(argv)

    graph = Graph(args.root)
    graph.load()

    rule_filter = list(args.rule) if args.rule is not None else None
    result = lint(
        graph,
        rule_ids=rule_filter,
        root=args.root,
        include_noisy=args.include_noisy,
    )

    if args.json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_text(result))

    # Exit non-zero only when a non-suppressed violation fired.
    return 1 if result["violation_count"] > 0 else 0
