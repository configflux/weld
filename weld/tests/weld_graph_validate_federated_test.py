"""``wd graph validate`` at a polyrepo root (ADR 0137 ss3).

The v0.24.0 field evaluation pointed ``wd graph validate`` at a federated root
whose four cross-repo edges had 4/4 endpoints resolving to no node anywhere,
and it printed ``{"valid": true, "errors": []}`` and exited 0. The bypass it
took was shape-only: one separator, two non-empty halves, therefore skip the
dangling check. This suite is that probe plus the rulings around it.

The pairing that matters is the last two tests. At a workspace root the
endpoints are resolved against the children; one directory up, with no
``workspaces.yaml``, the same graph validates clean -- because there the shape
check is the only honest answer available and stays exactly as it was.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._federation_validate import FederationIdIndex
from weld.cli import main as cli_main
from weld.contract import SCHEMA_VERSION, validate_edge
from weld.tests._federation_id_fixtures import (
    MISSING,
    cross_repo_edge,
    write_child,
    write_graph,
    write_workspace_root,
)
from weld.workspace import UNIT_SEPARATOR as SEP


class _Run:
    """One ``wd graph validate`` invocation: exit code, payload, stderr."""

    def __init__(self, rc: int, stdout: str, stderr: str) -> None:
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr

    @property
    def payload(self) -> dict:
        return json.loads(self.stdout)


def _validate(root: Path) -> _Run:
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = cli_main(["graph", "--root", str(root), "validate"])
        rc = 0 if result in (None, 0) else int(result)
    except SystemExit as exc:  # the failure path exits 1 (ADR 0134)
        rc = 0 if exc.code in (None, 0) else int(exc.code)
    return _Run(rc, out.getvalue(), err.getvalue())


class DanglingEndpointTest(unittest.TestCase):
    def test_child_local_endpoint_naming_no_node_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha", node_ids=("n1",))
            write_workspace_root(
                root,
                registered=("alpha",),
                repo_nodes=("alpha",),
                edges=(
                    cross_repo_edge("repo:alpha", f"alpha{SEP}nope"),
                ),
            )
            run = _validate(root)
            self.assertEqual(run.rc, 1, run.stdout + run.stderr)
            self.assertIs(run.payload["valid"], False)
            self.assertTrue(
                any("dangling reference" in e for e in run.payload["errors"]),
                run.payload["errors"],
            )

    def test_the_hybrid_endpoint_shape_fails(self) -> None:
        # The exact shape the evaluation found: a root-minted id namespaced
        # into a child, which belongs to neither id space.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha")
            write_workspace_root(
                root,
                registered=("alpha",),
                repo_nodes=("alpha",),
                edges=(
                    cross_repo_edge(
                        f"alpha{SEP}repo:alpha", f"alpha{SEP}repo:alpha"
                    ),
                ),
            )
            run = _validate(root)
            self.assertEqual(run.rc, 1, run.stdout + run.stderr)
            self.assertIs(run.payload["valid"], False)

    def test_repo_id_for_a_registered_absent_child_is_dangling(self) -> None:
        # Not "unverifiable": the root graph is readable, so the absence of a
        # repo: node it would have minted is a fact (ADR 0137 ss3).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha")
            write_child(root, "gone", state=MISSING)
            write_workspace_root(
                root,
                registered=("alpha", "gone"),
                repo_nodes=("alpha",),
                edges=(cross_repo_edge("repo:alpha", "repo:gone"),),
            )
            run = _validate(root)
            self.assertEqual(run.rc, 1, run.stdout + run.stderr)
            errors = " ".join(run.payload["errors"])
            self.assertIn("dangling reference", errors)
            self.assertNotIn("unverifiable", errors)


class UnverifiableEndpointTest(unittest.TestCase):
    """A registered child that cannot be read is not a pass (ADR 0134)."""

    def test_endpoint_in_a_missing_child_fails_with_distinct_text(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha")
            write_child(root, "gone", state=MISSING)
            write_workspace_root(
                root,
                registered=("alpha", "gone"),
                repo_nodes=("alpha",),
                edges=(cross_repo_edge("repo:alpha", f"gone{SEP}n1"),),
            )
            run = _validate(root)
            self.assertEqual(run.rc, 1, run.stdout + run.stderr)
            self.assertIs(run.payload["valid"], False)
            errors = " ".join(run.payload["errors"])
            self.assertIn("unverifiable reference", errors)
            self.assertNotIn("dangling reference", errors)
            # The message has to name the child and its state, because the
            # remedy is to restore the child, not to edit the edge.
            self.assertIn("gone", errors)
            self.assertIn(MISSING, errors)

    def test_the_stderr_report_points_at_the_remedy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha")
            write_child(root, "gone", state=MISSING)
            write_workspace_root(
                root,
                registered=("alpha", "gone"),
                repo_nodes=("alpha",),
                edges=(cross_repo_edge("repo:alpha", f"gone{SEP}n1"),),
            )
            run = _validate(root)
            self.assertIn("wd workspace status", run.stderr)


class ResolvableAndNonFederatedTest(unittest.TestCase):
    def test_resolvable_cross_repo_edges_still_validate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child(root, "alpha", node_ids=("n1",))
            write_child(root, "beta", node_ids=("n2",))
            write_workspace_root(
                root,
                registered=("alpha", "beta"),
                repo_nodes=("alpha", "beta"),
                edges=(
                    cross_repo_edge(f"alpha{SEP}n1", f"beta{SEP}n2"),
                    cross_repo_edge("repo:alpha", "repo:beta"),
                ),
            )
            run = _validate(root)
            self.assertEqual(run.rc, 0, run.stdout + run.stderr)
            # The envelope shape is part of the contract: unchanged keys.
            self.assertEqual(run.payload, {"valid": True, "errors": []})

    def test_without_a_workspace_the_shape_bypass_is_unchanged(self) -> None:
        # Same graph, no workspaces.yaml: no child id space exists, so the
        # only honest answer is the well-formedness one -- and it is the same
        # answer this command has always given (bypass preserved).
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
            run = _validate(root)
            self.assertEqual(run.rc, 0, run.stdout + run.stderr)
            self.assertEqual(run.payload, {"valid": True, "errors": []})


class ValidatorApiTest(unittest.TestCase):
    """Two branches of ``validate_edge`` the command itself cannot reach.

    ``wd graph validate`` always checks references and never builds an index
    outside a workspace root, so the interaction between the two is only
    reachable through the function.
    """

    _INDEX = FederationIdIndex(
        root_ids=frozenset({"repo:alpha"}),
        child_ids={"alpha": frozenset({"n1"})},
    )

    def _edge(self, to_id: str) -> dict:
        return {
            "from": "repo:alpha",
            "to": to_id,
            "type": "cross_repo:depends_on",
            "props": {},
        }

    def test_an_index_finds_what_the_shape_check_waves_through(self) -> None:
        edge = self._edge(f"alpha{SEP}nope")
        self.assertEqual(
            validate_edge(edge, {"repo:alpha"}, federation=True), []
        )
        errors = validate_edge(
            edge, {"repo:alpha"}, federation=True, id_index=self._INDEX
        )
        self.assertEqual([e.field for e in errors], ["to"])

    def test_check_refs_false_still_skips_every_reference_check(self) -> None:
        # `validate-fragment --allow-dangling` asks not to be told about
        # references at all; an index must not override that.
        errors = validate_edge(
            self._edge(f"alpha{SEP}nope"),
            {"repo:alpha"},
            check_refs=False,
            federation=True,
            id_index=self._INDEX,
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
