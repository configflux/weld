"""CLI-level tests for the extended ``wd stats`` breakdown (tracked issue).

PM audit requires ``wd stats`` to surface, in addition to node/edge counts:

- Top authority (most-connected) nodes -- asserted by
  :mod:`weld_stats_top_authority_test` at the graph level.
- Graph staleness so consumers can see whether the graph is up to date
  without running ``wd stale`` separately.
- A workspace breakdown when the current root is a polyrepo workspace, so
  the demo command shows per-child context.

These tests drive the CLI plumbing in :mod:`weld._graph_cli`. They are
black-box over stdout JSON to keep the contract explicit.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


from weld._graph_cli import main as cli_main  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.tests._federation_staleness_fixtures import (  # noqa: E402
    _init_repo,
    _write_child_graph,
)
from weld.workspace import (  # noqa: E402
    ChildEntry,
    WorkspaceConfig,
    dump_workspaces_yaml,
)
from weld.workspace_state import (  # noqa: E402
    build_workspace_state,
    load_workspace_config,
    save_workspace_state,
)


def _write_graph(root: Path, payload: dict) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_stats(root: Path) -> dict:
    """Run ``wd stats --json`` and parse the JSON envelope.

    Per ADR 0040 the CLI defaults to human text; tests asking for
    structured fields opt in via ``--json``.
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli_main(["--root", str(root), "stats", "--json"])
    return json.loads(buf.getvalue())


