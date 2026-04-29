"""Check Weld setup status and suggest next steps.

Inspects the .weld/ directory and prints human-readable guidance so a user
or agent knows exactly what to do next.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Known frameworks the agent matrix can cover. Order here is the stable display
# order used when rendering the matrix.
_KNOWN_FRAMEWORKS: tuple[str, ...] = ("copilot", "codex", "claude")

# Accepted --agent values. "auto" inspects the environment to guess the active
# agent, "all" forces every known framework into the matrix, and a framework
# name forces just that framework.
_AGENT_CHOICES: tuple[str, ...] = ("auto", "all", *_KNOWN_FRAMEWORKS)

def _status(tag: str, msg: str) -> str:
    return f"  [{tag:6s}] {msg}"

def _action(msg: str, cmd: str) -> tuple[str, str]:
    """Return a formatted status line and a next-step entry."""
    line = _status("ACTION", msg) + f"\n           -> Run: {cmd}"
    return line, cmd

def _check_discover_yaml(weld_dir: Path) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    steps: list[str] = []
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        line, cmd = _action("discover.yaml not found", "wd init")
        lines.append(line)
        steps.append(cmd)
    else:
        count = _count_active_sources(path)
        lines.append(_status("OK", f"discover.yaml exists ({count} active source{'s' if count != 1 else ''})"))
    return lines, steps

def _count_active_sources(path: Path) -> int:
    try:
        from weld._yaml import parse_yaml

        data = parse_yaml(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            sources = data.get("sources", [])
            if isinstance(sources, list):
                return len(sources)
    except Exception:
        pass
    return 0

def _check_graph_json(weld_dir: Path, root: Path) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    steps: list[str] = []
    path = weld_dir / "graph.json"
    if not path.is_file():
        line, cmd = _action("graph.json not found", "wd discover --output .weld/graph.json")
        lines.append(line)
        steps.append(cmd)
        return lines, steps

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        lines.append(_status("ACTION", "graph.json is invalid or unreadable"))
        steps.append("wd discover --output .weld/graph.json")
        return lines, steps

    # Staleness check
    stale_lines, stale_steps = _check_staleness(data, root)
    if stale_lines:
        lines.extend(stale_lines)
        steps.extend(stale_steps)
    else:
        lines.append(_status("OK", "graph.json exists and is up to date"))

    # Size check
    nodes = data.get("nodes", {})
    total = len(nodes)
    if total < 5:
        lines.append(_status("INFO", f"Graph has only {total} node{'s' if total != 1 else ''} — consider adding more sources to discover.yaml"))

    # Description coverage
    if total > 0:
        with_desc = sum(
            1 for n in nodes.values()
            if isinstance(n, dict)
            and isinstance((n.get("props") or {}).get("description"), str)
            and (n.get("props") or {}).get("description", "").strip()
        )
        pct = round(with_desc / total * 100)
        if pct < 30:
            lines.append(_status("INFO", f"{pct}% of nodes have descriptions"))

    return lines, steps

def _check_staleness(data: dict, root: Path) -> tuple[list[str], list[str]]:
    """Report graph freshness using the ADR 0017 source-file model.

    The primary signal is ``source_stale`` -- did any tracked source file
    change between ``meta.git_sha`` and HEAD. Only that signal produces a
    ``wd discover`` action, because only that state requires a rebuild.

    SHA-only drift (``sha_behind=True`` with ``source_stale=False``) is
    reported as an advisory with no next-step action: rerunning discovery
    on a SHA-only-drift graph destroys curated descriptions and
    enrichment. ``wd touch`` is the non-destructive way to advance the
    pointer but it is an informational hint, not a required action.
    """
    from weld._git import is_git_repo
    from weld._staleness import compute_stale_info

    if not is_git_repo(root):
        return [_status("INFO", "Not a git repo — staleness check skipped")], []

    info = compute_stale_info(root / ".weld" / "graph.json", data.get("meta") or {})
    source_stale = info.get("source_stale", False)
    sha_behind = info.get("sha_behind", False)
    behind = info.get("commits_behind", -1)
    graph_sha = info.get("graph_sha")

    if source_stale:
        if graph_sha is None:
            msg = "graph.json has no git SHA — may be stale"
        elif behind == -1:
            msg = "graph.json SHA not reachable from HEAD (possible force-push)"
        elif behind > 0:
            msg = (
                f"graph.json is {behind} commit{'s' if behind != 1 else ''} "
                f"behind HEAD and tracked source files changed"
            )
        else:
            msg = "tracked source files changed since last discovery"
        line, cmd = _action(msg, "wd discover --output .weld/graph.json")
        return [line], [cmd]

    if sha_behind:
        count = behind if isinstance(behind, int) and behind > 0 else 1
        # Advisory: enrichment is preserved; no discovery required. Do NOT
        # add a step -- rebuilding would destroy curated descriptions.
        return [_status(
            "INFO",
            f"graph.json SHA is {count} commit{'s' if count != 1 else ''} "
            f"behind HEAD, but tracked sources are unchanged — enrichment "
            f"preserved (run `wd touch` to advance the pointer)",
        )], []

    return [], []

def _check_file_index(weld_dir: Path) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    steps: list[str] = []
    path = weld_dir / "file-index.json"
    if not path.is_file():
        line, cmd = _action("file-index.json not found", "wd build-index")
        lines.append(line)
        steps.append(cmd)
    else:
        lines.append(_status("OK", "file-index.json exists"))
    return lines, steps

def _agent_surfaces(root: Path) -> dict[str, dict[str, bool]]:
    """Detect which agent surfaces are present per framework.

    Returns a mapping ``{framework: {surface_name: present}}`` for copilot,
    codex, and claude. ``.mcp.json`` is a shared signal; it is reported as the
    MCP status for copilot/claude only when a framework-specific surface
    (skill / command / instruction) is present.
    """
    return {
        "copilot": {
            "skill": (root / ".github" / "skills" / "weld" / "SKILL.md").is_file(),
            "instruction": (root / ".github" / "instructions" / "weld.instructions.md").is_file(),
            "mcp": (root / ".mcp.json").is_file(),
        },
        "codex": {
            "skill": (root / ".codex" / "skills" / "weld" / "SKILL.md").is_file(),
            "mcp": (root / ".codex" / "config.toml").is_file(),
        },
        "claude": {
            "command": (root / ".claude" / "commands" / "weld.md").is_file(),
        },
    }

# Which surfaces count as "framework-specific" for the zero-surface suppression
# rule (the listing gate in _framework_is_listed). ``mcp`` for copilot is
# shared root infrastructure (``.mcp.json`` lives at repo root) and on its own
# does not indicate copilot setup, so copilot is listed only when a
# skill/instruction exists.
_FRAMEWORK_SPECIFIC_SURFACES: dict[str, tuple[str, ...]] = {
    "copilot": ("skill", "instruction"),
    "codex": ("skill",),
    "claude": ("command",),
}

# Which surfaces the bootstrap next-step hint checks for completeness. This
# must exclude surfaces that are *shared* across frameworks (copilot's
# ``.mcp.json`` lives at repo root and is wired up by any one of
# copilot/claude), while still including per-framework MCP config that lives
# *inside* the framework's own tree (codex ``.codex/config.toml``).
# A missing shared surface should not, by itself, produce a
# ``-> wd bootstrap <fw>`` hint -- the matrix row still surfaces its
# absence for visibility.
_FRAMEWORK_COMPLETENESS_SURFACES: dict[str, tuple[str, ...]] = {
    "copilot": ("skill", "instruction"),
    "codex": ("skill", "mcp"),
    "claude": ("command",),
}

# Stable surface order per framework so output is deterministic.
_SURFACE_ORDER: dict[str, tuple[str, ...]] = {
    "copilot": ("skill", "instruction", "mcp"),
    "codex": ("skill", "mcp"),
    "claude": ("command",),
}

def _framework_is_listed(fw: str, surfaces: dict[str, bool]) -> bool:
    """Return True if the framework has at least one framework-specific surface."""
    return any(surfaces.get(s, False) for s in _FRAMEWORK_SPECIFIC_SURFACES[fw])

def _framework_line(fw: str, surfaces: dict[str, bool]) -> str:
    """Render a single per-framework matrix line."""
    pairs = [
        f"{name} {'yes' if surfaces.get(name, False) else 'no'}"
        for name in _SURFACE_ORDER[fw]
    ]
    row = ", ".join(pairs)
    # The bootstrap hint fires only when a surface the framework is expected
    # to own is missing. Shared root infrastructure (e.g. ``.mcp.json``) is
    # reported in the matrix row above for visibility but does not trigger
    # a bootstrap suggestion on its own -- see
    # _FRAMEWORK_COMPLETENESS_SURFACES.
    missing = [s for s in _FRAMEWORK_COMPLETENESS_SURFACES[fw] if not surfaces.get(s, False)]
    prefix = f"            {fw + ':':9s}{row}"
    if missing:
        return f"{prefix}  -> wd bootstrap {fw}"
    return prefix

def _detect_active_agent_from_env(env: dict[str, str] | None = None) -> str | None:
    """Guess the active agent from well-known environment variables.

    Currently we only surface Codex this way because it is the case tracked issue
    targets (a Codex user not seeing Codex in the matrix). Any ``CODEX_*`` env
    var is treated as a signal that Codex is the active agent. Returns the
    framework name or ``None`` when no signal is present.
    """
    source = env if env is not None else os.environ
    for key in source:
        if key.startswith("CODEX_"):
            return "codex"
    return None

def _resolve_forced_frameworks(active_agent: str | None) -> tuple[str, ...]:
    """Translate a caller-supplied agent selector into forced-listed frameworks.

    - ``None`` or ``"auto"``: inspect the environment for a signal.
    - ``"all"``: force all known frameworks.
    - a specific framework name: force just that framework.

    Unknown values yield an empty tuple -- argparse is the gate that rejects
    bad values at the CLI boundary.
    """
    if active_agent is None or active_agent == "auto":
        detected = _detect_active_agent_from_env()
        return (detected,) if detected else ()
    if active_agent == "all":
        return _KNOWN_FRAMEWORKS
    if active_agent in _KNOWN_FRAMEWORKS:
        return (active_agent,)
    return ()

def _check_agent_integration(
    root: Path, active_agent: str | None = None,
) -> tuple[list[str], list[str]]:
    """Report a per-framework matrix of agent surfaces.

    When no framework has any surface, emit a single generic hint. Otherwise
    list each framework that has at least one framework-specific surface
    (skill/command/instruction -- MCP alone does not count), with one
    ``surface yes|no`` per column and a ``-> wd bootstrap <fw>`` next step
    when the setup is partial.

    ``active_agent`` forces additional frameworks into the matrix even when
    they have zero surfaces -- so a Codex user running ``wd prime`` sees
    ``codex: skill no, mcp no -> wd bootstrap codex`` instead of silence.
    """
    all_surfaces = _agent_surfaces(root)
    forced = _resolve_forced_frameworks(active_agent)

    listed = [
        fw for fw in _KNOWN_FRAMEWORKS
        if _framework_is_listed(fw, all_surfaces[fw]) or fw in forced
    ]

    if not listed:
        return [_status("INFO", "No agent integration found — run: wd bootstrap claude  (or: copilot, codex)")], []

    lines = [_status("INFO", "Agent surfaces:")]
    steps: list[str] = []
    for fw in listed:
        lines.append(_framework_line(fw, all_surfaces[fw]))
        # Consistency with _framework_line: only completeness surfaces drive
        # the bootstrap next-step hint. Shared root infra like ``.mcp.json``
        # appears in the matrix row but does not imply copilot/claude is
        # incomplete -- see _FRAMEWORK_COMPLETENESS_SURFACES.
        missing = [s for s in _FRAMEWORK_COMPLETENESS_SURFACES[fw] if not all_surfaces[fw].get(s, False)]
        if missing:
            steps.append(f"wd bootstrap {fw}")
    return lines, steps

def prime(root: Path, active_agent: str | None = None) -> str:
    """Run all checks and return the formatted status report.

    ``active_agent`` selects which frameworks are forced into the agent-surface
    matrix even when they have zero surfaces on disk. ``None`` (default) and
    ``"auto"`` inspect the environment; ``"all"`` forces every known framework;
    a framework name forces just that one. See _resolve_forced_frameworks.
    """
    weld_dir = root / ".weld"

    if not weld_dir.is_dir():
        return (
            "Weld is not set up yet.\n"
            "\n"
            "Get started:\n"
            "  1. wd init                       # bootstrap .weld/discover.yaml\n"
            "  2. wd discover --output .weld/graph.json   # build the graph\n"
            "  3. wd build-index                 # build the keyword index\n"
            "  4. wd bootstrap claude             # or: wd bootstrap codex\n"
            "  5. wd prime                        # confirm everything is set up\n"
        )

    all_lines: list[str] = []
    all_steps: list[str] = []

    for check in (
        lambda: _check_discover_yaml(weld_dir),
        lambda: _check_graph_json(weld_dir, root),
        lambda: _check_file_index(weld_dir),
        lambda: _check_agent_integration(root, active_agent=active_agent),
    ):
        lines, steps = check()
        all_lines.extend(lines)
        all_steps.extend(steps)

    header = f"Weld Status (.weld/ in {root})\n"
    body = "\n".join(all_lines)

    if all_steps:
        numbered = "\n".join(f"  {i}. {s}" for i, s in enumerate(all_steps, 1))
        return f"{header}\n{body}\n\nNext steps:\n{numbered}\n"

    return f"{header}\n{body}\n\nWeld is up to date. No actions needed.\n"

def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``wd prime``."""
    parser = argparse.ArgumentParser(
        prog="wd prime",
        description="Check Weld setup status and suggest next steps",
    )
    parser.add_argument(
        "--root", type=Path, default=Path("."),
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "--agent", choices=_AGENT_CHOICES, default="auto",
        help=(
            "Force a framework into the agent-surface matrix even if no "
            "surfaces are configured. 'auto' (default) infers from "
            "environment variables (e.g. CODEX_*), 'all' shows every known "
            "framework, or specify 'claude', 'codex', or 'copilot'."
        ),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = prime(root, active_agent=args.agent)
    sys.stdout.write(output)

if __name__ == "__main__":
    main()
