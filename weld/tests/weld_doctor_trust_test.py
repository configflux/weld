"""``wd doctor`` per-language trust warning (epic: Tier-1 trust).

The Trust section warns when a language's unresolved-symbol ratio crosses
an absolute floor (:data:`weld._doctor_trust.UNRESOLVED_RATIO_FLOOR`).
Because weld persists no historical baseline, the threshold is an
absolute floor, not a regression-vs-history delta -- these tests pin that
behavior: a degraded language warns, a healthy / too-small language does
not, and the warning never raises the exit code.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld._doctor_trust import (
    TRUST_MIN_SYMBOLS,
    UNRESOLVED_RATIO_FLOOR,
    check_language_trust,
)
from weld.doctor import CheckResult, doctor


def _sym(node_id, language, origin):
    return {
        "id": node_id,
        "type": "symbol",
        "label": node_id,
        "props": {"language": language, "origin": origin},
    }


def _graph_with_language(language, *, total, unresolved):
    """Build a graph payload with *total* symbols of *language*.

    Exactly *unresolved* of them have ``origin == "unresolved"``.
    """
    nodes = {}
    for i in range(total):
        origin = "unresolved" if i < unresolved else "project"
        nid = f"symbol:{language}:n{i}"
        nodes[nid] = _sym(nid, language, origin)
    return {"meta": {"schema_version": 4}, "nodes": nodes, "edges": []}


def _write_graph(root: Path, payload: dict) -> Path:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(json.dumps(payload), encoding="utf-8")
    return weld_dir


class CheckLanguageTrustUnitTest(unittest.TestCase):
    def test_warns_when_ratio_above_floor(self) -> None:
        # 30 symbols, 20 unresolved -> ratio ~0.667, well above floor.
        payload = _graph_with_language("go", total=30, unresolved=20)
        with tempfile.TemporaryDirectory() as td:
            weld_dir = _write_graph(Path(td), payload)
            results = check_language_trust(weld_dir, CheckResult)
        warns = [r for r in results if r.level == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0].section, "Trust")
        self.assertIn("go", warns[0].message)

    def test_no_warn_when_ratio_within_floor(self) -> None:
        # 30 symbols, 3 unresolved -> ratio 0.1, under floor.
        payload = _graph_with_language("go", total=30, unresolved=3)
        with tempfile.TemporaryDirectory() as td:
            weld_dir = _write_graph(Path(td), payload)
            results = check_language_trust(weld_dir, CheckResult)
        self.assertFalse([r for r in results if r.level == "warn"])
        # A healthy measured graph still emits a visible ok row.
        self.assertTrue(any(r.level == "ok" and r.section == "Trust" for r in results))

    def test_small_language_never_warns(self) -> None:
        # Below TRUST_MIN_SYMBOLS even at 100% unresolved -> no warning.
        total = TRUST_MIN_SYMBOLS - 1
        payload = _graph_with_language("rust", total=total, unresolved=total)
        with tempfile.TemporaryDirectory() as td:
            weld_dir = _write_graph(Path(td), payload)
            results = check_language_trust(weld_dir, CheckResult)
        self.assertFalse([r for r in results if r.level == "warn"])

    def test_floor_is_absolute_not_baseline(self) -> None:
        # A graph just over the floor warns with no prior run recorded,
        # proving the threshold is an absolute floor (no baseline file).
        total = 100
        unresolved = int(total * (UNRESOLVED_RATIO_FLOOR + 0.05))
        payload = _graph_with_language("go", total=total, unresolved=unresolved)
        with tempfile.TemporaryDirectory() as td:
            weld_dir = _write_graph(Path(td), payload)
            results = check_language_trust(weld_dir, CheckResult)
        self.assertTrue([r for r in results if r.level == "warn"])

    def test_missing_graph_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            self.assertEqual(check_language_trust(weld_dir, CheckResult), [])


class DoctorTrustIntegrationTest(unittest.TestCase):
    """The Trust section participates in the full ``doctor`` run."""

    def _setup(self, root: Path, payload: dict) -> None:
        weld_dir = root / ".weld"
        weld_dir.mkdir(parents=True, exist_ok=True)
        (weld_dir / "discover.yaml").write_text(
            'sources:\n  - glob: "src/**/*.go"\n    type: file\n'
            "    strategy: tree_sitter\n",
            encoding="utf-8",
        )
        (weld_dir / "graph.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_doctor_surfaces_trust_warning(self) -> None:
        payload = _graph_with_language("go", total=40, unresolved=30)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._setup(root, payload)
            with patch("weld.doctor.is_git_repo", return_value=False):
                results = doctor(root)
        trust = [r for r in results if r.section == "Trust"]
        self.assertTrue(trust)
        self.assertTrue(any(r.level == "warn" and "go" in r.message for r in trust))

    def test_trust_warning_keeps_exit_zero(self) -> None:
        from weld.doctor import main as doctor_main

        payload = _graph_with_language("go", total=40, unresolved=30)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._setup(root, payload)
            (root / ".mcp.json").write_text('{"servers": {}}', encoding="utf-8")
            out = io.StringIO()
            with patch("weld.doctor.is_git_repo", return_value=False), \
                 patch("sys.stdout", out):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("Trust", out.getvalue())


if __name__ == "__main__":
    unittest.main()