def _run_stats_text(root: Path) -> tuple[str, str]:
    """Run human-format ``wd stats``; return ``(stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        cli_main(["--root", str(root), "stats"])
    return out.getvalue(), err.getvalue()


def _seed_workspace(root: Path, names: tuple[str, ...]) -> None:
    """Register *names* as children, clone each, and record a ledger.

    Children are real git repositories with real child graphs (a child
    without a ``.git`` is classified ``missing``, so a stub directory could
    never produce the stored ``present`` claim these tests start from), and
    the ledger is written by the product's own writer. The stored claim
    under test is therefore one ``wd discover`` would genuinely have
    recorded, not a hand-rolled fixture that can drift from the schema it
    stands in for.
    """
    _write_graph(root, {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
        "nodes": {},
        "edges": [],
    })
    for name in names:
        _write_child_graph(_init_repo(root / name), at_head=True)
    dump_workspaces_yaml(
        WorkspaceConfig(children=[ChildEntry(name=n, path=n) for n in names]),
        root / ".weld" / "workspaces.yaml",
    )
    config = load_workspace_config(root)
    assert config is not None
    save_workspace_state(root, build_workspace_state(root, config))


class TestStatsCliBaseline(unittest.TestCase):
    def test_single_repo_stats_contains_pm_breakdown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": "2026-04-24T12:00:00+00:00",
                    "schema_version": 1,
                },
                "nodes": {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {},
                    },
                    "entity:Order": {
                        "type": "entity",
                        "label": "Order",
                        "props": {},
                    },
                },
                "edges": [
                    {
                        "from": "entity:Order",
                        "to": "entity:Store",
                        "type": "depends_on",
                        "props": {},
                    },
                ],
            }
            _write_graph(root, payload)
            out = _run_stats(root)

            # PM required breakdown keys.
            self.assertIn("nodes_by_type", out)
            self.assertIn("edges_by_type", out)
            self.assertIn("top_authority_nodes", out)
            self.assertIn("stale", out)
            self.assertIn("description_coverage_pct", out)

            # Staleness payload is the same dict Graph.stale() returns; the
            # stats command must not invent its own schema here.
            self.assertIsInstance(out["stale"], dict)

    def test_single_repo_stats_omits_workspaces_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
                "nodes": {},
                "edges": [],
            }
            _write_graph(root, payload)
            out = _run_stats(root)
            self.assertNotIn("workspaces", out)


class TestStatsCliBackwardCompat(unittest.TestCase):
    def test_existing_keys_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, {
                "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
                "nodes": {},
                "edges": [],
            })
            out = _run_stats(root)
            for key in (
                "total_nodes",
                "total_edges",
                "nodes_by_type",
                "edges_by_type",
                "nodes_with_description",
                "description_coverage_pct",
                "description_coverage_by_type",
            ):
                self.assertIn(key, out)


class TestStatsCliWorkspaceSummary(unittest.TestCase):
    def test_polyrepo_stats_includes_workspaces_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Write the root graph.
            _write_graph(root, {
                "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
                "nodes": {},
                "edges": [],
            })
            # Register two children in workspaces.yaml that were never
            # cloned, with no ledger ever written (``wd init`` without a
            # subsequent ``wd discover``).
            cfg = WorkspaceConfig(
                children=[
                    ChildEntry(name="alpha", path="services/alpha"),
                    ChildEntry(name="beta", path="services/beta"),
                ],
            )
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            dump_workspaces_yaml(cfg, root / ".weld" / "workspaces.yaml")

            out = _run_stats(root)
            self.assertIn("workspaces", out)
            ws = out["workspaces"]
            self.assertIsInstance(ws, dict)
            self.assertEqual(ws.get("count"), 2)
            self.assertIn("children", ws)
            self.assertIsInstance(ws["children"], list)
            names = sorted(entry["name"] for entry in ws["children"])
            self.assertEqual(names, ["alpha", "beta"])
            # No ledger to read, so the status is the probe's: neither path
            # exists, so both are ``missing``. The old fallback said
            # ``unknown`` here, which is the one answer that is never true --
            # weld can see perfectly well that the directory is not there.
            for entry in ws["children"]:
                self.assertEqual(entry["status"], "missing")
            self.assertEqual(ws["present"], 0)
            # A registry with no ledger behind it *is* ledger drift, and the
            # remedy the pointer leads to (``wd discover``) is exactly what
            # this workspace needs.
            self.assertEqual(ws["drift_count"], 2)


class TestStatsCliObservedChildLifecycle(unittest.TestCase):
    """ADR 0138, extended to ``wd stats``: disk is the fact at read time.

    ``wd stats`` reported each child's *stored* ``status`` verbatim, so a
    child deleted after the last ``wd discover`` still read ``present`` here
    while ``wd stale`` and ``wd workspace status`` both reported it missing
    -- three surfaces, two answers. These tests hold this one to the disk.
    """

    def test_child_deleted_after_the_ledger_reads_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_workspace(root, ("alpha", "beta"))
            state_path = root / ".weld" / "workspace-state.json"
            before = state_path.read_bytes()
            shutil.rmtree(root / "beta")
            # Precondition: the ledger still claims the child that is gone.
            stored = json.loads(before)["children"]["beta"]["status"]
            self.assertEqual(stored, "present")

            ws = _run_stats(root)["workspaces"]
            statuses = {c["name"]: c["status"] for c in ws["children"]}
            self.assertEqual(statuses, {"alpha": "present", "beta": "missing"})
            # The roster is still the registered set; only presence moved.
            self.assertEqual(ws["count"], 2)
            self.assertEqual(ws["present"], 1)
            self.assertEqual(ws["drift_count"], 1)
            # Reported, not repaired: ``wd stats`` is a read (ADR 0138 §4).
            self.assertEqual(state_path.read_bytes(), before)

    def test_child_path_comes_from_the_registry_not_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_workspace(root, ("alpha",))

            ws = _run_stats(root)["workspaces"]
            # A ledger row records ``graph_path``; it has no ``path`` key, so
            # reading one reported "path": null for every child as soon as a
            # ledger existed -- while the never-discovered branch of the same
            # function filled the same key in from workspaces.yaml.
            self.assertEqual(ws["children"][0]["path"], "alpha")

    def test_agreeing_ledger_reports_zero_drift_and_keeps_its_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_workspace(root, ("alpha",))

            ws = _run_stats(root)["workspaces"]
            self.assertEqual(ws["present"], 1)
            # Always present, not omitted-when-empty: one key a machine
            # consumer reads unconditionally instead of branching on its
            # absence (ADR 0138 §3).
            self.assertEqual(ws["drift_count"], 0)
            self.assertEqual(ws["children"][0]["status"], "present")

    def test_unprobeable_registry_falls_back_to_the_ledger_and_says_so(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_workspace(root, ("alpha", "beta"))
            shutil.rmtree(root / "beta")

            # ADR 0138 §5: the probe declining is the "report the stored
            # ledger unchanged" signal -- correct, but never silent.
            with patch(
                "weld._workspace_drift.observed_children", return_value=None,
            ):
                text, err = _run_stats_text(root)
            self.assertIn("could not re-read the workspace registry", err)
            self.assertNotIn("could not re-read", text)
            # Falls back to the ledger's claim, so beta still reads present
            # -- which is exactly what the notice on stderr warns about.
            self.assertIn("workspaces: 2 registered, 2 present", text)

    def test_human_summary_splits_registered_from_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_workspace(root, ("alpha", "beta"))
            shutil.rmtree(root / "beta")

            text, err = _run_stats_text(root)
            # The old form was a bare "workspaces: 2 children" -- the same
            # shape bd 51oxx found being read as "2 are here" on wd stale.
            self.assertNotIn("2 children", text)
            self.assertIn("workspaces: 2 registered, 1 present", text)
            # One pointer, not a per-child block: stats is the summary and
            # wd workspace status is the detail surface (which names the
            # wd discover remedy itself, so we do not carry two here).
            self.assertIn(
                "workspace ledger drift: 1 child differs from the stored "
                "ledger -- run wd workspace status for detail",
                text,
            )
            self.assertEqual(err, "")

    def test_human_summary_stays_quiet_when_the_ledger_agrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_workspace(root, ("alpha",))

            text, _ = _run_stats_text(root)
            self.assertIn("workspaces: 1 registered, 1 present", text)
            # Silence on agreement: a pointer printed every run is a pointer
            # the reader learns to skip.
            self.assertNotIn("ledger drift", text)


if __name__ == "__main__":
    unittest.main()
