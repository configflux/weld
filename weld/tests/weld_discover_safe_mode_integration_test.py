"""End-to-end integration tests for ``wd discover --safe`` (ADR 0024).

Complementary to the unit tests in
``weld_discover_safe_mode_test.py``: these tests build a synthetic
project that contains *both* of the unsafe execution paths the flag
must disable -- a project-local Python strategy AND an
``external_json`` adapter -- and drive the full ``discover`` /
``discover.main`` entry points against it.

Each unsafe path is wired so it would create a marker file on the
filesystem if executed. The integration assertions are then simply:

* with ``--safe`` set, neither marker file exists after a full
  ``discover`` run, and a ``[weld] safe mode: skipped ...`` line is
  emitted to stderr for each refused path;
* without ``--safe`` (control), both marker files appear, proving
  the fixture would actually exercise both vectors if the flag did
  nothing -- guarding against a silent test where the safe-mode
  assertions trivially pass because nothing ever ran.

These tests deliberately do NOT touch
``weld/_discover_strategies.py`` -- they only consume the existing
public ``discover.discover`` and ``discover.main`` entry points so a
regression in the safe-mode plumbing fails the integration test
without requiring a unit re-test.
"""

from __future__ import annotations

import io
import stat
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.discover import discover, main as discover_main  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

# A project-local strategy body that drops a marker at import time AND on
# extract(). Either route would prove the local code ran; safe mode must
# block both.
_LOCAL_STRATEGY_BODY = textwrap.dedent(
    """\
    from pathlib import Path

    # Import-time side effect: would happen the moment safe mode allowed
    # the loader to spec-and-exec this module.
    Path(__file__).parent.joinpath("LOCAL_IMPORT_RAN").write_text("ran")

    def extract(root, source, context):
        from weld.strategies._helpers import StrategyResult
        # Extract-time side effect: would happen if the dispatcher
        # actually called us.
        Path(__file__).parent.joinpath("LOCAL_EXTRACT_RAN").write_text("ran")
        return StrategyResult(
            nodes={"unit:safe-integ-local": {
                "type": "unit", "label": "local", "props": {},
            }},
            edges=[],
            discovered_from=[],
        )
    """
)


