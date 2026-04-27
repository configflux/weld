"""Write onboarding assets for a specific agent framework.

Supports per-framework targets so each agent framework gets files in its
native location:

    wd bootstrap claude   -> .claude/commands/weld.md
    wd bootstrap codex    -> .codex/skills/weld/SKILL.md, .codex/config.toml
    wd bootstrap copilot  -> .github/skills/weld/SKILL.md
                             + .github/instructions/weld.instructions.md

All targets also write .weld/README.md and bootstrap discover.yaml if missing.

Opt-out flags:

    --no-mcp     For codex, drop .codex/config.toml entirely. For copilot /
                 claude, swap the skill / instruction / command templates
                 to their ``.cli.md`` variants that omit MCP mentions.
    --no-enrich  Swap to the ``.cli.md`` variant that omits ``wd enrich``
                 and manual-enrichment guidance. Applies to all frameworks.
    --cli-only   Convenience alias = ``--no-mcp --no-enrich``.

Each user-facing markdown template ships both ``*.md`` (default) and
``*.cli.md`` (opt-out) siblings. When any of the opt-out flags applies to
a file, the ``.cli.md`` variant is written instead. There is intentionally
one opt-out variant per file rather than separate ``no-mcp`` and
``no-enrich`` permutations -- the trade-off is per ADR 0013 and the plan
for this issue.

Federation awareness is layered in-code, not via yet another template
variant: if ``.weld/workspaces.yaml`` is present at the bootstrap target
root, a short federation paragraph is appended to every topology-dependent
markdown asset (copilot skill + instruction, codex skill, claude command).
This composes cleanly with ``--cli-only`` without spawning a federation.*
template matrix.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from weld.bootstrap_writer import process_template_dest
from weld.workspace_state import find_workspaces_yaml

# Federation guidance appended in-code (rather than a 4x template matrix) to
# every topology-dependent markdown asset when the bootstrap target root has
# a ``.weld/workspaces.yaml`` sentinel. Choosing code-side append keeps the
# template surface minimal: default + .cli.md per file (two variants total),
# and the federation paragraph composes cleanly with --cli-only / --no-mcp /
# --no-enrich without spawning federation.cli.md permutations.
_FEDERATION_PARAGRAPH = """
## Federation mode

