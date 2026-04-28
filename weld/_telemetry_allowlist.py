"""Closed-form allowlists for the telemetry event schema.

Per ADR 0035 § "Strict allowlist event schema (v1)" the on-disk file
must contain only enum-shaped values: subcommand names, MCP tool names,
and known CLI flag names. Anything outside these sets is coerced to
``"unknown"`` (commands) or filtered out (flags) before the redactor
sees the event.

Three frozensets and two coercion helpers live here so the writer and
``wd telemetry`` subcommand share one source of truth.

Sources of truth:

- :data:`CLI_COMMANDS` mirrors the subcommand branches in
  :mod:`weld.cli` plus this task's ``"telemetry"``, the framework names
  ``"help"`` / ``"version"``, and the sentinel ``"unknown"``.
- :data:`MCP_TOOLS` is sourced at import time from
  :func:`weld._mcp_tools.build_tools`. If that import fails (e.g.,
  during a partial install or a Bazel test that omits the MCP module),
  we fall back to the 13 hard-coded names so this module always loads.
- :data:`CLI_FLAGS` is hand-curated from every ``argparse.add_argument``
  declaration under ``weld/`` plus ``--no-telemetry``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final


# ---------------------------------------------------------------------------
# CLI subcommands.
# ---------------------------------------------------------------------------


_CLI_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        # Mirror weld.cli._dispatch branches.
        "init",
        "discover",
        "agents",
        "graph",
        "workspace",
        "build-index",
        "scaffold",
        "prime",
        "doctor",
        "security",
        "bootstrap",
        "bench",
        "demo",
        "brief",
        "trace",
        "impact",
        "query",
        "find",
        "context",
        "path",
        "callers",
        "references",
        "enrich",
        "export",
        "viz",
        "watch",
        "mcp",
        "diff",
        "list",
        "stats",
        "stale",
        "dump",
        "validate",
        "validate-fragment",
        "add-node",
        "add-edge",
        "rm-node",
        "rm-edge",
        "import",
        "lint",
        # Added by this task and its follow-ups.
        "telemetry",
        # Per-verb names emitted by ``weld.telemetry_cli`` so each
        # subcommand records under a distinct ``command`` value.
        "telemetry-status",
        "telemetry-show",
        "telemetry-path",
        "telemetry-export",
        "telemetry-clear",
        "telemetry-disable",
        "telemetry-enable",
        # Framework / sentinel names.
        "help",
        "version",
        "unknown",
    }
)
"""Allowlist for CLI ``command`` field on telemetry events."""


CLI_COMMANDS: Final[frozenset[str]] = _CLI_COMMANDS


# ---------------------------------------------------------------------------
# MCP tool names.
# ---------------------------------------------------------------------------


_MCP_TOOLS_FALLBACK: Final[frozenset[str]] = frozenset(
    {
        "weld_query",
        "weld_find",
        "weld_context",
        "weld_path",
        "weld_brief",
        "weld_stale",
        "weld_callers",
        "weld_references",
        "weld_export",
        "weld_diff",
        "weld_trace",
        "weld_impact",
        "weld_enrich",
    }
)


def _build_mcp_allowlist() -> frozenset[str]:
    """Source MCP tool names from ``build_tools`` if importable.

    The ``build_tools`` factory needs adapter callables. We pass small
    no-op stubs that satisfy its signature but are never invoked.
    """
    try:
        from weld._mcp_tools import build_tools as _build_tools

        class _StubTool:
            def __init__(self, *, name: str, **_kwargs) -> None:
                self.name = name

        def _stub(*_args, **_kwargs) -> None:  # pragma: no cover - never called
            return None

        tools = _build_tools(
            weld_query=_stub,
            weld_find=_stub,
            weld_context=_stub,
            weld_path=_stub,
            weld_brief=_stub,
            weld_stale=_stub,
            weld_callers=_stub,
            weld_references=_stub,
            weld_export=_stub,
            weld_diff=_stub,
            tool_cls=_StubTool,
            weld_trace=_stub,
            weld_impact=_stub,
            weld_enrich=_stub,
        )
        names = {t.name for t in tools}
        if names:
            return frozenset(names | _MCP_TOOLS_FALLBACK)
    except Exception:
        pass
    return _MCP_TOOLS_FALLBACK


MCP_TOOLS: Final[frozenset[str]] = _build_mcp_allowlist()
"""Allowlist for MCP ``command`` field on telemetry events."""


# ---------------------------------------------------------------------------
# CLI flag names.
# ---------------------------------------------------------------------------


CLI_FLAGS: Final[frozenset[str]] = frozenset(
    {
        # Long flags scanned from every ``add_argument`` call under weld/.
        "--agent",
        "--allow-dangling",
        "--allow-empty",
        "--allow-remote",
        "--artifact",
        "--cases",
        "--children",
        "--clean",
        "--cli-only",
        "--client",
        "--compare",
        "--debounce",
        "--depth",
        "--diff",
        "--dry-run",
        "--files",
        "--force",
        "--format",
        "--full",
        "--git-init",
        "--host",
        "--ignore-all",
        "--imports-per-file",
        "--include-unmanaged",
        "--incremental",
        "--init",
        "--json",
        "--label",
        "--last",
        "--layout",
        "--limit",
        "--max-cost",
        "--max-depth",
        "--max-tokens",
        "--merge",
        "--model",
        "--no-bootstrap",
        "--no-enrich",
        "--no-mcp",
        "--no-open",
        "--no-telemetry",
        "--no-watchdog",
        "--no-write",
        "--node",
        "--out",
        "--output",
        "--platform",
        "--poll-interval",
        "--port",
        "--print",
        "--prompts",
        "--props",
        "--provider",
        "--quality",
        "--recurse",
        "--report",
        "--root",
        "--rule",
        "--safe",
        "--security",
        "--seed-limit",
        "--source-label",
        "--strict",
        "--task",
        "--tasks",
        "--top",
        "--track-graphs",
        "--type",
        "--write",
        "--write-root-graph",
        "--yes",
        # Short flags.
        "-f",
        "-o",
        # Standard long flags every parser supports.
        "--help",
        "--version",
    }
)
"""Allowlist for the ``flags`` field on telemetry events."""


# ---------------------------------------------------------------------------
# Coercion helpers.
# ---------------------------------------------------------------------------


def coerce_command(surface: str, name: str) -> str:
    """Return ``name`` if it is allowlisted for ``surface``, else ``"unknown"``.

    ``surface`` must be ``"cli"`` or ``"mcp"``. Any other value short-circuits
    to ``"unknown"``.
    """
    if not isinstance(name, str):
        return "unknown"
    if surface == "cli":
        return name if name in CLI_COMMANDS else "unknown"
    if surface == "mcp":
        return name if name in MCP_TOOLS else "unknown"
    return "unknown"


def coerce_flags(flag_names: Iterable[str]) -> list[str]:
    """Return the sorted, deduplicated, allowlisted subset of ``flag_names``.

    Strings outside :data:`CLI_FLAGS` are silently dropped. Non-string
    inputs in the iterable are skipped. The output preserves no insertion
    order -- the caller gets a stable sorted list.
    """
    seen: set[str] = set()
    for f in flag_names or ():
        if isinstance(f, str) and f in CLI_FLAGS:
            seen.add(f)
    return sorted(seen)
