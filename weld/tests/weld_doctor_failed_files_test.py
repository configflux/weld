"""``wd doctor`` reports file-keyed strategy failures (bd 0jck).

A file no strategy could speak for this run -- refused by ``--safe``, a
strategy that would not load, or a file a strategy could not parse -- has no
graph node and no ``files_with_no_nodes`` entry (that set means a strategy
*decided* nothing belongs there, not that nothing ran). ``DiscoveryState.
files_with_failed_strategy`` (bd hch4) is the record; this is the report --
the file-keyed sibling of ``weld_doctor_failed_sources_test.py`` (bd um00,
entry-keyed). Same reasoning: deliberately not fed into ``coverage_stale`` /
``wd stale``, since a permanently failing file would then earn a refresh,
which would fail the same way, on every read, forever.

Split into its own file rather than grown onto ``weld_doctor_test.py`` or
``weld_doctor_failed_sources_test.py``, matching how every other doctor
concern already lives in its own file.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._doctor_strategies import _MAX_FAILED_FILES_SHOWN
from weld.discovery_state import DiscoveryState, save_state
from weld.doctor import doctor


def _setup_dir(root: Path) -> None:
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n  - glob: \"**/*.py\"\n    strategy: python_module\n",
        encoding="utf-8",
    )
    (root / ".weld" / "graph.json").write_text(
        '{"meta": {"schema_version": 4}, "nodes": {}, "edges": []}',
        encoding="utf-8",
    )


def _failed_warnings(results) -> list:
    return [
        r for r in results
        if r.level == "warn" and "could not be processed" in r.message
    ]


class DoctorFailedFilesTest(unittest.TestCase):
    def test_no_state_file_means_no_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            results = doctor(root)
            self.assertFalse(_failed_warnings(results))

    def test_a_clean_state_means_no_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            save_state(root, DiscoveryState(files={}))
            results = doctor(root)
            self.assertFalse(_failed_warnings(results))

    def test_files_with_no_nodes_alone_is_not_reported(self) -> None:
        """A strategy that legitimately produced nothing is not a failure."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            save_state(
                root,
                DiscoveryState(files={}, files_with_no_nodes={"weld/__init__.py"}),
            )
            results = doctor(root)
            self.assertFalse(_failed_warnings(results))

    def test_a_recorded_failure_is_reported_with_path_and_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            save_state(
                root,
                DiscoveryState(
                    files={}, files_with_failed_strategy={"weld/broken.py"},
                ),
            )
            results = doctor(root)
            warnings = _failed_warnings(results)
            self.assertEqual(1, len(warnings), [r.message for r in results])
            self.assertIn("1 file", warnings[0].message)
            self.assertIn("weld/broken.py", warnings[0].message)
            self.assertEqual("Strategies", warnings[0].section)

    def test_multiple_failures_under_bound_are_all_named(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            failed = {f"weld/mod_{i:02d}.py" for i in range(3)}
            save_state(
                root, DiscoveryState(files={}, files_with_failed_strategy=failed),
            )
            results = doctor(root)
            warnings = _failed_warnings(results)
            self.assertEqual(1, len(warnings))
            self.assertIn("3 files", warnings[0].message)
            for path in failed:
                self.assertIn(path, warnings[0].message)
            self.assertNotIn("more", warnings[0].message)

    def test_over_bound_failures_are_capped_but_count_stays_true(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            total = _MAX_FAILED_FILES_SHOWN + 5
            failed = {f"weld/mod_{i:02d}.py" for i in range(total)}
            save_state(
                root, DiscoveryState(files={}, files_with_failed_strategy=failed),
            )
            results = doctor(root)
            warnings = _failed_warnings(results)
            self.assertEqual(1, len(warnings), [r.message for r in results])
            message = warnings[0].message
            # The true total is always reported, never silently dropped.
            self.assertIn(f"{total} files", message)
            self.assertIn("+5 more", message)
            shown_paths = sorted(failed)[:_MAX_FAILED_FILES_SHOWN]
            omitted_paths = sorted(failed)[_MAX_FAILED_FILES_SHOWN:]
            for path in shown_paths:
                self.assertIn(path, message)
            for path in omitted_paths:
                self.assertNotIn(path, message)

    def test_entry_keyed_and_file_keyed_reports_coexist(self) -> None:
        """bd um00's report and bd 0jck's report do not clobber each other."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            save_state(
                root,
                DiscoveryState(
                    files={},
                    files_with_failed_strategy={"weld/broken.py"},
                    sources_with_failed_strategy={
                        "sha256:abc": {
                            "kind": "nonzero_exit", "reason": "exited 1",
                        },
                    },
                ),
            )
            results = doctor(root)
            file_warnings = _failed_warnings(results)
            source_warnings = [
                r for r in results
                if r.level == "warn" and "source entry failed" in r.message
            ]
            self.assertEqual(1, len(file_warnings))
            self.assertEqual(1, len(source_warnings))

    def test_doctor_does_not_write_state_file(self) -> None:
        """Read-only: reporting a failure never rewrites discovery-state.json."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            save_state(
                root,
                DiscoveryState(
                    files={}, files_with_failed_strategy={"weld/broken.py"},
                ),
            )
            state_path = root / ".weld" / "discovery-state.json"
            before = state_path.read_bytes()
            doctor(root)
            after = state_path.read_bytes()
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
