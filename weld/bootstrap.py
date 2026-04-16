"""Write onboarding assets for a specific agent framework.

Supports per-framework targets so each agent framework gets files in its
native location:

    wd bootstrap claude   -> .claude/commands/weld.md
    wd bootstrap codex    -> .codex/skills/weld/SKILL.md, .codex/config.toml
    wd bootstrap copilot  -> .github/skills/weld/SKILL.md

All targets also write .weld/README.md and bootstrap discover.yaml if missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Framework registry: name -> [(template filename, destination relative to root)]
_FRAMEWORKS: dict[str, tuple[tuple[str, Path], ...]] = {
    "claude": (
        ("weld_cmd_claude.md", Path(".claude") / "commands" / "weld.md"),
    ),
    "codex": (
        ("weld_skill_codex.md", Path(".codex") / "skills" / "weld" / "SKILL.md"),
        ("codex_mcp_config.toml", Path(".codex") / "config.toml"),
    ),
    "copilot": (
        ("weld_skill_copilot.md", Path(".github") / "skills" / "weld" / "SKILL.md"),
    ),
}

_README_TEMPLATE = "weld_readme.md"

def _templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"

def _write_template(
    template_name: str,
    dest: Path,
    *,
    force: bool = False,
    cwd: Path | None = None,
) -> bool:
    """Copy a bundled template to *dest*.

    Returns True if the file was written, False if skipped.
    """
    src = _templates_dir() / template_name
    if not src.is_file():
        raise FileNotFoundError(f"missing bundled template: {src}")

    if dest.exists() and not force:
        display = _display_path(dest, cwd=cwd)
        print(f"{display} already exists, skipping.")
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {_display_path(dest, cwd=cwd)}")
    return True

def _display_path(path: Path, *, cwd: Path | None = None) -> str:
    ref = cwd or Path.cwd()
    try:
        return str(path.relative_to(ref))
    except ValueError:
        return str(path)

def bootstrap(
    framework: str,
    root: Path,
    *,
    force: bool = False,
) -> None:
    """Write onboarding assets for *framework* into *root*."""
    if framework not in _FRAMEWORKS:
        raise ValueError(
            f"unknown framework: {framework!r} "
            f"(expected one of {', '.join(sorted(_FRAMEWORKS))})"
        )

    root = root.resolve()

    # 1. Write .weld/README.md
    _write_template(
        _README_TEMPLATE,
        root / ".weld" / "README.md",
        force=force,
        cwd=root,
    )

    # 2. Write framework-specific onboarding assets
    for template_name, rel_dest in _FRAMEWORKS[framework]:
        _write_template(
            template_name,
            root / rel_dest,
            force=force,
            cwd=root,
        )

    # 3. Bootstrap discover.yaml if missing
    discover_path = root / ".weld" / "discover.yaml"
    if discover_path.is_file():
        print("discover.yaml already exists, skipping.")
    else:
        from weld.init import init as init_bootstrap

        init_bootstrap(root, discover_path)

def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``wd bootstrap``."""
    parser = argparse.ArgumentParser(
        prog="wd bootstrap",
        description="Write onboarding assets for an agent framework",
    )
    sub = parser.add_subparsers(dest="framework", required=True)

    for name in sorted(_FRAMEWORKS):
        dests = ", ".join(str(dest) for _, dest in _FRAMEWORKS[name])
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

    args = parser.parse_args(argv)

    try:
        bootstrap(args.framework, args.root, force=args.force)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[weld] error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

if __name__ == "__main__":
    main()
