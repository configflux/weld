"""Diagnostic command that checks Weld setup and reports issues.

Each check returns a list of :class:`CheckResult` objects with a level
(``ok``, ``note``, ``warn``, or ``fail``), a human-readable message, and
a section name (``Project``, ``Config``, ``Graph``, ``Schema``,
``Nodes``, ``Edges``, ``Strategies``, ``Optional``, ``MCP``).

The formatted output is grouped by section with a ``Status`` summary
line at the bottom counting OK, note, warning, and error results. Notes
carry a stable id (printed as ``(id: <note-id>)``) so users can dismiss
them per project via ``wd doctor --ack <id>`` -- the dismissals are
persisted in ``.weld/doctor.yaml``.

Exit code: 0 if no ``[fail]`` results, including when no Weld project
has been initialized yet; 1 if any ``[fail]``. Notes never raise the
exit code.

Security posture: doctor output never prints the absolute root path or
environment variables. Paths are reported as ``.weld/<name>`` and
strategy names are taken only from ``discover.yaml``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


from weld._git import commits_behind, get_git_sha, is_git_repo
from weld._doctor_agent_graph import check_agent_graph
from weld._doctor_format import (
    apply_suppressions as _apply_suppressions,
    format_results,
)
from weld._doctor_optional import check_optional_deps, check_tree_sitter
from weld._doctor_strategies import check_strategies, check_trust_boundaries
from weld._doctor_suppressions import handle_ack_flags, load_suppressions
from weld._yaml import parse_yaml


@dataclass(frozen=True)
class CheckResult:
    """Single diagnostic finding.

    ``section`` groups results under a PM-required section header.
    Defaults to ``"Project"`` so legacy callers keep working.

    ``note_id`` is a stable identifier for ``note``-level findings the
    user may dismiss via ``wd doctor --ack <id>``. Non-note levels and
    notes that should not be individually suppressible (e.g. summary
    lines) leave it as ``None``.
    """

    level: str  # "ok" | "note" | "warn" | "fail"
    message: str
    section: str = "Project"
    note_id: str | None = None


# ── individual checks ────────────────────────────────────────────────


def _check_discover_yaml(weld_dir: Path) -> list[CheckResult]:
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        return [CheckResult("fail", ".weld/discover.yaml not found", "Config")]
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
        count = len(sources) if isinstance(sources, list) else 0
    except Exception:
        count = 0
    suffix = "entries" if count != 1 else "entry"
    return [
        CheckResult(
            "ok",
            f".weld/discover.yaml found ({count} source {suffix})",
            "Config",
        )
    ]


def _check_graph_json(weld_dir: Path) -> list[CheckResult]:
    """Report graph.json presence + schema/nodes/edges split into sections."""
    path = weld_dir / "graph.json"
    if not path.is_file():
        return [CheckResult("fail", ".weld/graph.json not found", "Graph")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [
            CheckResult("fail", ".weld/graph.json is invalid or unreadable", "Graph")
        ]

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
            "Graph",
        ),
        CheckResult("ok", f"schema v{schema_ver}", "Schema"),
        CheckResult("ok", f"{n_nodes} nodes", "Nodes"),
        CheckResult("ok", f"{n_edges} edges", "Edges"),
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
        return [CheckResult("warn", "graph has no git SHA -- staleness unknown", "Graph")]

    if graph_sha == current_sha:
        return []

    behind = commits_behind(root, graph_sha, current_sha) if current_sha else -1
    if behind > 0:
        suffix = "commits" if behind != 1 else "commit"
        return [
            CheckResult(
                "warn",
                f"graph is {behind} {suffix} behind HEAD -- run wd discover",
                "Graph",
            )
        ]
    return [CheckResult("warn", "graph is behind HEAD -- run wd discover", "Graph")]


# Re-export helpers used by tests that monkey-patch tree-sitter. We import
# at attribute lookup time via the submodule so patches on
# ``weld.doctor._check_tree_sitter_language`` keep working.
from weld._doctor_optional import _check_tree_sitter_language  # noqa: E402,F401


def _check_tree_sitter(weld_dir: Path) -> list[CheckResult]:
    return check_tree_sitter(weld_dir, CheckResult)


def _check_optional_deps() -> list[CheckResult]:
    return check_optional_deps(CheckResult)


# TODO(Epic 4 / safe-mode): once safe-mode plumbing lands (a dedicated
# module or CLI flag for restricted discovery), add a check here that
# reports whether safe-mode is available and whether it is currently
# active. Do not fabricate a check before the feature exists.


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
        return [CheckResult("ok", f"MCP server config found in {locations}", "MCP")]
    return [
        CheckResult(
            "note",
            "MCP server config not found (.mcp.json or .codex/config.toml)",
            "MCP",
            note_id="mcp-config-missing",
        )
    ]


def _check_trust_boundaries(weld_dir: Path) -> list[CheckResult]:
    return check_trust_boundaries(weld_dir, CheckResult)


def _check_strategies(weld_dir: Path, root: Path) -> list[CheckResult]:
    bundled_dir = Path(__file__).resolve().parent / "strategies"
    return check_strategies(weld_dir, root, bundled_dir, CheckResult)


def _check_python_version() -> list[CheckResult]:
    vi = sys.version_info
    ver_str = f"{vi[0]}.{vi[1]}.{vi[2]}"
    if vi[0] >= 3 and vi[1] >= 10:
        return [CheckResult("ok", f"Python {ver_str}", "Project")]
    return [CheckResult("warn", f"Python {ver_str} -- weld requires 3.10+", "Project")]


# ── public API ───────────────────────────────────────────────────────


def doctor(root: Path) -> list[CheckResult]:
    """Run all diagnostic checks and return the results.

    Parameters
    ----------
    root:
        Directory to inspect. It may or may not contain ``.weld/`` yet.
    """
    weld_dir = root / ".weld"

    if not weld_dir.is_dir():
        results = _check_python_version()
        results.append(
            CheckResult(
                "warn",
                "No Weld project found (.weld/ directory not found) -- run wd init",
                "Project",
            )
        )
        return results

    results: list[CheckResult] = []
    results.extend(_check_python_version())
    results.extend(_check_discover_yaml(weld_dir))
    results.extend(_check_graph_json(weld_dir))
    results.extend(_check_staleness(weld_dir, root))
    results.extend(_check_strategies(weld_dir, root))
    results.extend(_check_trust_boundaries(weld_dir))
    results.extend(check_agent_graph(weld_dir, CheckResult))
    results.extend(_check_tree_sitter(weld_dir))
    results.extend(_check_optional_deps())
    results.extend(_check_mcp_config(root))
    return results


def doctor_agent_graph(root: Path) -> list[CheckResult]:
    """Run only the [Agent Graph] section checks.

    Returns a single-section result list suitable for ``wd doctor
    --agent-graph``. When ``.weld/`` is missing, returns a project-level
    note pointing at ``wd init`` rather than fabricating an empty section.
    """
    weld_dir = root / ".weld"
    if not weld_dir.is_dir():
        return [
            CheckResult(
                "note",
                "No Weld project found (.weld/ directory not found) -- run wd init",
                "Project",
            )
        ]
    return check_agent_graph(weld_dir, CheckResult)


_EXIT_CODE_EPILOG = """\
Exit codes:
  0  healthy -- all checks pass, or only notes/warnings (visible but not
         fatal), including when no Weld project has been initialized yet
  1  invalid setup -- one or more errors detected
         (e.g. missing .weld/discover.yaml, corrupt .weld/graph.json,
          unresolved strategy reference)

