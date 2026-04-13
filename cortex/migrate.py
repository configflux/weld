"""Downstream migration tooling for `kg` -> `cortex`.

This module powers the `cortex migrate` subcommand. It migrates an adopter
project from the legacy `kg` toolkit layout to the renamed `cortex` layout,
as specified in ADR 0019.

The migration is intentionally scoped to three mechanical file edits:

1. Rename the `.kg/` data directory to `.cortex/` (only if `.cortex/` does
   not already exist — we never destructively merge).
2. Patch `.mcp.json` to rename the `"kg"` MCP server entry to `"cortex"`
   and update the module path from `kg.mcp_server` to `cortex.mcp_server`.
3. Rewrite `.gitignore` entries that match `.kg/*.tmp.*` to
   `.cortex/*.tmp.*`.

Anything that requires judgement (renaming `.claude/commands/kg.md`,
updating `.claude/settings.json` `Bash(kg)` entries, moving the codex
skill directory, etc.) is *scanned* and reported as a manual-action list
— `cortex migrate` never touches those files itself.

The entire migration is idempotent: running it twice is safe and the
second run is a no-op.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------

@dataclass
class MigrationReport:
    """Structured report of what migrate did.

    Attributes:
        migrated: items that were changed on this run
        skipped:  items that were not changed (already migrated or absent)
        manual:   items the adopter must handle manually (with instructions)
    """

    migrated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)

# ---------------------------------------------------------------------------
# Individual steps
# ---------------------------------------------------------------------------

def _migrate_data_dir(root: Path, report: MigrationReport) -> None:
    """Rename `.kg/` -> `.cortex/` when safe."""
    kg_dir = root / ".kg"
    cortex_dir = root / ".cortex"

    if not kg_dir.exists():
        report.skipped.append(".kg/ not present — nothing to rename")
        return

    if cortex_dir.exists():
        report.skipped.append(
            ".kg/ found but .cortex/ already exists — leaving .kg/ in place "
            "so you can resolve the collision manually"
        )
        return

    kg_dir.rename(cortex_dir)
    report.migrated.append("renamed .kg/ -> .cortex/")

def _migrate_mcp_json(root: Path, report: MigrationReport) -> None:
    """Patch `.mcp.json` to rename the kg server entry to cortex."""
    mcp_path = root / ".mcp.json"
    if not mcp_path.is_file():
        report.skipped.append(".mcp.json not present — nothing to patch")
        return

    try:
        raw = mcp_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        report.skipped.append(f".mcp.json could not be parsed: {exc}")
        return

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        report.skipped.append(".mcp.json has no mcpServers dict")
        return

    if "kg" not in servers:
        report.skipped.append(".mcp.json already migrated or has no kg entry")
        return

    kg_entry = servers.pop("kg")
    # Update the module path inside args if present.
    args = kg_entry.get("args")
    if isinstance(args, list):
        kg_entry["args"] = [
            _rename_kg_mcp_arg(arg) if isinstance(arg, str) else arg
            for arg in args
        ]

    # Preserve ordering: insert the cortex entry at the position kg occupied.
    # Python 3.7+ dicts preserve insertion order. We rebuild `servers` so the
    # cortex entry slots into the same place kg used to be.
    rebuilt: dict[str, object] = {}
    for key, value in servers.items():
        rebuilt[key] = value
    rebuilt["cortex"] = kg_entry
    data["mcpServers"] = rebuilt

    mcp_path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
    report.migrated.append("patched .mcp.json (kg -> cortex)")

def _rename_kg_mcp_arg(arg: str) -> str:
    """Rewrite a single `-m kg.mcp_server`-style arg to its cortex form.

    Only rewrites module-path-shaped values (``kg.<whatever>``) to avoid
    accidentally munging an unrelated arg that happens to be the bare
    string ``"kg"``.
    """
    if arg == "kg.mcp_server":
        return "cortex.mcp_server"
    if arg.startswith("kg."):
        return "cortex." + arg[len("kg.") :]
    return arg

def _migrate_gitignore(root: Path, report: MigrationReport) -> None:
    """Rewrite `.kg/*.tmp.*` entries to `.cortex/*.tmp.*`."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        report.skipped.append(".gitignore not present — nothing to patch")
        return

    original = gitignore.read_text(encoding="utf-8")
    if ".kg/" not in original:
        report.skipped.append(".gitignore has no .kg/ entries")
        return

    new_lines = []
    changed = False
    for line in original.splitlines(keepends=True):
        # Only rewrite lines that look like the tmp-file pattern we ship.
        stripped = line.rstrip("\n").rstrip("\r")
        if stripped.strip() == ".kg/*.tmp.*":
            new_line = line.replace(".kg/*.tmp.*", ".cortex/*.tmp.*", 1)
            new_lines.append(new_line)
            changed = True
        else:
            new_lines.append(line)

    if not changed:
        report.skipped.append(
            ".gitignore has .kg references but no .kg/*.tmp.* line to rewrite"
        )
        return

    gitignore.write_text("".join(new_lines), encoding="utf-8")
    report.migrated.append("patched .gitignore (.kg/*.tmp.* -> .cortex/*.tmp.*)")

