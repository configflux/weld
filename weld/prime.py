"""Check Weld setup status and suggest next steps.

Inspects the .weld/ directory and prints human-readable guidance so a user
or agent knows exactly what to do next.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
        line, cmd = _action("graph.json not found", "wd discover > .weld/graph.json")
        lines.append(line)
        steps.append(cmd)
        return lines, steps

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        lines.append(_status("ACTION", "graph.json is invalid or unreadable"))
        steps.append("wd discover > .weld/graph.json")
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
    from weld._git import commits_behind, get_git_sha, is_git_repo

    if not is_git_repo(root):
        return [_status("INFO", "Not a git repo — staleness check skipped")], []

    current_sha = get_git_sha(root)
    graph_sha = (data.get("meta") or {}).get("git_sha")

    if graph_sha is None:
        line, cmd = _action(
            "graph.json has no git SHA — may be stale",
            "wd discover > .weld/graph.json",
        )
        return [line], [cmd]

    if graph_sha == current_sha:
        return [], []

    behind = commits_behind(root, graph_sha, current_sha) if current_sha else -1
    if behind > 0:
        msg = f"graph.json is {behind} commit{'s' if behind != 1 else ''} behind HEAD"
    else:
        msg = "graph.json is behind HEAD"
    line, cmd = _action(msg, "wd discover > .weld/graph.json")
    return [line], [cmd]

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

def _check_agent_integration(root: Path) -> tuple[list[str], list[str]]:
    claude_cmd = root / ".claude" / "commands" / "weld.md"
    codex_skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
    if claude_cmd.is_file() or codex_skill.is_file():
        return [], []
    lines = [_status("INFO", "No agent integration found — run: wd bootstrap claude  (or: codex)")]
    return lines, []

def prime(root: Path) -> str:
    """Run all checks and return the formatted status report."""
    weld_dir = root / ".weld"

    if not weld_dir.is_dir():
        return (
            "Weld is not set up yet.\n"
            "\n"
            "Get started:\n"
            "  1. wd init                       # bootstrap .weld/discover.yaml\n"
            "  2. wd discover > .weld/graph.json   # build the graph\n"
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
        lambda: _check_agent_integration(root),
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
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = prime(root)
    sys.stdout.write(output)

if __name__ == "__main__":
    main()
