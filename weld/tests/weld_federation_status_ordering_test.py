"""``FederatedGraph.children_status()`` priority order + probe cost (bd sk3c).

Split out of ``weld_federation_test.py`` (which sat at the 400-line cap)
rather than grown onto it -- matching this repo's established split
convention (e.g. ``weld_drift_is_graph_only_test`` /
``weld_stale_sources_bound_test`` off ``weld_stale_source_test``).

Two independent claims from ADR 0082's 2026-08-21 amendment:

1. **Ordering.** ``children_status()`` sorts non-present states (missing /
   uninitialized / corrupt) before ``present`` ones, alphabetical within
   each class -- see :func:`weld.federation_support.children_status_priority_key`
   for the exact rule. :class:`FederatedGraphStatusOrderingTest` pins it in
   isolation, at the ``FederatedGraph`` level; the budget-capping proof over
   a many-child MCP-dispatched workspace lives in
   ``weld_mcp_children_status_budget_test.py``.
2. **Probe cost.** ``children_status()`` classifies each child via
   :func:`weld.federation_child_probe.probe_child_status`, which never
   builds a child's query index -- see that function's docstring for the
   measurement. :class:`FederatedGraphStatusProbeCostTest` pins the
   invariant a regression would break.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld.contract import SCHEMA_VERSION
from weld.federation import FederatedGraph
from weld.graph import Graph
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-04-15T20:30:00+00:00"


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(repo_root), capture_output=True, text=True, check=True,
    )


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _graph_payload(nodes: dict, *, schema_version: int = 1) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": schema_version},
        "nodes": nodes,
        "edges": [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    dump_workspaces_yaml(
        WorkspaceConfig(children=children, cross_repo_strategies=[]),
        root / ".weld" / "workspaces.yaml",
    )


def _write_root_graph(root: Path, children: list[str]) -> None:
    nodes = {
        f"repo:{name}": {"type": "repo", "label": name, "props": {"path": name}}
        for name in children
    }
    _write_graph(root, _graph_payload(nodes, schema_version=2))


class FederatedGraphStatusOrderingTest(unittest.TestCase):
    """bd sk3c: priority-aware children_status() ordering, isolated.

    ADR 0082's 2026-08-20 amendment bounds ``children_status`` to
    :data:`~weld._read_budget.CHILDREN_STATUS_RESERVE_BYTES` via
    :func:`~weld._read_budget.bound_dict_to_budget`, which drops the *tail*
    of whatever order it is handed. Plain alphabetical order (the pre-sk3c
    behavior) means a corrupt child whose name happens to sort last is
    exactly as likely to be dropped as a boring present one.
    """

    def test_a_corrupt_child_sorting_alphabetically_last_still_sorts_first(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("repo-alpha", "repo-beta", "repo-gamma"):
                present = _init_repo(root / name)
                _write_graph(
                    present,
                    _graph_payload({
                        f"file:src/{name}.py": {
                            "type": "file", "label": name,
                            "props": {"file": f"src/{name}.py"},
                        },
                    }),
                )
            # Alphabetically LAST of the four -- would be the first entry
            # bound_dict_to_budget's tail-drop discards under plain
            # alphabetical order.
            corrupt = _init_repo(root / "repo-zzz-corrupt")
            (corrupt / ".weld").mkdir(parents=True, exist_ok=True)
            (corrupt / ".weld" / "graph.json").write_text(
                "{bad json\n", encoding="utf-8",
            )
            names = ["repo-alpha", "repo-beta", "repo-gamma", "repo-zzz-corrupt"]
            _write_workspaces(root, [ChildEntry(name=n, path=n) for n in names])
            _write_root_graph(root, names)

            status = FederatedGraph(root).children_status()

            self.assertEqual(status["repo-zzz-corrupt"]["status"], "corrupt")
            self.assertEqual(
                list(status),
                ["repo-zzz-corrupt", "repo-alpha", "repo-beta", "repo-gamma"],
            )


class FederatedGraphStatusProbeCostTest(unittest.TestCase):
    """bd sk3c: children_status() must not pay for a queryable child handle.

    Measured separately (bd sk3c mini spec): ``Graph._build_inverted_index``
    (BM25 corpus, alias index, structural scores) is ~84% of the per-child
    cost the pre-fix ``children_status()`` paid via ``_load_child``, for
    work the classification never uses. Patching it to raise turns any
    regression that re-couples ``children_status()`` to a full child load
    into a hard failure here, not just a slower benchmark.
    """

    def test_children_status_never_builds_a_child_query_index(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            present = _init_repo(root / "repo-a")
            _write_graph(
                present,
                _graph_payload({
                    "file:src/a.py": {
                        "type": "file", "label": "alpha",
                        "props": {"file": "src/a.py"},
                    },
                }),
            )
            _write_workspaces(root, [ChildEntry(name="repo-a", path="repo-a")])
            _write_root_graph(root, ["repo-a"])

            # Construct BEFORE patching: __init__ loads the (childless) root
            # graph, which legitimately builds its own query state.
            graph = FederatedGraph(root)
            with patch.object(
                Graph, "_build_inverted_index",
                side_effect=AssertionError(
                    "children_status() must not build a child query index"
                ),
            ):
                status = graph.children_status()

            self.assertEqual(status["repo-a"]["status"], "present")


if __name__ == "__main__":
    unittest.main()
