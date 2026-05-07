"""Per-framework blast-radius fixture harness (ADR 0047).

This harness pins the user's "deterministic blast radius across every
supported framework" requirement. Each subdirectory under
``weld/tests/fixtures/blast_radius/`` is a small sample repo
(3-8 source files). For each fixture the harness:

1. Copies the fixture into a writable scratch tree (Bazel sandboxes the
   ``data`` filegroup as read-only). ``MODULE.bazel.in`` /
   ``BUILD.bazel.in`` files are renamed back to ``MODULE.bazel`` /
   ``BUILD.bazel`` during the copy so the parent Bazel build never sees
   them but discovery still does.
2. Runs :func:`weld.discover.discover` against the scratch tree and
   compares the canonical bytes to ``expected/graph.json``.
3. Re-runs discover a second time on the same scratch tree (state file
   intact) and asserts the canonical bytes match the first run -- the
   in-process determinism regression complementing
   ``discover_twice_identical_test`` at the harness layer.
4. For each ``expected/impact_<seed>.json`` golden, calls
   :func:`weld.impact_core.impact` with the documented seed and asserts
   byte-identical output.

When a comparison fails the assertion message names the offending
fixture and prints up to ~30 lines of unified diff so the regen path
is obvious. When the schema or strategies legitimately change, set
``REGEN_BLAST_RADIUS_GOLDENS=1`` (or run
``bazel run //weld/tests:regenerate_blast_radius_goldens``) and re-run.

Adding a fixture requires only:

1. Create ``weld/tests/fixtures/blast_radius/<name>/`` with a source
   tree and ``.weld/discover.yaml``.
2. Drop a placeholder ``expected/impact_<slug>.json`` containing a
   ``target.input`` field for each documented seed, then run regen.
3. Add a ``<name>/README.md`` describing the scenario.

No edits to this file are necessary -- the harness auto-discovers any
fixture directory that contains a ``.weld/discover.yaml``. Helper
implementation lives in ``_blast_radius_harness.py`` so this file
stays focused on the test cases.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.tests._blast_radius_harness import (  # noqa: E402
    canonical_graph_text,
    copy_fixture,
    diff_snippet,
    discover,
    discover_fixture_names,
    fixtures_dir,
    impact_canonical_text,
    impact_envelope,
    is_regen_mode,
    regen_env_var,
    seed_from_golden,
    seed_pairs,
)

_REGEN_HINT = (
    "Golden drift detected. If the change is intentional, regenerate with:\n"
    f"  {regen_env_var()}=1 bazel test \\\n"
    "    //weld/tests:weld_blast_radius_fixtures_test --test_output=all\n"
    "(or `bazel run //weld/tests:regenerate_blast_radius_goldens`).\n"
    "Then review the updated golden JSON before committing."
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_one_fixture(
    fixture_name: str,
    test_case: unittest.TestCase,
    *,
    regen: bool,
) -> None:
    """Drive discover + impact assertions for a single fixture.

    Public so the regen wrapper script can reuse it. On *regen* mode
    the function rewrites the goldens and skips assertions; the set
    of impact seeds is taken from existing ``impact_*.json`` filenames
    so regen does not invent new seeds.
    """
    src_root = fixtures_dir() / fixture_name
    expected_dir = src_root / "expected"
    with tempfile.TemporaryDirectory(prefix=f"blast-{fixture_name}-") as tmp:
        scratch = Path(tmp) / fixture_name
        copy_fixture(src_root, scratch)
        graph1 = discover(scratch, incremental=False)
        canonical1 = canonical_graph_text(graph1)
        # In-process determinism regression: re-run discover and
        # assert byte identity. Independent of the golden compare --
        # a fixture can drift relative to a stale golden but still
        # be internally deterministic.
        graph2 = discover(scratch, incremental=False)
        canonical2 = canonical_graph_text(graph2)
        test_case.assertEqual(
            canonical1, canonical2,
            f"[{fixture_name}] discover() is non-deterministic across two "
            "consecutive runs in the same process. This indicates a "
            "regression in weld.discover or a strategy the fixture wires; "
            "investigate before regenerating goldens.",
        )

        graph_golden = expected_dir / "graph.json"
        if regen:
            _write(graph_golden, canonical1)
        else:
            expected = _read(graph_golden)
            if canonical1 != expected:
                snippet = diff_snippet(
                    canonical1, expected, fixture_name, "graph.json",
                )
                test_case.fail(
                    f"[{fixture_name}] discover() output drifted from "
                    f"expected/graph.json:\n{snippet}\n{_REGEN_HINT}"
                )

        # Impact comparisons -- one per impact_<slug>.json golden.
        for slug, golden in seed_pairs(expected_dir):
            seed, depth = seed_from_golden(golden)
            envelope = impact_envelope(graph1, seed, depth)
            actual = impact_canonical_text(envelope)
            if regen:
                _write(golden, actual)
                continue
            expected = _read(golden)
            if actual != expected:
                snippet = diff_snippet(
                    actual, expected, fixture_name, golden.name,
                )
                test_case.fail(
                    f"[{fixture_name}] impact(seed={seed!r}, depth={depth}) "
                    f"drifted from expected/{golden.name}:\n{snippet}\n"
                    f"{_REGEN_HINT}"
                )


class BlastRadiusFixturesTest(unittest.TestCase):
    """ADR 0047 harness: pin discover + impact for every supported scenario."""

    def test_each_fixture_matches_goldens(self) -> None:
        """Iterate every fixture; one subTest per fixture for clear naming."""
        names = discover_fixture_names()
        self.assertGreater(
            len(names), 0,
            f"No fixtures discovered under {fixtures_dir()}. The "
            "blast-radius harness must ship at least the v1 set "
            "(python_pip, typescript_node, dockerfile_compose, "
            "bazel_python, cross_artefact). If the directory is "
            "intentionally empty, delete this test target.",
        )
        regen = is_regen_mode()
        for name in names:
            with self.subTest(fixture=name):
                run_one_fixture(name, self, regen=regen)
        if regen:
            self.skipTest(
                f"Regenerated goldens for fixtures: {', '.join(names)}. "
                f"Re-run without {regen_env_var()} to verify."
            )

    def test_v1_fixture_set_is_complete(self) -> None:
        """The v1 fixture set is fixed (ADR 0047 §3).

        Fixtures may be added; none of the v1 set may be removed
        without an ADR amendment. This test fails loudly if someone
        deletes one accidentally.
        """
        required = {
            "python_pip",
            "typescript_node",
            "dockerfile_compose",
            "bazel_python",
            "cross_artefact",
        }
        present = set(discover_fixture_names())
        missing = required - present
        self.assertFalse(
            missing,
            f"Missing v1 blast-radius fixtures: {sorted(missing)}. "
            "Restore them or amend ADR 0047 to drop them.",
        )

    def test_diff_message_is_readable_on_mismatch(self) -> None:
        """Deliberate-failure check for the diff snippet helper.

        This is the user's productivity hook: when a golden drifts the
        error message must point at the offending fixture and print a
        compact diff. We exercise the helper here so a regression in
        the snippet format fails loudly rather than silently producing
        a useless 'AssertionError: True is not False' style message.
        """
        actual = '{\n  "node": "a"\n}\n'
        expected = '{\n  "node": "b"\n}\n'
        snippet = diff_snippet(actual, expected, "synthetic", "graph.json")
        self.assertIn("expected/graph.json", snippet)
        self.assertIn("actual/graph.json", snippet)
        self.assertIn('-  "node": "b"', snippet)
        self.assertIn('+  "node": "a"', snippet)


if __name__ == "__main__":
    unittest.main()
