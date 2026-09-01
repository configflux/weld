"""Finding-06 regression: ``impact`` on a ``repo:`` node with no cross-repo
resolver wired must report cannot-answer, not a fabricated ``Risk: LOW``.

Field eval v0.23.1 Finding 06: ``cross_repo_strategies: []`` is the documented
default, so a federation root graph holds zero cross-repo edges and every
``wd impact "repo:<child>"`` returned ``Risk: LOW / 0 dependents`` -- identical
to a genuinely isolated component. The truthful answer is UNKNOWN: dependents of
that repo cannot be computed because no resolver wires them. ADR 0134 authorises
the ``result_unknown`` code for exactly this case, surfaced as ``Risk: UNKNOWN``
with the reason and a pointer to ``cross_repo_strategies``.

These tests lock:

* the pure engine marks the outcome (``impact()`` sets ``risk_level`` UNKNOWN
  and a ``cannot_answer`` record) rather than scoring a fabricated zero;
* a measured zero (a non-``repo`` node with genuinely no dependents) stays
  ``LOW`` and carries no ``cannot_answer`` marker -- ADR 0134's boundary that a
  legitimate empty result must not be demoted to an error;
* a ``repo:`` node that *does* have inbound (cross-repo) edges is answered
  normally, so the marker fires only when dependents are uncomputable;
* the CLI exits non-zero and emits the ``error[result_unknown]`` line;
* the human render says UNKNOWN with the pointer.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.tests._impact_test_helpers import (
    ensure_repo_root_on_syspath,
    run_cli,
)

ensure_repo_root_on_syspath()

from weld._errors import RESULT_UNKNOWN  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.impact_core import format_human, impact  # noqa: E402
from weld.workspace import (  # noqa: E402
    ChildEntry,
    WorkspaceConfig,
    dump_workspaces_yaml,
)


_TS = "2026-08-29T00:00:00+00:00"


def _graph_from(
    nodes: dict,
    edges: list[dict] | None = None,
    *,
    stamp: dict | None = None,
    root: Path | None = None,
) -> Graph:
    """A federation root graph, optionally carrying ADR 0137's resolver stamp.

    *stamp* becomes ``meta.cross_repo``, which is what ``wd discover`` writes
    when cross-repo resolvers ran. *root* places the graph beside a real
    workspace config, which is the only way to reach the second cannot-answer
    variant.
    """
    graph = Graph(root if root is not None else Path("."))
    meta = {
        "version": SCHEMA_VERSION,
        "updated_at": _TS,
        "schema_version": 2,
    }
    if stamp is not None:
        meta["cross_repo"] = stamp
    graph._data = {"meta": meta, "nodes": nodes, "edges": edges or []}
    return graph


def _stamp(*strategies: str, edges: int = 0, dropped: int = 0) -> dict:
    """The ``meta.cross_repo`` record a resolver pass leaves behind."""
    return {
        "strategies": sorted(strategies),
        "resolved_children": ["libs-order-schema", "notify-service"],
        "edges": edges,
        "dropped": dropped,
    }


def _repo_node(name: str) -> dict:
    return {"type": "repo", "label": name, "props": {"path": name}}


def _file_node(path: str) -> dict:
    return {"type": "file", "label": path, "props": {"file": path}}


class ImpactRepoNodeCannotAnswerTest(unittest.TestCase):
    """A ``repo:`` node with no inbound edges is UNKNOWN, not LOW."""

    def test_isolated_repo_node_reports_result_unknown(self) -> None:
        graph = _graph_from({"repo:libs-order-schema": _repo_node("libs-order-schema")})

        result = impact(graph, target="repo:libs-order-schema")

        self.assertEqual(result["risk_level"], "UNKNOWN")
        marker = result.get("cannot_answer")
        self.assertIsInstance(marker, dict)
        self.assertEqual(marker["error_code"], RESULT_UNKNOWN)
        self.assertIn("cross_repo_strategies", marker["reason"])
        # The fabricated verdict must not have been computed.
        self.assertEqual(result["direct_dependents"], [])
        self.assertEqual(result["transitive_dependents"], [])

    def test_human_render_says_unknown_with_pointer(self) -> None:
        graph = _graph_from({"repo:libs-order-schema": _repo_node("libs-order-schema")})

        rendered = format_human(impact(graph, target="repo:libs-order-schema"))

        self.assertIn("Risk: UNKNOWN", rendered)
        self.assertIn("cross_repo_strategies", rendered)
        # The confident LOW verdict must be gone.
        self.assertNotIn("Risk: LOW", rendered)


class ImpactMeasuredZeroStaysLowTest(unittest.TestCase):
    """A genuine empty result is a correct answer -- ADR 0134 boundary."""

    def test_non_repo_node_with_no_dependents_stays_low(self) -> None:
        graph = _graph_from({"file:weld/utils.py": _file_node("weld/utils.py")})

        result = impact(graph, target="file:weld/utils.py")

        self.assertEqual(result["risk_level"], "LOW")
        self.assertNotIn("cannot_answer", result)

    def test_repo_node_with_inbound_edges_is_answered(self) -> None:
        # A wired cross-repo edge points at the repo node: dependents are
        # measurable, so the marker must NOT fire.
        graph = _graph_from(
            {
                "repo:libs-order-schema": _repo_node("libs-order-schema"),
                "repo:notify-service": _repo_node("notify-service"),
            },
            [
                {
                    "from": "repo:notify-service",
                    "to": "repo:libs-order-schema",
                    "type": "cross_repo:depends_on",
                    "props": {},
                },
            ],
        )

        result = impact(graph, target="repo:libs-order-schema")

        self.assertNotEqual(result["risk_level"], "UNKNOWN")
        self.assertNotIn("cannot_answer", result)
        direct_ids = {d["id"] for d in result["direct_dependents"]}
        self.assertIn("repo:notify-service", direct_ids)


class ImpactStampedZeroIsMeasuredTest(unittest.TestCase):
    """ADR 0137 ss5: a recorded resolver pass is what turns 0 into an answer.

    The pre-0.24.0 rule inferred "no resolver is wired" from the absence of an
    inbound edge, so it could not tell a repo nothing depends on from a repo
    nobody looked at -- and it stated the wrong one of those as a fact about a
    config file it never read. ``meta.cross_repo`` is the evidence that
    distinguishes them, and ``measured_by`` is the provenance travelling with
    the result so a reader does not have to take the zero on trust.
    """

    _SEED = "repo:libs-order-schema"

    def test_stamped_repo_with_no_inbound_edge_is_a_measured_zero(self) -> None:
        graph = _graph_from(
            {self._SEED: _repo_node("libs-order-schema")},
            stamp=_stamp("package_graph"),
        )

        result = impact(graph, target=self._SEED)

        self.assertEqual(result["risk_level"], "LOW")
        self.assertNotIn("cannot_answer", result)
        self.assertEqual(result["direct_dependents"], [])
        self.assertEqual(result["measured_by"], ["package_graph"])

    def test_measured_by_travels_with_a_non_empty_result(self) -> None:
        graph = _graph_from(
            {
                self._SEED: _repo_node("libs-order-schema"),
                "repo:notify-service": _repo_node("notify-service"),
            },
            [{
                "from": "repo:notify-service",
                "to": self._SEED,
                "type": "cross_repo:depends_on",
                "props": {},
            }],
            stamp=_stamp("compose_topology", "package_graph", edges=1),
        )

        result = impact(graph, target=self._SEED)

        self.assertEqual(result["measured_by"], ["compose_topology", "package_graph"])
        direct_ids = {d["id"] for d in result["direct_dependents"]}
        self.assertEqual(direct_ids, {"repo:notify-service"})

    def test_human_render_names_what_measured_it(self) -> None:
        graph = _graph_from(
            {self._SEED: _repo_node("libs-order-schema")},
            stamp=_stamp("package_graph"),
        )

        rendered = format_human(impact(graph, target=self._SEED))

        self.assertIn("Measured by: package_graph", rendered)
        self.assertNotIn("Risk: UNKNOWN", rendered)

    def test_an_unstamped_graph_carries_no_measured_by(self) -> None:
        """Absent, not empty: nothing measured it, so nothing is claimed."""
        graph = _graph_from(
            {
                self._SEED: _repo_node("libs-order-schema"),
                "repo:notify-service": _repo_node("notify-service"),
            },
            [{
                "from": "repo:notify-service",
                "to": self._SEED,
                "type": "cross_repo:depends_on",
                "props": {},
            }],
        )

        self.assertNotIn("measured_by", impact(graph, target=self._SEED))

    def test_a_file_seed_carries_no_measured_by(self) -> None:
        """Cross-repo provenance describes a question about a repository."""
        graph = _graph_from(
            {"file:weld/utils.py": _file_node("weld/utils.py")},
            stamp=_stamp("package_graph"),
        )

        self.assertNotIn("measured_by", impact(graph, target="file:weld/utils.py"))


class ImpactUnstampedReasonVariantsTest(unittest.TestCase):
    """Two ways to have no answer, told apart by reading the config.

    Both name ``cross_repo_strategies``, because both are about it -- but only
    one of them may say it is empty. The v0.24.0 evaluation printed "is empty"
    at a workspace whose ``cross_repo_strategies`` listed a resolver in the
    very file the sentence pointed at.
    """

    _SEED = "repo:libs-order-schema"

    def _reason(self, root: Path) -> str:
        graph = _graph_from({self._SEED: _repo_node("libs-order-schema")}, root=root)
        result = impact(graph, target=self._SEED)
        self.assertEqual(result["risk_level"], "UNKNOWN")
        return result["cannot_answer"]["reason"]

    def test_no_workspace_config_reports_the_empty_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reason = self._reason(Path(tmp))

        self.assertIn("cross_repo_strategies is empty", reason)

    def test_configured_strategies_report_the_no_pass_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            dump_workspaces_yaml(
                WorkspaceConfig(
                    children=[ChildEntry(name="notify-service", path="notify-service")],
                    cross_repo_strategies=["package_graph"],
                ),
                root / ".weld" / "workspaces.yaml",
            )
            reason = self._reason(root)

        self.assertIn("cross_repo_strategies", reason)
        self.assertIn("package_graph", reason)
        self.assertNotIn("is empty", reason)


class ImpactCannotAnswerCliTest(unittest.TestCase):
    """CLI exits non-zero and emits the structured error line."""

    def _seed_root(self, tmp) -> None:
        weld_dir = Path(tmp) / ".weld"
        weld_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {
                "version": SCHEMA_VERSION,
                "git_sha": "deadbeef",
                "updated_at": _TS,
                "schema_version": 2,
            },
            "nodes": {"repo:libs-order-schema": _repo_node("libs-order-schema")},
            "edges": [],
        }
        (weld_dir / "graph.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_cli_exits_nonzero_and_emits_error_line(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self._seed_root(tmp)
            code, stdout, stderr = run_cli(
                ["repo:libs-order-schema", "--root", tmp]
            )

        self.assertNotEqual(code, 0)
        self.assertIn(f"error[{RESULT_UNKNOWN}]", stderr)
        self.assertIn("cross_repo_strategies", stderr)

    def test_cli_json_carries_marker_and_exits_nonzero(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self._seed_root(tmp)
            code, stdout, _ = run_cli(
                ["repo:libs-order-schema", "--json", "--root", tmp]
            )

        self.assertNotEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result["risk_level"], "UNKNOWN")
        self.assertEqual(result["cannot_answer"]["error_code"], RESULT_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
