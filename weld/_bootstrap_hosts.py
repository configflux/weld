"""Per-host bootstrap dispatch (ADR 0054).

This module is the writer-side counterpart of :mod:`weld._bootstrap_adapters`.
``bootstrap.py`` keeps the original claude/codex/copilot logic untouched and
delegates here for the four new hosts (cursor, aider, gemini-cli, copilot-cli).

The dispatch is deliberately small: render the skill and config strings via
the adapter module, then feed both through the existing
:func:`weld.bootstrap_writer.process_template_dest` machinery so the diff,
force, idempotence, and managed-region semantics from ADR 0033 apply
uniformly. Hosts that do not write a config file (none today; reserved for
future hosts) get the config-write step skipped.

Split from :mod:`weld.bootstrap` to keep both files under the 400-line cap
without compressing real code. Also hosts the shared argparse subparser
builder so the legacy and ADR-0054 hosts share one source of truth for the
flag surface.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from weld._bootstrap_adapters import (
    HostBootstrap,
    host_registry,
    host_spec,
    render_config,
    render_skill,
)
from weld.bootstrap_writer import process_template_dest


def per_host_names() -> tuple[str, ...]:
    """Return the host names registered for ADR-0054 dispatch in stable order."""
    return tuple(h.name for h in host_registry())


def per_host_targets(name: str) -> tuple[Path, ...]:
    """Return the relative target paths for host *name* (skill, then config).

    Used by the CLI ``--help`` summary so each new host's parser node lists
    the files it writes. Order matches the human-friendly ``skill, mcp``
    reading order.
    """
    spec = host_spec(name)
    targets: list[Path] = [spec.skill_path]
    if spec.config_path is not None:
        targets.append(spec.config_path)
    return tuple(targets)


def _display(path: Path, *, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


def dispatch_per_host_bootstrap(
    name: str,
    root: Path,
    *,
    force: bool,
    diff: bool,
    no_mcp: bool,
    include_unmanaged: bool,
) -> int:
    """Run the per-host bootstrap pipeline for *name* into *root*.

    Mirrors the contract of :func:`weld.bootstrap.bootstrap`:

    * Renders skill content (canonical body + host overlay + optional wiki
      fallback) and writes it via :func:`process_template_dest` so the file is
      seeded on first run and managed-region drift is restored on
      ``--force``.
    * Writes the host's MCP/config file when ``supports_mcp`` is true and
      ``--no-mcp`` is not set. Hosts without native MCP support never write a
      config file; the config_path slot is reserved for future no-MCP hosts
      that still want a config (none today).
    * Returns the count of files signalling a diff/refusal when ``diff=True``;
      always 0 in write mode (preserving the existing CLI contract).

    The ``--no-enrich`` and ``--cli-only`` flags do not change behaviour for
    the new hosts -- the canonical body is the same for both MCP-supporting
    and no-MCP variants, and the wiki-fallback stanza is gated on
    ``supports_mcp`` rather than the opt-out flag. This keeps the per-host
    template surface flat (one body string, no ``.cli.md`` permutation).
    """
    spec = host_spec(name)

    diff_count = 0

    # 1. Skill / conventions file. Always written (regardless of --no-mcp)
    # because the wiki fallback is the substitute path for hosts that cannot
    # speak MCP, and the canonical body is independent of MCP.
    skill_rendered = render_skill(name)
    skill_dest = root / spec.skill_path
    if process_template_dest(
        skill_rendered,
        skill_dest,
        _display(skill_dest, cwd=root),
        force=force,
        diff=diff,
        framework=name,
        include_unmanaged=include_unmanaged,
    ):
        diff_count += 1

    # 2. Config file (MCP JSON for cursor/gemini/copilot-cli, YAML for aider).
    # Hosts with ``supports_mcp=True`` honour ``--no-mcp`` by skipping the
    # config file entirely (matching the codex contract). Hosts with
    # ``supports_mcp=False`` carry a non-MCP config (aider's YAML pointing at
    # CONVENTIONS.md) so ``--no-mcp`` is a no-op for them.
    if spec.config_path is not None and not (spec.supports_mcp and no_mcp):
        config_rendered = render_config(name)
        if config_rendered is not None:
            config_dest = root / spec.config_path
            if process_template_dest(
                config_rendered,
                config_dest,
                _display(config_dest, cwd=root),
                force=force,
                diff=diff,
                framework=name,
                include_unmanaged=include_unmanaged,
            ):
                diff_count += 1

    return diff_count


def add_framework_subparser(
    sub: argparse._SubParsersAction, name: str, dests: str,
) -> None:
    """Register one framework subparser with the shared ``wd bootstrap`` flag set.

    Factored out so the legacy claude/codex/copilot path and the ADR-0054
    host registry share one source of truth for ``--force / --diff /
    --no-mcp / --no-enrich / --cli-only / --include-unmanaged``. Adding a
    new flag means one edit here, not one per host.
    """
    fw_parser = sub.add_parser(
        name,
        help=f"Write onboarding assets for {name} (-> {dests})",
    )
    fw_parser.add_argument(
        "--root", type=Path, default=Path("."),
        help="Project root directory (default: current directory)",
    )
    fw_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing files",
    )
    fw_parser.add_argument(
        "--diff", action="store_true",
        help=(
            "Print unified diffs between bundled templates and the "
            "on-disk copies without writing; exits 1 when any diffs "
            "are found, 0 otherwise."
        ),
    )
    fw_parser.add_argument(
        "--no-mcp", action="store_true", dest="no_mcp",
        help=(
            "Do not write MCP configuration, and strip MCP mentions "
            "from generated markdown"
        ),
    )
    fw_parser.add_argument(
        "--no-enrich", action="store_true", dest="no_enrich",
        help="Strip wd enrich guidance from generated markdown",
    )
    fw_parser.add_argument(
        "--cli-only", action="store_true", dest="cli_only",
        help="Shortcut for --no-mcp --no-enrich",
    )
    fw_parser.add_argument(
        "--include-unmanaged",
        action="store_true",
        dest="include_unmanaged",
        help=(
            "With --diff, fall back to the whole-file unified diff "
            "(default --diff is region-scoped per ADR 0033). "
            "Requires --diff; rejected otherwise."
        ),
    )


__all__ = [
    "HostBootstrap",
    "add_framework_subparser",
    "dispatch_per_host_bootstrap",
    "host_registry",
    "host_spec",
    "per_host_names",
    "per_host_targets",
]