# ---------------------------------------------------------------------------
# Manual-action scanner
# ---------------------------------------------------------------------------

# Files we know need a manual rename or content edit. Each entry is
# (relative path, instruction).
_MANUAL_RENAMES: tuple[tuple[str, str], ...] = (
    (
        ".claude/commands/kg.md",
        "rename to .claude/commands/cortex.md",
    ),
    (
        ".claude/agents/kg.md",
        "rename to .claude/agents/cortex.md",
    ),
    (
        ".claude/commands/enrich-kg.md",
        "rename to .claude/commands/enrich-cortex.md",
    ),
    (
        ".codex/skills/kg/SKILL.md",
        "move to .codex/skills/cortex/SKILL.md",
    ),
)

def _scan_manual_items(root: Path, report: MigrationReport) -> None:
    """Scan for files that require a manual rename/edit."""
    for rel, instruction in _MANUAL_RENAMES:
        path = root / rel
        if path.exists():
            report.manual.append(f"{rel}: {instruction}")

    # Also look for `Bash(kg)` permission entries in .claude/settings.json.
    settings = root / ".claude" / "settings.json"
    if settings.is_file():
        try:
            content = settings.read_text(encoding="utf-8")
        except OSError:
            return
        # Cheap substring scan — we do not re-parse JSON because the file
        # may include comments or trailing commas in some setups.
        if "Bash(kg" in content:
            report.manual.append(
                ".claude/settings.json: replace any `Bash(kg)` or "
                "`Bash(kg *)` permission entries with the cortex equivalents"
            )

# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def migrate(root: Path) -> MigrationReport:
    """Run the full migration in *root*.

    Returns a :class:`MigrationReport` describing what was changed, skipped,
    and what the adopter must still do manually.

    This function is idempotent: calling it on a directory that is already
    migrated is safe and produces an empty ``migrated`` list.
    """
    root = Path(root).resolve()
    report = MigrationReport()
    _migrate_data_dir(root, report)
    _migrate_mcp_json(root, report)
    _migrate_gitignore(root, report)
    _scan_manual_items(root, report)
    return report

def _print_report(report: MigrationReport) -> None:
    """Human-readable summary of what happened."""
    if report.migrated:
        print("Migrated:")
        for item in report.migrated:
            print(f"  - {item}")
    if report.skipped:
        print("Skipped:")
        for item in report.skipped:
            print(f"  - {item}")
    if report.manual:
        print("Manual action required:")
        for item in report.manual:
            print(f"  - {item}")
    if not any((report.migrated, report.skipped, report.manual)):
        print("Nothing to do — project already uses the cortex layout.")

def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``cortex migrate``."""
    parser = argparse.ArgumentParser(
        prog="cortex migrate",
        description=(
            "Migrate a project from the legacy `kg` toolkit layout to the "
            "renamed `cortex` layout (ADR 0019)."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root directory (default: current directory)",
    )
    args = parser.parse_args(argv)

    try:
        report = migrate(args.root)
    except OSError as exc:
        print(f"[cortex migrate] error: {exc}", file=sys.stderr)
        return 1

    _print_report(report)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
