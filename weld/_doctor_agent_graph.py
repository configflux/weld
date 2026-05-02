"""Doctor checks for the Agent Graph (`.weld/agent-graph.json`).

Surfaces the agent-graph health summary as a first-class section in the
``wd doctor`` output instead of leaving it buried in
``wd agents discover --show-diagnostics``.

The section degrades gracefully:

* file present, no diagnostics -> ``[ok]`` line with the agent count
* file present, broken-reference diagnostics -> ``[warn]`` line with the
  diagnostic count and a pointer to ``wd agents discover --show-diagnostics``
* file missing -> ``[note]`` skip line pointing at ``wd agents discover``
* file present but unparseable -> ``[warn]`` line explaining the corruption

The :class:`weld.doctor.CheckResult` class is duck-typed via the *result_cls*
parameter to keep this module import-cycle free.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from weld.agent_graph_storage import AGENT_GRAPH_FILENAME

SECTION = "Agent Graph"


def _agent_graph_path(weld_dir: Path) -> Path:
    return weld_dir / AGENT_GRAPH_FILENAME


def _load_graph(path: Path) -> dict[str, Any] | None:
    """Return the parsed agent-graph payload or ``None`` if unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _count_agents(nodes: Any) -> int:
    if not isinstance(nodes, dict):
        return 0
    return sum(
        1
        for n in nodes.values()
        if isinstance(n, dict) and n.get("type") == "agent"
    )


def _broken_ref_count(diagnostics: Any) -> int:
    if not isinstance(diagnostics, list):
        return 0
    return sum(
        1
        for d in diagnostics
        if isinstance(d, dict)
        and d.get("code") == "agent_graph_broken_reference"
    )


def check_agent_graph(weld_dir: Path, result_cls: Any) -> list[Any]:
    """Build the [Agent Graph] section results.

    Returns at least one result so the section always appears when ``.weld/``
    exists.
    """
    path = _agent_graph_path(weld_dir)
    if not path.is_file():
        return [
            result_cls(
                "note",
                "agent-graph not present -- run: wd agents discover",
                SECTION,
                "agent-graph-missing",
            )
        ]

    data = _load_graph(path)
    if data is None:
        return [
            result_cls(
                "warn",
                ".weld/agent-graph.json is unreadable -- "
                "run: wd agents discover",
                SECTION,
            )
        ]

    nodes = data.get("nodes") or {}
    meta = data.get("meta") or {}
    diagnostics = meta.get("diagnostics") or []

    n_agents = _count_agents(nodes)
    n_broken = _broken_ref_count(diagnostics)

    suffix = "agents" if n_agents != 1 else "agent"
    results = [
        result_cls(
            "ok",
            f"{n_agents} {suffix} discovered",
            SECTION,
        )
    ]
    if n_broken:
        diag_suffix = "diagnostics" if n_broken != 1 else "diagnostic"
        results.append(
            result_cls(
                "warn",
                f"{n_broken} broken-reference {diag_suffix} in agent definitions"
                " -- run: wd agents discover --show-diagnostics",
                SECTION,
            )
        )
    return results
