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


_TS = "2026-08-29T00:00:00+00:00"


def _graph_from(nodes: dict, edges: list[dict] | None = None) -> Graph:
    graph = Graph(Path("."))
    graph._data = {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": 2,
        },
        "nodes": nodes,
        "edges": edges or [],
    }
    return graph


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
