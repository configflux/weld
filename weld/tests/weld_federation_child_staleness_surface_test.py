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

:class:`RosterConventionParityTest` then holds every surface that reports a
child roster -- ``wd stale``, ``wd workspace status``, ``wd stats`` -- to one
presence count over one real workspace (field-eval N5, then ADR 0138). The git
fixtures live in ``weld.tests._federation_staleness_fixtures``, shared with
``weld_workspace_status_drift_test`` and ``weld_stats_cli_breakdown_test``.
"""

from __future__ import annotations

import json
import re
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.tests._federation_staleness_fixtures import (
    _commit,
    _git,
    _init_repo,
    _run_cli,
    _seed_root,
    _write_child_graph,
)
from weld.workspace import ChildEntry


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
            # Both children are on disk, so both are counted present; the
            # stale column sub-counts the drifted one rather than moving it
            # out of present.
            self.assertIn("present=2", text)

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

    def test_stale_text_distinguishes_absent_children_from_fresh(self) -> None:
        # bd 51oxx (field-eval finding 02, ADR 0134 one level up): in a fresh
        # worktree of a federation root where ZERO children are checked out on
        # disk, ``wd stale`` used to print ``children: 4 (0 stale)`` -- read by
        # an agent as "all healthy" when in fact none exist to be stale. The
        # summary must now report how many are actually present and break the
        # absent ones out by lifecycle state.
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            _seed_root(
                root,
                [
                    ChildEntry(name="svc-a", path="svc-a"),
                    ChildEntry(name="svc-b", path="svc-b"),
                    ChildEntry(name="svc-c", path="svc-c"),
                    ChildEntry(name="svc-d", path="svc-d"),
                ],
            )

            text = _run_cli("--root", str(root), "stale")
            self.assertIn("4 registered", text)
            self.assertIn("0 present", text)
            self.assertIn("missing=4", text)
            # The old bare "(0 stale)" form must be gone.
            self.assertNotIn("children: 4 (0 stale)", text)

            # JSON payload is unchanged in shape: one entry per registered
            # child, each carrying its lifecycle state (MCP parity relies on
            # this staying identical to the CLI's --json).
            payload = json.loads(_run_cli("--root", str(root), "stale", "--json"))
            states = {c["name"]: c["state"] for c in payload["children"]}
            self.assertEqual(
                states,
                {n: "missing" for n in ("svc-a", "svc-b", "svc-c", "svc-d")},
            )

    def test_stale_quiet_when_all_children_fresh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            ok = _init_repo(root / "svc-ok")
            _write_child_graph(ok, at_head=True)
            _seed_root(root, [ChildEntry(name="svc-ok", path="svc-ok")])

            payload = json.loads(_run_cli("--root", str(root), "stale", "--json"))
            self.assertFalse(payload["stale"])
            self.assertEqual(payload["children"][0]["state"], "fresh")

            # The human roster must say the same thing. It used to disagree
            # with the payload right here: a child the oracle reports
            # ``fresh`` was counted into no bucket at all, so one present,
            # healthy child rendered as "0 present".
            text = _run_cli("--root", str(root), "stale", "--no-refresh")
            self.assertIn("children: 1 registered, 1 present, 0 stale", text)
            self.assertNotIn("missing=", text)


class RosterConventionParityTest(unittest.TestCase):
    """One presence convention, three commands (field-eval finding N5).

    ``wd stale``'s child roster and ``wd workspace status``'s ``Counts:``
    line describe the same children, derived from the same oracle run, and
    they disagreed about how many were there. The roster counted a
    ``present`` state :mod:`weld._federation_staleness` never emits, so its
    present count silently tracked the stale count; the status line let
    ``stale`` *replace* ``present``, emptying the bucket a drifted child
    still belongs in. All now mean the same thing -- present = on disk =
    ``fresh`` + ``stale``, with ``stale`` a sub-count -- and this feeds one
    real workspace to every surface to hold them to it. ``wd stats`` joins
    on the deleted-child case below, where it was the last reader still
    answering from the stored ledger.
    """

    def test_roster_and_status_agree_on_one_real_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            fresh = _init_repo(root / "svc-fresh")
            _write_child_graph(fresh, at_head=True)
            drifted = _init_repo(root / "svc-stale")
            _write_child_graph(drifted, at_head=True)
            _commit(drifted)  # on disk, but past its graph
            _init_repo(root / "svc-bare")  # on disk, no graph -> uninitialized
            _seed_root(
                root,
                [
                    ChildEntry(name="svc-fresh", path="svc-fresh"),
                    ChildEntry(name="svc-stale", path="svc-stale"),
                    ChildEntry(name="svc-bare", path="svc-bare"),
                    ChildEntry(name="ghost", path="ghost"),  # never cloned
                ],
            )

            # The oracle is the arbiter; neither renderer's arithmetic is
            # hard-coded here. It has no ``present`` state -- that spelling
            # belongs to workspace status -- so a roster counting one is
            # counting nothing.
            payload = json.loads(_run_cli("--root", str(root), "stale", "--json"))
            states = [c["state"] for c in payload["children"]]
            self.assertNotIn("present", states)
            on_disk = sum(1 for s in states if s in ("fresh", "stale"))
            drifted_count = states.count("stale")
            self.assertEqual((len(states), on_disk, drifted_count), (4, 2, 1))

            roster = _run_cli("--root", str(root), "stale", "--no-refresh")
            self.assertIn(
                f"children: {len(states)} registered, {on_disk} present, "
                f"{drifted_count} stale",
                roster,
            )
            # registered == present + absent: the two absent children are
            # broken out, and neither is folded into present.
            self.assertIn("missing=1", roster)
            self.assertIn("uninitialized=1", roster)

            status = _run_cli("workspace", "status", "--root", str(root))
            self.assertIn(f"present={on_disk}", status)
            self.assertIn(f"stale={drifted_count}", status)
            # The per-child token is unchanged: a drifted child still reads
            # ``stale`` on its own line even though it counts as present.
            self.assertIn("svc-stale: stale", status)

            roster_present = re.search(r"(\d+) present", roster)
            status_present = re.search(r"present=(\d+)", status)
            self.assertIsNotNone(roster_present, roster)
            self.assertIsNotNone(status_present, status)
            self.assertEqual(
                roster_present.group(1), status_present.group(1),
                f"the two surfaces disagree on present:\n{roster}\n{status}",
            )

    def test_child_deleted_after_the_ledger_was_written(self) -> None:
        """The third state: the ledger's claim and the disk disagree.

        ADR 0138. The two states above are both states the ledger was
        *written* in, so a status command reading it verbatim still happened
        to be right. Delete a child afterwards and the stored ledger is the
        only place it still exists -- which is where the two surfaces used to
        part company: ``wd stale`` rebuilds live and saw 2 present, status
        read the ledger and reported 3, with the deleted child rendered
        ``present`` on its own line.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            for name in ("svc-a", "svc-b", "docs-site"):
                _write_child_graph(_init_repo(root / name), at_head=True)
            _seed_root(
                root,
                [ChildEntry(name=n, path=n)
                 for n in ("svc-a", "svc-b", "docs-site")],
            )

            state_path = root / ".weld" / "workspace-state.json"
            before = state_path.read_bytes()
            shutil.rmtree(root / "docs-site")
            # Precondition: the ledger has not been rewritten, so it is still
            # claiming a child that is no longer on disk.
            stored = json.loads(before)["children"]["docs-site"]["status"]
            self.assertEqual(stored, "present")

            roster = _run_cli("--root", str(root), "stale", "--no-refresh")
            status = _run_cli("workspace", "status", "--root", str(root))

            self.assertIn("children: 3 registered, 2 present", roster)
            self.assertIn("missing=1", roster)
            self.assertIn("present=2", status)
            self.assertIn("missing=1", status)
            self.assertIn("docs-site: missing", status)

            roster_present = re.search(r"(\d+) present", roster)
            status_present = re.search(r"present=(\d+)", status)
            self.assertIsNotNone(roster_present, roster)
            self.assertIsNotNone(status_present, status)
            self.assertEqual(
                roster_present.group(1), status_present.group(1),
                f"the two surfaces disagree on present:\n{roster}\n{status}",
            )

            # The number moved, so status says why and names the remedy --
            # and it reports the drift rather than repairing it.
            self.assertIn("ledger says present, disk says missing", status)
            self.assertIn("run: wd discover", status)
            self.assertEqual(state_path.read_bytes(), before)

            # Third surface, same workspace: wd stats read the ledger too, so
            # it was the last place the deleted child still read present. Its
            # own mechanics are pinned in weld_stats_cli_breakdown_test; what
            # only this workspace can show is that the number agrees.
            stats = json.loads(_run_cli("--root", str(root), "stats", "--json"))
            workspaces = stats["workspaces"]
            self.assertEqual(
                workspaces["present"], int(status_present.group(1)),
                f"stats disagrees on present:\n{workspaces}\n{status}",
            )


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
