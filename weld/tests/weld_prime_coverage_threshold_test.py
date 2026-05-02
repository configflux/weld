"""Tests for the meaningful-description-coverage block emitted by ``wd prime``.

Pinned UX rules (see ``weld/_prime_coverage.py``):

- Coverage at 100% over the meaningful types: silent (no line emitted).
- Coverage strictly below the threshold (default 80%): escalate to
  ``[ACTION]`` with a concrete ``wd enrich --types=<missing>`` next-command.
- Coverage at or above the threshold but with a remaining gap: emit a
  soft ``[INFO]`` advisory (no next-step entry).

These tests are file-system black-box over the full ``prime()`` output
so the rendered prefix and Next-steps wiring are both covered.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.prime import prime  # noqa: E402


def _minimal_discover_yaml(n: int = 3) -> str:
    lines = ["sources:"]
    for i in range(n):
        lines.append(f'  - glob: "src{i}/**/*.py"')
        lines.append("    type: file")
        lines.append("    strategy: python_module")
    return "\n".join(lines) + "\n"


def _write_graph(weld_dir: Path, nodes: dict) -> None:
    payload = {"meta": {}, "nodes": nodes, "edges": []}
    (weld_dir / "graph.json").write_text(json.dumps(payload))


def _setup_root(td: str, nodes: dict) -> Path:
    root = Path(td)
    weld_dir = root / ".weld"
    weld_dir.mkdir()
    (weld_dir / "discover.yaml").write_text(_minimal_discover_yaml())
    _write_graph(weld_dir, nodes)
    (weld_dir / "file-index.json").write_text("{}")
    # Suppress the agent-integration hint so the coverage line is the
    # only thing competing for ACTION/INFO output.
    (root / ".claude" / "commands").mkdir(parents=True)
    (root / ".claude" / "commands" / "weld.md").write_text("test")
    return root


class TestPrimeCoverageBelowThreshold(unittest.TestCase):
    """Below-threshold coverage must escalate to [ACTION]."""

    def test_action_when_meaningful_coverage_below_80(self) -> None:
        # 1 described agent + 9 undescribed config nodes => 10%.
        nodes = {
            "agent:reviewer": {
                "type": "agent", "label": "reviewer",
                "props": {"description": "Reviews diffs."},
            },
        }
        for i in range(9):
            nodes[f"config:{i}"] = {
                "type": "config", "label": f"c{i}", "props": {},
            }
        with tempfile.TemporaryDirectory() as td:
            root = _setup_root(td, nodes)
            output = prime(root)
            self.assertIn("[ACTION", output)
            # The action line carries the concrete next-command.
            self.assertIn("wd enrich", output)
            # Missing-type hint must include 'config' (the actual gap)
            # and must NOT include 'agent' (already covered).
            self.assertIn("config", output)
            self.assertNotIn("--types=agent", output)

    def test_action_lands_in_next_steps_block(self) -> None:
        nodes = {
            f"config:{i}": {
                "type": "config", "label": f"c{i}", "props": {},
            }
            for i in range(5)
        }
        with tempfile.TemporaryDirectory() as td:
            root = _setup_root(td, nodes)
            output = prime(root)
            self.assertIn("Next steps:", output)
            tail = output.split("Next steps:")[1]
            self.assertIn("wd enrich", tail)


class TestPrimeCoverageAboveThreshold(unittest.TestCase):
    """At/above threshold with a remaining gap stays informational."""

    def test_info_at_or_above_threshold_with_gap(self) -> None:
        # 4 described agents + 1 undescribed config = 80% meaningful.
        nodes = {
            f"agent:a{i}": {
                "type": "agent", "label": f"a{i}",
                "props": {"description": f"agent {i}"},
            }
            for i in range(4)
        }
        nodes["config:gap"] = {
            "type": "config", "label": "gap", "props": {},
        }
        with tempfile.TemporaryDirectory() as td:
            root = _setup_root(td, nodes)
            output = prime(root)
            self.assertIn("[INFO", output)
            # Soft advisory: no [ACTION] tag for coverage, no
            # ``wd enrich`` next-step.
            self.assertNotIn("[ACTION", output.split("Next steps:")[0]
                             if "Next steps:" in output else output)
            self.assertNotIn("wd enrich", output)

    def test_silent_when_full_coverage(self) -> None:
        nodes = {
            "agent:reviewer": {
                "type": "agent", "label": "reviewer",
                "props": {"description": "Reviews diffs."},
            },
            "command:plan": {
                "type": "command", "label": "plan",
                "props": {"description": "Plans work."},
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = _setup_root(td, nodes)
            output = prime(root)
            # Full meaningful coverage => no description-coverage line
            # at all and no enrichment next-step.
            self.assertNotIn("meaningful nodes have descriptions", output)
            self.assertNotIn("wd enrich", output)


class TestPrimeCoverageSymbolDoesNotEscalate(unittest.TestCase):
    """``symbol`` nodes are not meaningful, so they cannot trigger an action.

    This is the regression guard for the headline reframing: an old
    user with a 1%-raw, 100%-meaningful graph must NOT see an [ACTION]
    nag every time they run ``wd prime``.
    """

    def test_high_symbol_low_raw_does_not_escalate(self) -> None:
        nodes = {
            "agent:reviewer": {
                "type": "agent", "label": "reviewer",
                "props": {"description": "Reviews diffs."},
            },
        }
        for i in range(50):
            nodes[f"symbol:{i}"] = {
                "type": "symbol", "label": f"s{i}", "props": {},
            }
        with tempfile.TemporaryDirectory() as td:
            root = _setup_root(td, nodes)
            output = prime(root)
            self.assertNotIn("wd enrich", output)
            # Old behaviour ("1% of nodes have descriptions") would
            # leak the raw figure here. The reframe replaces that line
            # with silence (full meaningful coverage).
            self.assertNotIn("of nodes have descriptions", output)


if __name__ == "__main__":
    unittest.main()
