"""Unit tests for :mod:`weld._auto_refresh` helper logic (ADR 0051).

These tests pin the opt-out resolution chain, the ``--no-refresh`` and
``--safe`` precedence rules, the federated-root skip, and the
missing-graph short-circuit. They isolate the helper from the discovery
pipeline by patching ``compute_stale_info`` and ``_discover_single_repo``
so they assert the *control flow* the ADR mandates -- not the cost of
running real discovery.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _auto_refresh  # noqa: E402


def _seed_graph(root: Path, *, meta: dict | None = None) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta or {"version": 4},
        "nodes": {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    # Auto-refresh skips refresh when discover.yaml is missing (so
    # synthetic-fixture tests stay independent). These unit tests
    # exercise refresh control flow directly so they need a yaml on
    # disk to pass that gate.
    (weld_dir / "discover.yaml").write_text(
        "sources: []\n", encoding="utf-8",
    )


class StripNoRefreshFlagTest(unittest.TestCase):
    def test_strip_removes_flag_and_signals_seen(self) -> None:
        out, seen = _auto_refresh.strip_no_refresh(
            ["query", "Store", "--no-refresh"]
        )
        self.assertEqual(out, ["query", "Store"])
        self.assertTrue(seen)

    def test_strip_when_absent_returns_unchanged(self) -> None:
        out, seen = _auto_refresh.strip_no_refresh(["query", "Store"])
        self.assertEqual(out, ["query", "Store"])
        self.assertFalse(seen)


class EnvVarOptOutTest(unittest.TestCase):
    def test_env_zero_disables_silently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            stderr = io.StringIO()
            with mock.patch.object(
                _auto_refresh, "_do_refresh"
            ) as did:
                result = _auto_refresh.auto_refresh_if_stale(
                    root,
                    no_refresh=False,
                    safe=False,
                    json_output=False,
                    env={"WELD_AUTO_REFRESH": "0"},
                    stderr=stderr,
                )
            self.assertIsNone(result)
            did.assert_not_called()
            # Silent when env-disabled (no banner, no warning).
            self.assertEqual(stderr.getvalue(), "")

    def test_env_off_disables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            with mock.patch.object(
                _auto_refresh, "_do_refresh"
            ) as did:
                _auto_refresh.auto_refresh_if_stale(
                    root,
                    env={"WELD_AUTO_REFRESH": "off"},
                    stderr=io.StringIO(),
                )
            did.assert_not_called()

    def test_env_on_does_not_force_refresh_when_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            with mock.patch.object(
                _auto_refresh, "_do_refresh"
            ) as did, mock.patch.object(
                _auto_refresh, "compute_stale_info", create=True,
                return_value={"stale": False},
            ):
                _auto_refresh.auto_refresh_if_stale(
                    root,
                    env={"WELD_AUTO_REFRESH": "1"},
                    stderr=io.StringIO(),
                )
            did.assert_not_called()


class NoRefreshFlagTest(unittest.TestCase):
    def test_no_refresh_warns_and_skips_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            stderr = io.StringIO()
            with mock.patch(
                "weld._staleness.compute_stale_info",
                return_value={"stale": True},
            ), mock.patch.object(
                _auto_refresh, "_do_refresh"
            ) as did:
                result = _auto_refresh.auto_refresh_if_stale(
                    root,
                    no_refresh=True,
                    env={},
                    stderr=stderr,
                )
            self.assertIsNone(result)
            did.assert_not_called()
            self.assertIn("--no-refresh in effect", stderr.getvalue())
            self.assertIn("stale", stderr.getvalue())


class SafeModePrecedenceTest(unittest.TestCase):
    def test_safe_mode_threads_through_to_discover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            with mock.patch(
                "weld._staleness.compute_stale_info",
                return_value={"stale": True},
            ), mock.patch(
                "weld.discover._discover_single_repo"
            ) as discover, mock.patch(
                "weld.discovery_state.load_state",
                return_value=None,
            ):
                _auto_refresh.auto_refresh_if_stale(
                    root,
                    safe=True,
                    env={},
                    stderr=io.StringIO(),
                )
            discover.assert_called_once()
            kwargs = discover.call_args.kwargs
            self.assertTrue(kwargs.get("safe"))

    def test_safe_mode_suppresses_banner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            stderr = io.StringIO()
            with mock.patch(
                "weld._staleness.compute_stale_info",
                return_value={"stale": True},
            ), mock.patch(
                "weld.discover._discover_single_repo"
            ), mock.patch(
                "weld.discovery_state.load_state",
                return_value=None,
            ), mock.patch(
                "weld._auto_refresh._record_telemetry_event"
            ):
                _auto_refresh.auto_refresh_if_stale(
                    root,
                    safe=True,
                    env={},
                    stderr=stderr,
                )
            # ``--safe`` runs refresh silently per ADR 0051.
            self.assertEqual(stderr.getvalue(), "")


class JsonOutputSuppressesBannerTest(unittest.TestCase):
    def test_json_output_silences_banner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            stderr = io.StringIO()
            with mock.patch(
                "weld._staleness.compute_stale_info",
                return_value={"stale": True},
            ), mock.patch(
                "weld.discover._discover_single_repo"
            ), mock.patch(
                "weld.discovery_state.load_state",
                return_value=None,
            ), mock.patch(
                "weld._auto_refresh._record_telemetry_event"
            ):
                _auto_refresh.auto_refresh_if_stale(
                    root,
                    json_output=True,
                    env={},
                    stderr=stderr,
                )
            self.assertEqual(stderr.getvalue(), "")


class FederatedRootSkipTest(unittest.TestCase):
    def test_federated_root_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            (root / ".weld" / "workspaces.yaml").write_text(
                "version: 1\n", encoding="utf-8",
            )
            with mock.patch.object(
                _auto_refresh, "_do_refresh"
            ) as did:
                result = _auto_refresh.auto_refresh_if_stale(
                    root, env={}, stderr=io.StringIO(),
                )
            self.assertIsNone(result)
            did.assert_not_called()


class MissingGraphShortCircuitTest(unittest.TestCase):
    def test_missing_graph_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            with mock.patch.object(
                _auto_refresh, "_do_refresh"
            ) as did:
                result = _auto_refresh.auto_refresh_if_stale(
                    root, env={}, stderr=io.StringIO(),
                )
            self.assertIsNone(result)
            did.assert_not_called()

    def test_corrupt_graph_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            (root / ".weld" / "graph.json").write_text(
                "{not json", encoding="utf-8",
            )
            with mock.patch.object(
                _auto_refresh, "_do_refresh"
            ) as did:
                result = _auto_refresh.auto_refresh_if_stale(
                    root, env={}, stderr=io.StringIO(),
                )
            self.assertIsNone(result)
            did.assert_not_called()


class FreshGraphSkipsRefreshTest(unittest.TestCase):
    def test_fresh_graph_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            with mock.patch(
                "weld._staleness.compute_stale_info",
                return_value={"stale": False},
            ), mock.patch.object(
                _auto_refresh, "_do_refresh"
            ) as did:
                result = _auto_refresh.auto_refresh_if_stale(
                    root, env={}, stderr=io.StringIO(),
                )
            self.assertIsNone(result)
            did.assert_not_called()


class StaleGraphTriggersRefreshTest(unittest.TestCase):
    def test_stale_graph_triggers_refresh_and_returns_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)

            with mock.patch(
                "weld._staleness.compute_stale_info",
                return_value={"stale": True},
            ), mock.patch(
                "weld.discover._discover_single_repo"
            ) as discover, mock.patch(
                "weld.discovery_state.load_state",
                return_value=None,
            ), mock.patch(
                "weld._auto_refresh._record_telemetry_event"
            ) as record:
                result = _auto_refresh.auto_refresh_if_stale(
                    root, env={}, stderr=io.StringIO(),
                )
            self.assertIsNotNone(result)
            assert result is not None  # for type checker
            self.assertTrue(result["refreshed"])
            self.assertEqual(result["files_changed"], 0)
            self.assertGreaterEqual(result["elapsed_ms"], 0)
            discover.assert_called_once()
            record.assert_called_once()


class DiscoveryFailureSwallowedTest(unittest.TestCase):
    def test_discovery_exception_returns_none_quietly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            with mock.patch(
                "weld._staleness.compute_stale_info",
                return_value={"stale": True},
            ), mock.patch(
                "weld.discover._discover_single_repo",
                side_effect=RuntimeError("boom"),
            ), mock.patch(
                "weld.discovery_state.load_state",
                return_value=None,
            ):
                # Must not raise; refresh failures degrade to "serve stale".
                result = _auto_refresh.auto_refresh_if_stale(
                    root, env={}, stderr=io.StringIO(),
                )
            self.assertIsNone(result)


class CountFilesChangedTest(unittest.TestCase):
    def test_added_modified_deleted_combine(self) -> None:
        pre = {"a.py": "h1", "b.py": "h2", "c.py": "h3"}
        post = {"a.py": "h1", "b.py": "h2-new", "d.py": "h4"}
        # b modified, c deleted, d added => 3
        self.assertEqual(
            _auto_refresh._count_files_changed(pre, post), 3,
        )

    def test_no_changes_is_zero(self) -> None:
        pre = {"a.py": "h1"}
        post = {"a.py": "h1"}
        self.assertEqual(
            _auto_refresh._count_files_changed(pre, post), 0,
        )


if __name__ == "__main__":
    unittest.main()
