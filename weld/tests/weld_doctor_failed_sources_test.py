"""``wd doctor`` reports entry-keyed source failures (bd um00).

A footprint-less source entry (a command-only ``external_json`` adapter, or
any strategy configured with no ``glob``/``path``/``files`` key) has no file
to carry a failure into ``files_with_failed_strategy`` or any freshness
check. ``DiscoveryState.sources_with_failed_strategy`` is the record; this
is the report -- deliberately not fed into ``coverage_stale`` / ``wd
stale``, matching bd 0jck's reasoning for the file-keyed sibling: a
permanently failing command would then earn a refresh, and re-spawn the
subprocess, on every read, forever.

Split into its own file rather than grown onto ``weld_doctor_test.py``,
matching how every other doctor concern already lives in its own file
(``weld_doctor_sections_test``, ``weld_doctor_trust_test``, etc.) -- the
monolithic file was already near the CLAUDE.md 400-line cap.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.discovery_state import DiscoveryState, save_state
from weld.doctor import doctor


def _setup_dir(root: Path) -> None:
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n  - strategy: external_json\n    command: \"tools/adapter\"\n",
        encoding="utf-8",
    )
    (root / ".weld" / "graph.json").write_text(
        '{"meta": {"schema_version": 4}, "nodes": {}, "edges": []}',
        encoding="utf-8",
    )


class DoctorFailedSourcesTest(unittest.TestCase):
    def test_no_state_file_means_no_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            results = doctor(root)
            self.assertFalse(
                [r for r in results if "source entry failed" in r.message],
            )

    def test_a_clean_state_means_no_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            save_state(root, DiscoveryState(files={}))
            results = doctor(root)
            self.assertFalse(
                [r for r in results if "source entry failed" in r.message],
            )

    def test_a_recorded_failure_is_reported_with_kind_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            save_state(
                root,
                DiscoveryState(
                    files={},
                    sources_with_failed_strategy={
                        "sha256:abc": {
                            "kind": "nonzero_exit", "reason": "exited 1: boom",
                        },
                    },
                ),
            )
            results = doctor(root)
            warnings = [
                r for r in results
                if r.level == "warn" and "source entry failed" in r.message
            ]
            self.assertTrue(warnings, [r.message for r in results])
            self.assertIn("nonzero_exit", warnings[0].message)
            self.assertIn("boom", warnings[0].message)
            self.assertEqual("Strategies", warnings[0].section)

    def test_multiple_failures_each_get_their_own_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            save_state(
                root,
                DiscoveryState(
                    files={},
                    sources_with_failed_strategy={
                        "sha256:a": {"kind": "timeout", "reason": "slow"},
                        "sha256:b": {"kind": "command_not_found", "reason": "gone"},
                    },
                ),
            )
            results = doctor(root)
            warnings = [
                r for r in results
                if r.level == "warn" and "source entry failed" in r.message
            ]
            self.assertEqual(2, len(warnings))


if __name__ == "__main__":
    unittest.main()
