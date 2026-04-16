"""Diagnostic command that checks Weld setup and reports issues.

Each check returns a list of :class:`CheckResult` objects with a level
(``ok``, ``warn``, or ``fail``) and a human-readable message.

Exit code: 0 if no ``[fail]`` results, 1 if any ``[fail]``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path


from weld._git import commits_behind, get_git_sha, is_git_repo
from weld._yaml import parse_yaml
from weld.strategies._ts_parse import grammar_module_name, grammar_package_name


@dataclass(frozen=True)
class CheckResult:
    """Single diagnostic finding."""

    level: str  # "ok" | "warn" | "fail"
    message: str


# ── individual checks ────────────────────────────────────────────────


def _check_discover_yaml(weld_dir: Path) -> list[CheckResult]:
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        return [CheckResult("fail", ".weld/discover.yaml not found")]
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
        count = len(sources) if isinstance(sources, list) else 0
    except Exception:
        count = 0
    suffix = "entries" if count != 1 else "entry"
    return [CheckResult("ok", f".weld/discover.yaml found ({count} source {suffix})")]


def _check_graph_json(weld_dir: Path) -> list[CheckResult]:
    path = weld_dir / "graph.json"
    if not path.is_file():
        return [CheckResult("fail", ".weld/graph.json not found")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [CheckResult("fail", ".weld/graph.json is invalid or unreadable")]

    nodes = data.get("nodes", {})
    edges = data.get("edges", [])
    meta = data.get("meta", {}) or {}
    schema_ver = meta.get("schema_version", "?")
    n_nodes = len(nodes) if isinstance(nodes, dict) else 0
    n_edges = len(edges) if isinstance(edges, list) else 0
    return [
        CheckResult(
            "ok",
            f".weld/graph.json found ({n_nodes} nodes, {n_edges} edges, schema v{schema_ver})",
        )
    ]


def _check_staleness(weld_dir: Path, root: Path) -> list[CheckResult]:
    path = weld_dir / "graph.json"
    if not path.is_file():
        return []  # already covered by _check_graph_json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    if not is_git_repo(root):
        return []

    current_sha = get_git_sha(root)
    meta = data.get("meta") or {}
    graph_sha = meta.get("git_sha")

    if graph_sha is None:
        return [CheckResult("warn", "graph has no git SHA -- staleness unknown")]

    if graph_sha == current_sha:
        return []

    behind = commits_behind(root, graph_sha, current_sha) if current_sha else -1
    if behind > 0:
        suffix = "commits" if behind != 1 else "commit"
        return [
            CheckResult(
                "warn",
                f"graph is {behind} {suffix} behind HEAD -- run wd discover",
            )
        ]
    return [CheckResult("warn", "graph is behind HEAD -- run wd discover")]


def _check_tree_sitter_language(lang: str) -> bool:
    """Return True if tree-sitter grammar for *lang* is importable."""
    mod_name = grammar_module_name(lang)
    try:
        spec = importlib.util.find_spec(mod_name)
        return spec is not None
    except (ModuleNotFoundError, ValueError):
        return False


_TREE_SITTER_LANGUAGES = (
    "python", "javascript", "typescript", "go", "rust", "cpp", "csharp",
)


def _check_tree_sitter(weld_dir: Path) -> list[CheckResult]:
    """Check tree-sitter availability for configured languages."""
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        return []

    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:
        return []

    uses_tree_sitter = any(
        isinstance(s, dict) and s.get("strategy") == "tree_sitter"
        for s in sources
        if isinstance(s, dict)
    )
    if not uses_tree_sitter:
        return []

    available: list[str] = []
    missing: list[str] = []
    for lang in _TREE_SITTER_LANGUAGES:
        if _check_tree_sitter_language(lang):
            available.append(lang)
        else:
            missing.append(lang)

    results: list[CheckResult] = []
    if available:
        results.append(
            CheckResult("ok", f"tree-sitter available ({', '.join(available)})")
        )
    if missing:
        for lang in missing:
            display = "C#" if lang == "csharp" else lang.title()
            results.append(
                CheckResult(
                    "warn",
                    f"{grammar_package_name(lang)} not installed -- "
                    f"{display} files using regex fallback",
                )
            )
    return results


def _check_mcp_config(root: Path) -> list[CheckResult]:
    repo_mcp = root / ".mcp.json"
    codex_mcp = root / ".codex" / "config.toml"

    found: list[str] = []
    if repo_mcp.is_file():
        found.append(".mcp.json")
    if codex_mcp.is_file():
        found.append(".codex/config.toml")

    if found:
        locations = " and ".join(found)
        return [CheckResult("ok", f"MCP server config found in {locations}")]
    return [CheckResult("warn", "MCP server config not found (.mcp.json or .codex/config.toml)")]


def _resolve_strategy(name: str, root: Path) -> bool:
    """Return True if a strategy can be resolved (project-local or bundled)."""
    project_local = root / ".weld" / "strategies" / f"{name}.py"
    bundled = Path(__file__).resolve().parent / "strategies" / f"{name}.py"
    return project_local.is_file() or bundled.is_file()


def _check_strategies(weld_dir: Path, root: Path) -> list[CheckResult]:
    """Verify all strategies referenced in discover.yaml can be resolved."""
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        return []

    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:
        return []

    strategies: set[str] = set()
    for src in sources:
        if isinstance(src, dict):
            strat = src.get("strategy")
            if isinstance(strat, str):
                strategies.add(strat)

    if not strategies:
        return []

    missing: list[str] = []
    for strat in sorted(strategies):
        if not _resolve_strategy(strat, root):
            missing.append(strat)

    if missing:
        results: list[CheckResult] = []
        for name in missing:
            results.append(
                CheckResult("fail", f"strategy '{name}' referenced but not found")
            )
        return results

    count = len(strategies)
    suffix = "strategies" if count != 1 else "strategy"
    return [CheckResult("ok", f"all {count} referenced {suffix} resolved")]


def _check_python_version() -> list[CheckResult]:
    vi = sys.version_info
    ver_str = f"{vi[0]}.{vi[1]}.{vi[2]}"
    if vi[0] >= 3 and vi[1] >= 10:
        return [CheckResult("ok", f"Python {ver_str}")]
    return [CheckResult("warn", f"Python {ver_str} -- weld requires 3.10+")]


# ── public API ───────────────────────────────────────────────────────


def doctor(root: Path) -> list[CheckResult]:
    """Run all diagnostic checks and return the results.

    Parameters
    ----------
    root:
        Project root directory (the directory containing ``.weld/``).
    """
    weld_dir = root / ".weld"

    if not weld_dir.is_dir():
        return [
            CheckResult("fail", ".weld/ directory not found"),
            CheckResult("fail", ".weld/discover.yaml not found"),
            CheckResult("fail", ".weld/graph.json not found"),
        ]

    results: list[CheckResult] = []
    results.extend(_check_discover_yaml(weld_dir))
    results.extend(_check_graph_json(weld_dir))
    results.extend(_check_staleness(weld_dir, root))
    results.extend(_check_tree_sitter(weld_dir))
    results.extend(_check_mcp_config(root))
    results.extend(_check_strategies(weld_dir, root))
    results.extend(_check_python_version())
    return results


def format_results(results: list[CheckResult]) -> str:
    """Format results as human-readable lines with [ok]/[warn]/[fail] tags."""
    lines: list[str] = []
    for r in results:
        tag = r.level
        lines.append(f"[{tag:4s}] {r.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd doctor``."""
    parser = argparse.ArgumentParser(
        prog="wd doctor",
        description="Check Weld setup and report issues",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root directory (default: current directory)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()

    results = doctor(root)
    output = format_results(results)
    sys.stdout.write(output + "\n")

    has_fail = any(r.level == "fail" for r in results)
    return 1 if has_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
