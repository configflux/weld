"""Integration coverage for federated child-staleness *surfacing* (ADR 0066 §2).

The oracle's rule-by-rule behaviour is pinned by
``weld_federation_staleness_test``. This module drives the two **user
surfaces** end-to-end through the real CLI entry points against real git
fixtures (no stubbed ``git``), covering the three acceptance criteria of
issue 00p8.2:

1. A ``present`` child with commits past its graph shows ``stale`` in
   ``wd workspace status`` -- both the human text (status token + ``stale=N``
   counts column) and ``--json`` (a derived ``freshness`` object).
2. Root ``wd stale`` reports aggregate child staleness: a ``children`` array
   plus top-level ``stale = root OR any(child.stale)``.
3. ``missing`` / ``uninitialized`` / ``corrupt`` children degrade gracefully
   -- visible with their lifecycle state, never counted ``stale``.
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld.cli import main as cli_main
from weld.discover import discover
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _write_child_graph(repo_root: Path, *, at_head: bool = True) -> None:
    """Write a child ``graph.json`` plus (optionally) its sidecar at HEAD.

    The discovered-from SHA lives in the ADR 0065 sidecar, matching the real
    write path. ``at_head=False`` omits the sidecar (fresh-clone shape).
    """
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": 1, "schema_version": 1, "discovered_from": ["."]},
        "nodes": {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    if at_head:
        head = _git(repo_root, "rev-parse", "HEAD")
        (weld_dir / "graph-meta.json").write_text(
            json.dumps({"version": 1, "git_sha": head}, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _commit(repo_root: Path, name: str = "feature.py") -> str:
    (repo_root / name).write_text("x = 1\n", encoding="utf-8")
    _git(repo_root, "add", name)
    _git(repo_root, "commit", "-q", "-m", f"add {name}")
    return _git(repo_root, "rev-parse", "HEAD")


def _seed_root(root: Path, children: list[ChildEntry]) -> None:
    """Commit a workspaces.yaml at *root* and federate-discover once.

    ``write_root_graph=True`` mirrors the real ``wd discover`` at a
    federated root: the meta-graph is written to ``.weld/graph.json`` (with
    its git_sha stamped to HEAD) so the root's own ``wd stale`` check is
    fresh and isolates child staleness from a missing root graph.
    """
    _init_repo(root)
    dump_workspaces_yaml(
        WorkspaceConfig(children=children, cross_repo_strategies=[]),
        root / ".weld" / "workspaces.yaml",
    )
    _git(root, "add", ".weld/workspaces.yaml")
    _git(root, "commit", "-q", "-m", "add workspaces.yaml")
    discover(root, incremental=False, write_root_graph=True)


def _run_cli(*argv: str) -> str:
    stdout = io.StringIO()
    with patch("sys.stdout", stdout):
        code = cli_main(list(argv))
    assert code == 0, f"cli {argv} exited {code}: {stdout.getvalue()}"
    return stdout.getvalue()


class WorkspaceStatusStaleSurfaceTest(unittest.TestCase):
    """AC 1: a present child past its graph shows ``stale`` (text + json)."""

    def test_status_text_and_json_show_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            fresh = _init_repo(root / "svc-fresh")
            _write_child_graph(fresh, at_head=True)
            stale = _init_repo(root / "svc-stale")
            _write_child_graph(stale, at_head=True)
            _commit(stale)  # HEAD now past the graph's discovered-from SHA
            _seed_root(
                root,
                [
                    ChildEntry(name="svc-fresh", path="svc-fresh"),
                    ChildEntry(name="svc-stale", path="svc-stale"),
                ],
            )

            text = _run_cli("workspace", "status", "--root", str(root))
            self.assertIn("svc-stale: stale", text)
            self.assertIn("svc-fresh: present", text)
            self.assertIn("stale=1", text)

            raw = _run_cli("workspace", "status", "--root", str(root), "--json")
            payload = json.loads(raw)
            stale_entry = payload["children"]["svc-stale"]
            # Stored lifecycle state is unchanged; staleness is derived.
            self.assertEqual(stale_entry["status"], "present")
            self.assertTrue(stale_entry["freshness"]["stale"])
            self.assertEqual(stale_entry["freshness"]["reason"], "source_changed")
            # Fresh child carries a freshness object too, marked not stale.
            self.assertFalse(payload["children"]["svc-fresh"]["freshness"]["stale"])


class RootStaleAggregationTest(unittest.TestCase):
    """AC 2: root ``wd stale`` aggregates child drift."""

    def test_stale_reports_children_and_top_level_or(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            stale = _init_repo(root / "svc-stale")
            _write_child_graph(stale, at_head=True)
            _commit(stale)
            _seed_root(root, [ChildEntry(name="svc-stale", path="svc-stale")])

            raw = _run_cli("--root", str(root), "stale", "--json")
            payload = json.loads(raw)
            # Top-level stale fires on child drift even though the root
            # meta-graph itself is fresh (root OR any child).
            self.assertTrue(payload["stale"])
            self.assertFalse(payload["root_source_stale"])
            names = {c["name"]: c for c in payload["children"]}
            self.assertIn("svc-stale", names)
            self.assertEqual(names["svc-stale"]["state"], "stale")

            text = _run_cli("--root", str(root), "stale")
            self.assertIn("children:", text)
            self.assertIn("svc-stale: stale", text)

    def test_stale_quiet_when_all_children_fresh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            ok = _init_repo(root / "svc-ok")
            _write_child_graph(ok, at_head=True)
            _seed_root(root, [ChildEntry(name="svc-ok", path="svc-ok")])

            payload = json.loads(_run_cli("--root", str(root), "stale", "--json"))
            self.assertFalse(payload["stale"])
            self.assertEqual(payload["children"][0]["state"], "fresh")


class GracefulDegradationTest(unittest.TestCase):
    """AC 3: missing / uninitialized / corrupt are visible, never stale."""

    def test_lifecycle_states_not_counted_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            # present + fresh
            ok = _init_repo(root / "svc-ok")
            _write_child_graph(ok, at_head=True)
            # uninitialized: a repo with no graph
            _init_repo(root / "svc-bare")
            # corrupt: a repo whose graph.json is not valid JSON
            corrupt = _init_repo(root / "svc-corrupt")
            (corrupt / ".weld").mkdir(parents=True)
            (corrupt / ".weld" / "graph.json").write_text("{ not json", encoding="utf-8")
            _seed_root(
                root,
                [
                    ChildEntry(name="svc-ok", path="svc-ok"),
                    ChildEntry(name="svc-bare", path="svc-bare"),
                    ChildEntry(name="svc-corrupt", path="svc-corrupt"),
                    # ghost: never cloned -> missing
                    ChildEntry(name="ghost", path="ghost"),
                ],
            )

            payload = json.loads(_run_cli("--root", str(root), "stale", "--json"))
            # No child is stale -> top-level reflects only the (fresh) root.
            self.assertFalse(payload["stale"])
            states = {c["name"]: c["state"] for c in payload["children"]}
            self.assertEqual(states["svc-bare"], "uninitialized")
            self.assertEqual(states["svc-corrupt"], "corrupt")
            self.assertEqual(states["ghost"], "missing")
            self.assertEqual(states["svc-ok"], "fresh")

            text = _run_cli("workspace", "status", "--root", str(root))
            # Lifecycle states still render in workspace status, unchanged.
            self.assertIn("svc-bare: uninitialized", text)
            self.assertIn("svc-corrupt: corrupt", text)
            self.assertIn("ghost: missing", text)
            self.assertNotIn("stale=", text)  # no stale column when none stale


class LedgerSelfDescribingFieldsTest(unittest.TestCase):
    """ADR 0011 §5: present children persist graph_mtime_ns/node/edge counts."""

    def test_present_child_records_counts_and_mtime(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            child = _init_repo(root / "svc")
            # Two nodes / one edge so the counts are non-trivial.
            (child / ".weld").mkdir(parents=True, exist_ok=True)
            (child / ".weld" / "graph.json").write_text(
                json.dumps(
                    {
                        "meta": {"version": 1, "schema_version": 1, "discovered_from": ["."]},
                        "nodes": {
                            "entity:A": {"type": "entity", "label": "A", "props": {}},
                            "entity:B": {"type": "entity", "label": "B", "props": {}},
                        },
                        "edges": [
                            {"from": "entity:A", "to": "entity:B", "type": "calls", "props": {}},
                        ],
                    },
                    indent=2, sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            head = _git(child, "rev-parse", "HEAD")
            (child / ".weld" / "graph-meta.json").write_text(
                json.dumps({"version": 1, "git_sha": head}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _seed_root(root, [ChildEntry(name="svc", path="svc")])

            ledger = json.loads(
                (root / ".weld" / "workspace-state.json").read_text(encoding="utf-8"),
            )
            entry = ledger["children"]["svc"]
            self.assertEqual(entry["node_count"], 2)
            self.assertEqual(entry["edge_count"], 1)
            self.assertIsInstance(entry["graph_mtime_ns"], int)
            # Non-present children carry null self-describing fields.
            # (covered structurally: present child here is the only child)


if __name__ == "__main__":
    unittest.main()
