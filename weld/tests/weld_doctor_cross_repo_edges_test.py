"""``wd doctor``'s Edges section at a polyrepo root (ADR 0137 ss3).

Doctor counted edges and called that a health check. The v0.24.0 field
evaluation put a root in front of it whose every cross-repo edge referenced a
node that existed nowhere, and doctor reported ``[ok] 4 edges`` -- a true
sentence and a useless one. So the Edges section now also says whether those
edges point anywhere.

The last test is the one that keeps the rest honest: doctor must never raise,
and a check that cannot run has to *say so* rather than fall through to
silence, which would put us back at "healthy" for a workspace nobody checked.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import weld._federation_ids as federation_ids
from weld.doctor import CheckResult, doctor
from weld._doctor_graph_json import check_graph_json
from weld.tests._federation_id_fixtures import (
    MISSING,
    cross_repo_edge,
    write_child,
    write_graph,
    write_workspace_root,
)
from weld.contract import SCHEMA_VERSION
from weld.workspace import UNIT_SEPARATOR as SEP


def _edges_results(weld_dir: Path) -> list[CheckResult]:
    return [
        r for r in check_graph_json(weld_dir, CheckResult) if r.section == "Edges"
    ]


def _fails(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.level == "fail"]


def _dangling_workspace(root: Path) -> None:
    write_child(root, "alpha", node_ids=("n1",))
    write_workspace_root(
        root,
        registered=("alpha",),
        repo_nodes=("alpha",),
        edges=(cross_repo_edge("repo:alpha", f"alpha{SEP}nope"),),
    )


class DoctorReportsUnresolvableEndpointsTest(unittest.TestCase):
    def test_dangling_endpoint_is_a_fail_under_edges(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _dangling_workspace(root)
            failures = _fails(_edges_results(root / ".weld"))
            self.assertEqual(len(failures), 1, failures)
            self.assertIn("1 dangling", failures[0].message)
            self.assertIn("wd graph validate", failures[0].message)

    def test_unverifiable_endpoint_is_counted_separately(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha", node_ids=("n1",))
            write_child(root, "gone", state=MISSING)
            write_workspace_root(
                root,
                registered=("alpha", "gone"),
                repo_nodes=("alpha",),
                edges=(
                    cross_repo_edge("repo:alpha", f"alpha{SEP}nope"),
                    cross_repo_edge("repo:alpha", f"gone{SEP}n1"),
                ),
            )
            failures = _fails(_edges_results(root / ".weld"))
            self.assertEqual(len(failures), 1, failures)
            self.assertIn("1 dangling", failures[0].message)
            self.assertIn("1 unverifiable", failures[0].message)

    def test_resolvable_edges_add_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha", node_ids=("n1",))
            write_child(root, "beta", node_ids=("n2",))
            write_workspace_root(
                root,
                registered=("alpha", "beta"),
                repo_nodes=("alpha", "beta"),
                edges=(cross_repo_edge(f"alpha{SEP}n1", f"beta{SEP}n2"),),
            )
            results = _edges_results(root / ".weld")
            self.assertEqual([r.level for r in results], ["ok"])

    def test_single_repo_is_left_alone(self) -> None:
        # No workspaces.yaml: there is no child id space to resolve into, so
        # doctor asks nothing new and the section is the count it always was.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(
                root,
                {
                    "meta": {"version": SCHEMA_VERSION, "schema_version": 2},
                    "nodes": {},
                    "edges": [cross_repo_edge(f"alpha{SEP}n1", f"beta{SEP}n2")],
                },
            )
            results = _edges_results(root / ".weld")
            self.assertEqual([r.level for r in results], ["ok"])

    def test_the_finding_reaches_a_full_doctor_run(self) -> None:
        # The section-level tests above would all still pass if nothing wired
        # the check into ``doctor()``; this is the wiring.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _dangling_workspace(root)
            failures = [
                r for r in doctor(root)
                if r.section == "Edges" and r.level == "fail"
            ]
            self.assertEqual(len(failures), 1, failures)
            self.assertIn("dangling", failures[0].message)


class DoctorNeverRaisesTest(unittest.TestCase):
    def test_an_unbuildable_index_warns_instead_of_raising(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _dangling_workspace(root)
            with patch.object(
                federation_ids,
                "federation_id_index_for_root",
                side_effect=RuntimeError("workspace unreadable"),
            ):
                results = _edges_results(root / ".weld")
            warnings = [r for r in results if r.level == "warn"]
            self.assertEqual(len(warnings), 1, results)
            self.assertIn("could not be checked", warnings[0].message)
            self.assertIn("RuntimeError", warnings[0].message)
            # A check that did not run must not raise the exit code either --
            # it is a warning precisely because we do not know.
            self.assertEqual(_fails(results), [])


if __name__ == "__main__":
    unittest.main()
