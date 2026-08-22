"""``Violation`` -- the dependency-free leaf under the arch_lint rule registry.

Split out of :mod:`weld.arch_lint` (bd 5038-mx8sd, ADR 0130 disposition #6):
``arch_lint.py`` is the lint-rule-registry facade -- it top-level-imports
every rule module (``_graph_closure_invariants``,
``_graph_edge_provenance_lint``, ``_graph_strategy_pair``,
``arch_lint_coverage``, ``arch_lint_cycles``, ``arch_lint_orphan``), and
those six rule modules needed the shared ``Violation`` dataclass back from
``arch_lint.py`` to construct their return values -- a real back-edge,
previously worked around with a ``TYPE_CHECKING``-guarded import for the
annotation plus a function-local "late import to break cycle" import for
the runtime construction, in each of the six. That made an 8-member import
cycle (``file:weld/_graph_closure_invariants`` in
``wd lint --rule no-circular-deps``): the anchor happened to also be this
repo's own textbook example of the pattern (``weld/arch_lint_cycles.py``'s
``from weld.arch_lint import Violation  # late import to break cycle``).

This module holds no import of :mod:`weld.arch_lint` or any rule-module
sibling, so nothing importing it can cycle back. ``arch_lint.py`` imports
``Violation`` from here and re-exports it via ``__all__`` for its existing
public surface (``from weld.arch_lint import Violation`` keeps working
unchanged for external callers -- CI scripts, the MCP server, tests); the
six rule modules import it from here directly at real top level instead of
late-importing it from ``arch_lint.py``, which is what breaks the cycle.

Not dissolved by this move alone: ``weld/arch_lint_cli.py`` had its own,
unrelated back-edge into ``arch_lint.py`` (it needed ``available_rule_ids``
and ``lint`` -- the rule registry and runner themselves, not a shared
type -- to implement ``main()``). That was a registry/runner dependency,
not a shared-type one, so it needed a different fix -- dependency
injection, the shape ADR 0130 disposition #7 used for
``weld/_mcp_dispatch`` -- landed separately (bd 5038-efr7z). See
``weld/arch_lint_cli.py``'s own module docstring for that shape.
"""

from __future__ import annotations

from dataclasses import dataclass


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


__all__ = ["Violation"]