def _build_external_json_adapter(root: Path) -> Path:
    """Write an executable adapter that drops a marker and emits valid JSON.

    A safe-mode regression that re-spawned this command would leave the
    marker file behind even though stdout would still be a valid empty
    fragment.
    """
    script = root / "marker_adapter.py"
    marker = root / "EXTERNAL_RAN"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json, sys
            from pathlib import Path
            Path({str(marker)!r}).write_text("ran")
            json.dump(
                {{"nodes": {{}}, "edges": [], "discovered_from": []}},
                sys.stdout,
            )
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _build_project(tmp: Path) -> tuple[Path, Path, Path, Path]:
    """Build a synthetic project containing both unsafe vectors.

    Returns ``(root, local_strat_dir, external_adapter, external_marker)``.
    The project-local strategy is named ``safe_integ_local_strategy`` --
    deliberately not the name of any bundled strategy so the safe-mode
    refusal cannot silently fall back to a bundled implementation and
    mask a regression in the project-local refusal path.
    """
    root = tmp
    weld_dir = root / ".weld"
    strat_dir = weld_dir / "strategies"
    strat_dir.mkdir(parents=True, exist_ok=True)

    # 1) Project-local strategy override
    name = "safe_integ_local_strategy"
    (strat_dir / f"{name}.py").write_text(_LOCAL_STRATEGY_BODY, encoding="utf-8")

    # 2) external_json adapter
    adapter = _build_external_json_adapter(root)
    external_marker = root / "EXTERNAL_RAN"

    # 3) discover.yaml referencing both
    (weld_dir / "discover.yaml").write_text(
        textwrap.dedent(
            f"""\
            sources:
              - strategy: {name}
              - strategy: external_json
                command: "{adapter}"
            topology:
              nodes: []
              edges: []
            """
        ),
        encoding="utf-8",
    )
    return root, strat_dir, adapter, external_marker


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class DiscoverSafeModeBlocksBothVectorsTest(unittest.TestCase):
    """End-to-end: ``discover(root, safe=True)`` blocks both unsafe paths."""

    def test_safe_mode_blocks_both_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, strat_dir, _adapter, external_marker = _build_project(Path(td))

            buf = io.StringIO()
            with redirect_stderr(buf):
                graph = discover(root, safe=True)

            # The discover entry point still returns a valid graph dict --
            # safe mode degrades, it does not error.
            self.assertIsInstance(graph, dict)
            self.assertIn("nodes", graph)

            # Project-local strategy: neither import-time nor extract-time
            # markers may exist.
            self.assertFalse(
                (strat_dir / "LOCAL_IMPORT_RAN").exists(),
                "project-local strategy was imported under --safe",
            )
            self.assertFalse(
                (strat_dir / "LOCAL_EXTRACT_RAN").exists(),
                "project-local strategy.extract() ran under --safe",
            )

            # external_json: the subprocess must not have run.
            self.assertFalse(
                external_marker.exists(),
                "external_json command was spawned under --safe",
            )

            # Stderr advertises *both* refusals so operators see what was
            # disabled.
            stderr = buf.getvalue()
            self.assertIn(
                "safe mode: skipped project-local strategy", stderr,
                f"missing project-local skipped log line: {stderr!r}",
            )
            self.assertIn(
                "safe_integ_local_strategy", stderr,
                f"project-local skipped line missing strategy name: {stderr!r}",
            )
            self.assertIn(
                "safe mode: skipped external_json", stderr,
                f"missing external_json skipped log line: {stderr!r}",
            )

            # The bundled-strategy fallback must NOT have produced a node
            # for our deliberately-unique strategy name (no bundled match
            # exists, so the result simply contains no node from that
            # source).
            self.assertNotIn(
                "unit:safe-integ-local",
                graph.get("nodes", {}),
                "project-local node leaked into graph despite --safe",
            )

    def test_unsafe_mode_executes_both_vectors_control(self) -> None:
        """Without --safe, the same fixture *does* execute both paths.

        This is the control case: it proves the markers really would be
        written if safe mode failed. Without this, the safe-mode
        assertions could trivially pass on a fixture that never wired up
        the unsafe paths in the first place.
        """
        with tempfile.TemporaryDirectory() as td:
            root, strat_dir, _adapter, external_marker = _build_project(Path(td))

            # Discard stderr noise; we only care about side effects here.
            with redirect_stderr(io.StringIO()):
                graph = discover(root)  # safe defaults to False

            self.assertTrue(
                (strat_dir / "LOCAL_IMPORT_RAN").exists(),
                "control: project-local strategy did not import",
            )
            self.assertTrue(
                (strat_dir / "LOCAL_EXTRACT_RAN").exists(),
                "control: project-local extract() did not run",
            )
            self.assertTrue(
                external_marker.exists(),
                "control: external_json command did not run",
            )
            # And the project-local node *did* land in the graph.
            self.assertIn("unit:safe-integ-local", graph.get("nodes", {}))


class DiscoverSafeModeViaMainTest(unittest.TestCase):
    """Driving the CLI ``main([...])`` with ``--safe`` blocks both vectors."""

    def test_safe_flag_via_main_blocks_both_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root, strat_dir, _adapter, external_marker = _build_project(tmp)
            output_path = tmp / "graph.json"

            err = io.StringIO()
            out = io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                rc = discover_main(
                    ["--safe", str(root), "--output", str(output_path)]
                )

            self.assertEqual(rc, 0, f"discover --safe exited {rc}; stderr={err.getvalue()!r}")
            # When --output is set, stdout stays empty and the graph
            # lands at the output path. Either confirms a successful run.
            self.assertTrue(
                output_path.exists(),
                "discover --safe --output did not write the graph file",
            )

            # Same side-effect assertions as the in-process test.
            self.assertFalse(
                (strat_dir / "LOCAL_IMPORT_RAN").exists(),
                "project-local strategy was imported under --safe (CLI path)",
            )
            self.assertFalse(
                (strat_dir / "LOCAL_EXTRACT_RAN").exists(),
                "project-local strategy.extract() ran under --safe (CLI path)",
            )
            self.assertFalse(
                external_marker.exists(),
                "external_json command was spawned under --safe (CLI path)",
            )

            stderr = err.getvalue()
            self.assertIn("safe mode: skipped project-local strategy", stderr)
            self.assertIn("safe mode: skipped external_json", stderr)


if __name__ == "__main__":
    unittest.main()
