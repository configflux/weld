"""Architectural linting over the discovered graph.

This module runs a small set of pluggable rules against ``.weld/graph.json``
and reports violations as a stable JSON envelope or human-readable text.
Each rule iterates the graph once and yields zero or more violations. The
CLI exits ``0`` when no violations were found and ``1`` when any rule
produced a violation -- suitable for use as a CI quality gate.

Built-in rules:

* ``orphan-detection`` -- flags nodes with neither incoming nor outgoing
  edges.
* ``strategy-coverage`` -- reads ``.weld/discover.yaml`` and flags source
  entries whose glob patterns match zero files (stale or misconfigured
  discovery config).
* ``no-circular-deps`` -- detects strongly connected components (cycles)
  via Tarjan's algorithm.

Additional rules (layer boundaries, cross-component edges, required
metadata) slot into the same ``_RULES`` registry without touching the
runner.
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
from weld.graph import Graph

ARCH_LINT_VERSION = 1

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

def _rule_orphan_detection(data: dict) -> Iterable[Violation]:
    """Flag nodes with zero incoming or outgoing edges (dead code candidates)."""
    nodes: dict = data.get("nodes", {}) or {}
    edges: list = data.get("edges", []) or []

    touched: set[str] = set()
    for edge in edges:
        frm = edge.get("from")
        to = edge.get("to")
        if isinstance(frm, str):
            touched.add(frm)
        if isinstance(to, str):
            touched.add(to)

    orphans = sorted(node_id for node_id in nodes if node_id not in touched)
    for node_id in orphans:
        node = nodes.get(node_id) or {}
        label = node.get("label") or node_id
        yield Violation(
            rule="orphan-detection",
            node_id=node_id,
            message=(
                f"node {node_id!r} ({label}) has no incoming or outgoing "
                f"edges; likely dead code or a discovery gap"
            ),
        )

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
        rule_id="orphan-detection",
        description=(
            "Flag nodes that have neither incoming nor outgoing edges "
            "(dead code candidates)."
        ),
        check=_rule_orphan_detection,
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

def lint(
    graph: Graph,
    *,
    rule_ids: Iterable[str] | None = None,
    root: Path | None = None,
) -> dict:
    """Run architectural rules against *graph* and return a result envelope.

    The result shape is stable for CI consumers:

    Custom edge-deny rules are loaded from ``.weld/lint-rules.yaml`` when
    present and are selected by ``rule_ids`` just like built-in rules.

    ``{
        "arch_lint_version": 1,
        "rules_run": [...rule ids actually executed...],
        "violations": [...Violation.to_dict()...],
        "violation_count": <int>,
        "warnings": [...runner-level warnings, never per-rule findings...],
    }``

    *rule_ids* selects a subset of the registered rules. Unknown ids are
    reported as warnings rather than errors so callers can discover
    available rules incrementally. An empty (but non-``None``) iterable
    filters out every rule.

    *root* is the project root directory. When ``None`` it is derived
    from the graph's backing path (``<root>/.weld/graph.json``). Rules
    that set ``needs_root=True`` receive *root* as a second argument.
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
    for rule in selected:
        if rule.needs_root:
            results = rule.check(data, root)
        else:
            results = rule.check(data)
        for violation in results:
            violations.append(violation.to_dict())

    return {
        "arch_lint_version": ARCH_LINT_VERSION,
        "rules_run": [rule.rule_id for rule in selected],
        "violations": violations,
        "violation_count": len(violations),
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

# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

def format_text(result: dict) -> str:
    """Render a ``lint()`` result as a human-readable report."""
    lines: list[str] = []
    count = result.get("violation_count", 0)
    rules_run = result.get("rules_run", [])
    if not rules_run:
        lines.append("No rules executed.")
    else:
        lines.append(f"Ran rules: {', '.join(rules_run)}")

    if count == 0:
        lines.append("No architectural violations found.")
    else:
        lines.append(f"Found {count} violation(s):")
        for violation in result.get("violations", []):
            lines.append(
                f"- [{violation['rule']}] {violation['node_id']}: "
                f"{violation['message']}"
            )

    for warning in result.get("warnings", []):
        lines.append(f"Warning: {warning}")

    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd lint``.

    Exit code is ``0`` on a clean graph and ``1`` when any violation was
    reported, regardless of output format.
    """
    parser = argparse.ArgumentParser(
        prog="wd lint",
        description=(
            "Lint the graph for architectural violations (dead code, layer "
            "inversion, missing metadata). Loads .weld/lint-rules.yaml when "
            "present. Exits non-zero on violations."
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
    args = parser.parse_args(argv)

    graph = Graph(args.root)
    graph.load()

    rule_filter = list(args.rule) if args.rule is not None else None
    result = lint(graph, rule_ids=rule_filter, root=args.root)

    if args.json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_text(result))

    return 1 if result["violation_count"] > 0 else 0
