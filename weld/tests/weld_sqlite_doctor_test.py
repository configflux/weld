"""Tests for the sqlite-sidecar doctor check (ADR 0058).

Covers:

- absent sidecar -> silent (the sidecar is optional);
- fresh sidecar -> ``ok`` result;
- stale sidecar (SHA mismatch) -> ``warn`` result pointing at
  ``wd graph index --rebuild``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _sqlite_writer as writer  # noqa: E402
from weld._doctor_sqlite import check_sqlite_sidecar  # noqa: E402
from weld.serializer import dumps_graph  # noqa: E402


@dataclass
class FakeResult:
    """Stand-in for :class:`weld.doctor.CheckResult` in unit tests."""

    status: str
    message: str
    section: str


def _sample_graph() -> dict:
    return {
        "meta": {"schema_version": 1},
        "nodes": {
            "a": {"type": "service", "label": "a", "props": {"file": "a.py"}},
        },
        "edges": [],
    }


def _write_graph(root: Path) -> Path:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    graph_path.write_bytes(dumps_graph(_sample_graph()).encode("utf-8"))
    return graph_path


class DoctorSqliteSidecarTest(unittest.TestCase):
    def test_absent_sidecar_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root)
            self.assertEqual(check_sqlite_sidecar(root / ".weld", FakeResult), [])

    def test_fresh_sidecar_reports_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path = _write_graph(root)
            writer.build_sidecar_for_bytes(
                _sample_graph(), graph_path.read_bytes(),
                root / ".weld" / "graph.db", generated_at="t",
            )
            results = check_sqlite_sidecar(root / ".weld", FakeResult)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "ok")

    def test_stale_sidecar_reports_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path = _write_graph(root)
            writer.build_sidecar_for_bytes(
                _sample_graph(), graph_path.read_bytes(),
                root / ".weld" / "graph.db", generated_at="t",
            )
            # Mutate the JSON so the recorded SHA no longer matches.
            graph_path.write_bytes(graph_path.read_bytes() + b"\n")
            results = check_sqlite_sidecar(root / ".weld", FakeResult)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "warn")
            self.assertIn("wd graph index --rebuild", results[0].message)


if __name__ == "__main__":
    unittest.main()