Notes ([note]) flag soft recommendations such as missing optional
providers or missing MCP config; dismiss them per project with
``--ack <id>``. Warnings ([warn]) flag a currently-degraded state.
Neither raises the exit code.
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wd doctor",
        description="Check Weld setup and report issues",
        epilog=_EXIT_CODE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root directory (default: current directory)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--security",
        action="store_true",
        help="Show the trust-posture report only (ADR 0025)",
    )
    mode.add_argument(
        "--agent-graph",
        action="store_true",
        help="Show the Agent Graph health summary only",
    )
    mode.add_argument(
        "--ack",
        action="append",
        metavar="NOTE_ID",
        help="Dismiss a doctor note by id (repeatable)",
    )
    mode.add_argument(
        "--unack",
        action="append",
        metavar="NOTE_ID",
        help="Re-enable a previously dismissed note (repeatable)",
    )
    mode.add_argument(
        "--list-acks",
        action="store_true",
        help="Print currently-dismissed note ids, one per line",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --security, emit the trust-posture report as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd doctor``.

    With ``--security``, restricts output to the trust-posture engine
    (ADR 0025) and supports ``--json``. With ``--ack`` / ``--unack`` /
    ``--list-acks``, manages the per-project note suppression sidecar at
    ``.weld/doctor.yaml`` and exits without running the full check
    suite. Without any of those flags, runs all checks and points to
    ``wd security`` whenever the trust-posture engine finds any ``high``
    signal.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()

    if args.ack or args.unack or args.list_acks:
        return handle_ack_flags(
            root,
            ack=args.ack,
            unack=args.unack,
            list_acks=bool(args.list_acks),
        )

    if args.security:
        from weld.security import run_security

        return run_security(root, as_json=args.json)

    if args.agent_graph:
        ag_results = doctor_agent_graph(root)
        sys.stdout.write(format_results(ag_results) + "\n")
        return 1 if any(r.level == "fail" for r in ag_results) else 0

    results = doctor(root)
    weld_dir = root / ".weld"
    suppressed = load_suppressions(weld_dir) if weld_dir.is_dir() else set()
    visible = _apply_suppressions(results, suppressed)
    output = format_results(visible)
    sys.stdout.write(output + "\n")

    # Pointer line: surface the dedicated trust-posture view when we detect
    # a high-risk signal. Cheap to assess; never raises.
    try:
        from weld._security_posture import assess, has_high

        if has_high(assess(root)):
            sys.stdout.write(
                "\nSecurity: high-risk signals detected -- run "
                "`wd security` (or `wd doctor --security`) for details.\n"
            )
    except Exception:  # noqa: BLE001 -- never let the pointer crash doctor
        pass

    has_fail = any(r.level == "fail" for r in results)
    return 1 if has_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
