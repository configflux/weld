"""Human-readable rendering of a blast-radius result.

Carved out of :mod:`weld.impact_core` for the reason :mod:`weld._graph_match`
was (bd jkir): the ADR 0107 aggregation-node rule pushed that module past the
400-line cap, and the cheapest local fix -- deleting the comment that explains
why a ``package`` node is not a dependency-graph participant -- is worse than
the right one.

This function is the natural thing to move, because it is the only part of
``impact_core`` its own module docstring rules out: that module promises "no
argparse, no git, no IO", and a presentation layer is what a renderer is.
Nothing here reads the graph; it takes the finished envelope and formats it.

:mod:`weld.impact_core` re-imports ``format_human`` so
``from weld.impact_core import format_human`` keeps working -- ``weld.impact``,
``weld.impact_cli`` and a backwards-compat test in
``weld/tests/weld_impact_cli_test.py`` all address it there.
"""

from __future__ import annotations


def format_human(result: dict) -> str:
    """Render an impact result as a short human-readable summary."""
    target_input = result["target"]["input"]
    if isinstance(target_input, list):
        target_str = ", ".join(target_input) if target_input else "(none)"
    else:
        target_str = str(target_input)
    lines = [
        f"Target: {target_str}",
        f"Resolved nodes: {len(result['target']['resolved_nodes'])}",
        f"Risk: {result['risk_level']}",
        f"Direct dependents: {len(result['direct_dependents'])}",
        f"Transitive dependents: {len(result['transitive_dependents'])}",
    ]
    surfaces = result["affected_surfaces"]
    if any(surfaces.values()):
        lines.append("Affected surfaces:")
        lines.append(f"- CLI commands: {len(surfaces['cli_commands'])}")
        lines.append(f"- MCP tools: {len(surfaces['mcp_tools'])}")
        # ``.get`` like the ``tests`` line below it, and for the same reason:
        # this renderer is fed envelopes built before the bucket existed --
        # test fixtures and any caller holding a stored result (bd 7rla).
        lines.append(f"- Repo tools: {len(surfaces.get('repo_tools', []))}")
        lines.append(f"- API endpoints: {len(surfaces['api_endpoints'])}")
        lines.append(f"- Entry points: {len(surfaces['entrypoints'])}")
        lines.append(f"- Boundaries: {len(surfaces['boundaries'])}")
        lines.append(f"- Tests: {len(surfaces.get('tests', []))}")
    warnings = result.get("warnings") or {}
    if isinstance(warnings, dict):
        for message in warnings.get("messages") or []:
            lines.append(f"Warning: {message}")
        if warnings.get("stale_graph"):
            lines.append("Warning: graph is stale (--allow-stale)")
        if warnings.get("unresolved_callsites"):
            lines.append(
                f"Warning: unresolved callsites touched: {warnings['unresolved_callsites']}",
            )
        if warnings.get("speculative_edges"):
            lines.append(
                f"Warning: speculative edges traversed: {warnings['speculative_edges']}",
            )
        out_of_scope = warnings.get("out_of_scope_inputs") or []
        if out_of_scope:
            lines.append(f"Warning: out-of-scope inputs: {', '.join(out_of_scope)}")
        low_capability = warnings.get("low_capability_inputs") or []
        if low_capability:
            lines.append(
                f"Warning: low-capability inputs: {', '.join(low_capability)}",
            )
    return "\n".join(lines) + "\n"


__all__ = ["format_human"]
