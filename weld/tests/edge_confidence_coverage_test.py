"""Dogfood canary: 100% confidence coverage on the weld repository graph.

ADR 0050 requires 100% confidence coverage on the dogfooded graph.
This test loads the repository's own ``.weld/graph.json`` and asserts
that every edge carries a value in
:data:`weld.contract.CONFIDENCE_VALUES`. The intent is twofold:

1. Catch a regression where a strategy refactor drops the
   ``confidence`` stamp from an emitted edge. The CI run would fail
   with a list of (source_strategy, edge_type) pairs that need
   attention -- the same shape the warning surface produces.
2. Surface coverage on the migration path: if ``wd discover`` writes
   a graph that is missing confidence anywhere, a new emission site
   has slipped past audit and the helper ``wd migrate
   --add-confidence`` is the immediate workaround.

Test design notes:

* The test is gated on the presence of the dogfood graph at
  ``.weld/graph.json``. Bazel sandboxing isolates tests from the
  workspace by default, but this file ships under ``tags =
  ["no-sandbox"]`` so the dogfood graph is reachable from the
  repository root. When the graph is missing (a fresh checkout that
  has not yet run ``wd discover``), the test logs a skip rather than
  failing -- the canary only fires when the graph exists.
* The diagnostic on failure groups offenders by ``source_strategy``
  so the message is short and actionable. Per-edge listing is the
  fallback for the rare case where a single strategy drops a single
  edge type.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

_repo_root_path = Path(__file__).resolve().parent.parent.parent
_repo_root = str(_repo_root_path)

from weld.contract import CONFIDENCE_VALUES  # noqa: E402


def _candidate_graph_paths() -> list[Path]:
    """Return paths to try for the dogfood graph in priority order.

    Bazel runs tests with cwd inside a runfiles tree; the actual
    workspace lives one level up via ``BUILD_WORKSPACE_DIRECTORY``
    (set by ``bazel run``) or ``BUILD_WORKING_DIRECTORY``. ``bazel
    test`` does not set those so we also fall back to walking up from
    the test file location until we find a directory that contains
    ``.weld/graph.json``.
    """
    candidates: list[Path] = []

    bazel_workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if bazel_workspace:
        candidates.append(Path(bazel_workspace) / ".weld" / "graph.json")

    bazel_working = os.environ.get("BUILD_WORKING_DIRECTORY")
    if bazel_working:
        candidates.append(Path(bazel_working) / ".weld" / "graph.json")

    # Walk up from the test file to find the repo root that contains
    # .weld/graph.json. Bazel sandbox runs would not find it, but
    # local `pytest` and `python -m unittest` runs do.
    current = _repo_root_path
    for _ in range(6):
        candidates.append(current / ".weld" / "graph.json")
        current = current.parent

    return candidates


def _load_dogfood_graph() -> dict | None:
    """Return the parsed graph or ``None`` if no dogfood graph exists."""
    for path in _candidate_graph_paths():
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return None


def _summarise_offenders(offenders: list[dict]) -> str:
    """Return a short diagnostic grouping offenders by source_strategy."""
    by_strategy: dict[tuple[str, str, str], int] = {}
    for edge in offenders:
        props = edge.get("props") or {}
        ss = str(props.get("source_strategy") or "<unset>")
        et = str(edge.get("type") or "<unset>")
        bucket = "missing" if "confidence" not in props else "invalid"
        by_strategy[(ss, et, bucket)] = by_strategy.get(
            (ss, et, bucket), 0,
        ) + 1
    lines = [
        f"{count}x source_strategy={ss!r} type={et!r} ({bucket})"
        for (ss, et, bucket), count in sorted(by_strategy.items())
    ]
    return "\n".join(lines)


class DogfoodConfidenceCoverageTest(unittest.TestCase):
    """The repository's own graph must show 100% confidence coverage."""

    def test_every_edge_has_a_valid_confidence(self) -> None:
        graph = _load_dogfood_graph()
        # Print the resolution outcome so a CI inspector can tell at a
        # glance whether the canary fired against real data or skipped
        # quietly. Both outcomes are valid (the dogfood graph is not
        # always reachable from a sandboxed test runner) but a silent
        # skip is exactly the failure mode this print prevents.
        if graph is None:
            print(
                "[edge_confidence_coverage_test] dogfood graph not "
                "reachable; skipping (no-op).",
                file=sys.stderr,
            )
        else:
            print(
                f"[edge_confidence_coverage_test] dogfood graph "
                f"reachable; auditing {len(graph.get('edges', []))} edges.",
                file=sys.stderr,
            )
        if graph is None:
            # When this test runs under Bazel sandboxing, the
            # repository's ``.weld/graph.json`` is not in the runfiles
            # tree, so the loader returns None. The canary still has
            # value when invoked from a regular shell (``pytest``,
            # ``python -m unittest``, or ``bazel run --no-sandbox``);
            # it skips quietly in the sandbox so it does not give a
            # false green when the graph genuinely is missing.
            self.skipTest(
                "No .weld/graph.json visible in the test environment. "
                "Run 'wd discover --output .weld/graph.json' from the "
                "repository root, then re-run this test outside the "
                "Bazel sandbox; the canary fires only when the graph "
                "is reachable.",
            )

        edges = graph.get("edges", [])
        if not edges:
            self.skipTest(
                "Dogfood graph has no edges; the coverage canary is a "
                "no-op until discovery emits at least one edge.",
            )

        offenders = []
        for edge in edges:
            props = edge.get("props") or {}
            confidence = props.get("confidence")
            if confidence not in CONFIDENCE_VALUES:
                offenders.append(edge)

        self.assertEqual(
            offenders, [],
            (
                "ADR 0050: every emitted edge in the dogfooded graph "
                "must carry a confidence value drawn from "
                f"{sorted(CONFIDENCE_VALUES)}. "
                f"Found {len(offenders)} of {len(edges)} edges in "
                "violation.\n"
                + _summarise_offenders(offenders)
                + "\nFix: stamp the missing 'confidence' on the "
                "producing strategy, then re-run discover. As an "
                "interim, 'wd migrate --add-confidence' will backfill "
                "the graph using the static defaults map."
            ),
        )


if __name__ == "__main__":
    unittest.main()