<!-- weld-managed:start name=federation -->
This workspace is a polyrepo (`.weld/workspaces.yaml` present). Root
discovery is federated. Use `wd workspace status` to list child repos, then
re-run `wd brief` or `wd query` inside the child you want to inspect.
<!-- weld-managed:end name=federation -->
"""

# Templates whose content depends on repo topology. The bootstrap appends
# ``_FEDERATION_PARAGRAPH`` to these files when the target root carries a
# workspaces.yaml sentinel. Non-markdown templates (README, discover.yaml,
# codex_mcp_config.toml) are excluded -- their content is topology-agnostic.
_TOPOLOGY_DEPENDENT_TEMPLATES: frozenset[str] = frozenset({
    "weld_cmd_claude.md",
    "weld_cmd_claude.cli.md",
    "weld_skill_codex.md",
    "weld_skill_codex.cli.md",
    "weld_skill_copilot.md",
    "weld_skill_copilot.cli.md",
    "weld_instructions_copilot.md",
    "weld_instructions_copilot.cli.md",
})

# Framework registry: name -> [(template filename, destination relative to root)]
# MCP pair: same shape, but the pair is dropped entirely when --no-mcp is set.
_FRAMEWORKS: dict[str, tuple[tuple[str, Path], ...]] = {
    "claude": (
        ("weld_cmd_claude.md", Path(".claude") / "commands" / "weld.md"),
    ),
    "codex": (
        ("weld_skill_codex.md", Path(".codex") / "skills" / "weld" / "SKILL.md"),
    ),
    "copilot": (
        ("weld_skill_copilot.md", Path(".github") / "skills" / "weld" / "SKILL.md"),
        (
            "weld_instructions_copilot.md",
            Path(".github") / "instructions" / "weld.instructions.md",
        ),
    ),
}

# MCP-carrying pairs keyed by framework. These are added to the write list
# only when MCP is enabled. Codex is the only framework whose default
# bootstrap writes an MCP config file; copilot/claude only mention MCP in
# their markdown templates, so their MCP opt-out is handled via the
# ``.cli.md`` variant swap rather than an extra file.
_MCP_PAIRS: dict[str, tuple[tuple[str, Path], ...]] = {
    "codex": (
        ("codex_mcp_config.toml", Path(".codex") / "config.toml"),
    ),
}

_README_TEMPLATE = "weld_readme.md"


def _templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def _cli_variant(template_name: str) -> str:
    """Return the ``.cli.md`` sibling name for *template_name*.

    The swap is only defined for markdown templates. Non-markdown templates
    (e.g. ``codex_mcp_config.toml``) do not have a CLI variant because
    ``--no-mcp`` drops them from the write list entirely.
    """
    if template_name.endswith(".md"):
        return template_name[: -len(".md")] + ".cli.md"
    raise ValueError(
        f"no .cli.md variant defined for non-markdown template: {template_name!r}"
    )


def _render_template(template_name: str, *, append: str | None = None) -> str:
    """Return the bundled template's rendered content with optional appendix.

    Centralises the "template + optional federation paragraph" shape so the
    same content can be written, diffed, or compared against an existing
    file without divergence.
    """
    src = _templates_dir() / template_name
    if not src.is_file():
        raise FileNotFoundError(f"missing bundled template: {src}")
    content = src.read_text(encoding="utf-8")
    if append:
        # Ensure exactly one blank line between template body and appendix
        # so the appended section is a well-formed markdown block regardless
        # of whether the source ends with a trailing newline.
        if not content.endswith("\n"):
            content += "\n"
        if not content.endswith("\n\n"):
            content += "\n"
        content += append.lstrip("\n")
    return content


def _process_template(
    template_name: str,
    dest: Path,
    *,
    force: bool,
    diff: bool,
    cwd: Path | None,
    append: str | None,
    framework: str,
    include_unmanaged: bool = False,
) -> bool:
    """Render template, then write/diff/compare against *dest*.

    Region-aware (ADR 0033): when the template carries managed-region
    markers, comparison is scoped inside the markers and operator-curated
    content outside the markers is left untouched. Whole-file behaviour is
    preserved for templates without markers.

    Returns True when a diff/refusal was signalled, False on no-op or write.
    """
    rendered = _render_template(template_name, append=append)
    display = _display_path(dest, cwd=cwd)
    return process_template_dest(
        rendered, dest, display,
        force=force, diff=diff,
        framework=framework, include_unmanaged=include_unmanaged,
    )


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
    no_mcp: bool = False,
    no_enrich: bool = False,
    cli_only: bool = False,
    diff: bool = False,
    include_unmanaged: bool = False,
) -> int:
    """Write (or diff) onboarding assets for *framework* into *root*.

    Parameters
    ----------
    framework:
        Registered framework name (``claude``, ``codex``, ``copilot``).
    root:
        Project root directory.
    force:
        Overwrite existing files when True.
    no_mcp:
        Drop MCP-carrying files (codex ``config.toml``) and swap any
        MCP-mentioning markdown template to its ``.cli.md`` variant.
    no_enrich:
        Swap any markdown template that contains ``wd enrich`` /
        manual-enrichment guidance to its ``.cli.md`` variant.
    cli_only:
        Convenience alias for ``no_mcp=True, no_enrich=True``.
    diff:
        When True, print unified diffs for every template whose on-disk
        copy differs from the bundled version and return the count of
        differing files. Nothing is written in diff mode; opt-out and
        federation behaviour are still honoured for the comparison target.

    Returns the number of files with diffs when ``diff=True`` (0 when all
    targeted assets match the bundled templates); always 0 in write mode.
    """
    if framework not in _FRAMEWORKS:
        raise ValueError(
            f"unknown framework: {framework!r} "
            f"(expected one of {', '.join(sorted(_FRAMEWORKS))})"
        )

    # cli_only folds into the two underlying flags so downstream logic only
    # has to look at no_mcp / no_enrich.
    if cli_only:
        no_mcp = True
        no_enrich = True

    # Any opt-out triggers the .cli.md variant for markdown templates that
    # carry MCP or enrich content. The variant strips both concerns, which
    # keeps the template surface small (one default + one opt-out per file).
    use_cli_variant = no_mcp or no_enrich

    root = root.resolve()

    # Detect federation topology. When ``.weld/workspaces.yaml`` is present
    # at *root*, append a federation paragraph to every topology-dependent
    # markdown asset. Reuses ``find_workspaces_yaml`` -- no new detector.
    # See ``_FEDERATION_PARAGRAPH`` for the rationale (code-side append vs
    # federation.* template variants).
    federation_mode = find_workspaces_yaml(root) is not None

    diff_count = 0

    def _run(template_name: str, dest: Path, append: str | None) -> None:
        nonlocal diff_count
        if _process_template(
            template_name, dest,
            force=force, diff=diff, cwd=root, append=append,
            framework=framework, include_unmanaged=include_unmanaged,
        ):
            diff_count += 1

    # 1. Write .weld/README.md (no variant swap -- README content is stable).
    _run(_README_TEMPLATE, root / ".weld" / "README.md", None)

    # 2. Framework-specific markdown assets (skill / instructions / command).
    for template_name, rel_dest in _FRAMEWORKS[framework]:
        effective_name = (
            _cli_variant(template_name) if use_cli_variant else template_name
        )
        append = (
            _FEDERATION_PARAGRAPH
            if federation_mode
            and effective_name in _TOPOLOGY_DEPENDENT_TEMPLATES
            else None
        )
        _run(effective_name, root / rel_dest, append)

    # 3. MCP config files -- written only when MCP is enabled. MCP config is
    # topology-agnostic (it configures servers, not discovery shape), so no
    # federation paragraph is appended here.
    if not no_mcp:
        for template_name, rel_dest in _MCP_PAIRS.get(framework, ()):
            _run(template_name, root / rel_dest, None)

    # 4. Bootstrap discover.yaml if missing. ``discover.yaml`` is generated
    # content, not a template copy, so it stays outside the diff/force
    # surface -- existing files keep the silent-skip behaviour.
    if diff:
        return diff_count
    discover_path = root / ".weld" / "discover.yaml"
    if discover_path.is_file():
        print("discover.yaml already exists, skipping.")
    else:
        from weld.init import init as init_bootstrap

        init_bootstrap(root, discover_path)
    return 0


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``wd bootstrap``."""
    parser = argparse.ArgumentParser(
        prog="wd bootstrap",
        description="Write onboarding assets for an agent framework",
    )
    sub = parser.add_subparsers(dest="framework", required=True)

    for name in sorted(_FRAMEWORKS):
        dests = ", ".join(
            str(dest)
            for _, dest in (*_FRAMEWORKS[name], *_MCP_PAIRS.get(name, ()))
        )
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

    args = parser.parse_args(argv)

    # --include-unmanaged only affects the diff path: it toggles whole-file vs
    # region-scoped comparison (ADR 0033). In write mode the flag is a silent
    # no-op, which is confusing UX -- reject the combination at parse time so
    # operators get a clear, argparse-style error (exit code 2 to stderr).
    if args.include_unmanaged and not args.diff:
        parser.error("--include-unmanaged requires --diff")

    try:
        diff_count = bootstrap(
            args.framework,
            args.root,
            force=args.force,
            no_mcp=args.no_mcp,
            no_enrich=args.no_enrich,
            cli_only=args.cli_only,
            diff=args.diff,
            include_unmanaged=args.include_unmanaged,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[weld] error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.diff:
        # --diff exits with 1 when any target differs, 0 otherwise.
        raise SystemExit(1 if diff_count else 0)


if __name__ == "__main__":
    main()
