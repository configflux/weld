"""``wd workspace status`` reports from disk and names the drift (ADR 0138).

The end-to-end parity between this command and ``wd stale`` is pinned next
door, in ``weld_federation_child_staleness_surface_test``'s
``RosterConventionParityTest``. This suite pins the mechanism underneath it:
what :func:`weld._workspace_drift.reconcile` keeps from the stored ledger,
what it takes from the probe, which differences become drift rows, and what
the two output surfaces do with them.

One test here is load-bearing beyond its own assertion.
``test_graph_drift_survives_the_status_overlay`` fails the moment someone
"simplifies" the overlay into replacing the whole stored entry with the
probed one: ADR 0066's tier-2 check is *recorded digest != current bytes*, so
a freshly probed ``graph_sha256`` compares the file against itself, and the
``graph_drift`` reason quietly stops existing on this surface with every
other test still green.

Lives in its own file because ``weld_workspace_state_test.py`` is at the
400-line cap.
"""

from __future__ import annotations

import io
import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld._workspace_drift import drift_lines, observed_children, reconcile
from weld.tests._federation_staleness_fixtures import (
    _init_repo,
    _run_cli,
    _seed_root,
    _write_child_graph,
)
from weld.workspace import ChildEntry


def _entry(status: str, **extra: object) -> dict:
    """A ledger entry in the shape ``workspace-state.json`` stores."""
    entry: dict = {
        "status": status,
        "head_sha": "a" * 40,
        "head_ref": "refs/heads/main",
        "is_dirty": False,
        "graph_path": "svc/.weld/graph.json",
        "graph_sha256": "stored-digest",
        "last_seen_utc": "2026-01-01T00:00:00Z",
        "graph_mtime_ns": 1,
        "node_count": 7,
        "edge_count": 3,
    }
    entry.update(extra)
    return entry


def _state(**children: dict) -> dict:
    return {"version": 1, "children": dict(children)}


class ReconcileTest(unittest.TestCase):
    """Which row wins: the ledger's, or the probe's (ADR 0138 §2)."""

    def test_a_contradicted_entry_is_replaced_whole(self) -> None:
        """Not just ``status``: the whole recorded row described the wrong child.

        A row whose lifecycle the disk contradicts recorded its ``head_ref``,
        ``head_sha`` and ``is_dirty`` of a repository that is no longer in
        that state. Overlaying only the status left them standing, and the
        renderer printed a branch, a SHA and ``dirty`` for a directory that is
        not there.
        """
        probed = _entry(
            "missing",
            head_sha=None,
            head_ref=None,
            is_dirty=False,
            graph_sha256=None,
        )
        state, drift = reconcile(
            _state(svc=_entry("present", is_dirty=True)),
            {"svc": probed},
        )
        self.assertEqual(state["children"]["svc"], probed)
        self.assertEqual(
            drift, [{"name": "svc", "stored": "present", "observed": "missing"}],
        )

    def test_an_agreeing_entry_is_reported_unchanged(self) -> None:
        """The recorded row survives where the disk confirms it.

        ``graph_sha256`` is the field that matters most: it is the baseline
        the ADR 0066 tier-2 drift check compares current bytes against, and
        ``present`` children are the only ones that check ever runs over. The
        rest of the row is what answers "what did the last discover see, and
        when", so none of it is overwritten either.
        """
        state, drift = reconcile(
            _state(svc=_entry("present")),
            {"svc": _entry(
                "present",
                graph_sha256="live-digest",
                last_seen_utc="2026-08-30T00:00:00Z",
                head_sha="b" * 40,
                node_count=99,
            )},
        )
        child = state["children"]["svc"]
        self.assertEqual(child["graph_sha256"], "stored-digest")
        self.assertEqual(child["last_seen_utc"], "2026-01-01T00:00:00Z")
        self.assertEqual(child["head_sha"], "a" * 40)
        self.assertEqual(child["node_count"], 7)
        self.assertEqual(drift, [])

    def test_registered_child_absent_from_the_ledger(self) -> None:
        """Registered since the last discover: nothing stored to keep."""
        probed = _entry("present", graph_sha256="live-digest")
        state, drift = reconcile(_state(), {"new-svc": probed})
        self.assertEqual(state["children"]["new-svc"], probed)
        self.assertEqual(
            drift,
            [{"name": "new-svc", "stored": None, "observed": "present"}],
        )

    def test_ledger_child_no_longer_registered_leaves_the_roster(self) -> None:
        """Dropped from ``workspaces.yaml``: reported, then removed.

        Keeping it in ``children`` would restate the bug this ADR fixes one
        level up -- reporting a registration the registry no longer holds --
        so the name survives only in the drift row.
        """
        state, drift = reconcile(
            _state(gone=_entry("present"), svc=_entry("present")),
            {"svc": _entry("present")},
        )
        self.assertEqual(sorted(state["children"]), ["svc"])
        self.assertEqual(
            drift, [{"name": "gone", "stored": "present", "observed": None}],
        )

    def test_unreadable_ledger_entry_keeps_the_renderers_word(self) -> None:
        state, drift = reconcile(
            {"version": 1, "children": {"svc": "not-an-object"}},
            {"svc": _entry("present")},
        )
        self.assertEqual(state["children"]["svc"]["status"], "present")
        self.assertEqual(
            drift, [{"name": "svc", "stored": "invalid", "observed": "present"}],
        )

    def test_no_registry_returns_the_ledger_untouched(self) -> None:
        """``None`` means "nothing to compare against", not "everything drifted"."""
        original = _state(svc=_entry("present"))
        state, drift = reconcile(original, None)
        self.assertIs(state, original)
        self.assertEqual(drift, [])

    def test_the_loaded_ledger_is_never_mutated(self) -> None:
        original = _state(svc=_entry("present"))
        snapshot = json.dumps(original, sort_keys=True)
        reconcile(original, {"svc": _entry("missing")})
        self.assertEqual(json.dumps(original, sort_keys=True), snapshot)

    def test_rows_are_sorted_by_name(self) -> None:
        _, drift = reconcile(
            _state(alpha=_entry("present"), zulu=_entry("present")),
            {"zulu": _entry("missing"), "alpha": _entry("corrupt")},
        )
        self.assertEqual([row["name"] for row in drift], ["alpha", "zulu"])


class DriftBlockTest(unittest.TestCase):
    """The human block: silent on agreement, specific on disagreement."""

    def test_silent_when_the_ledger_agrees(self) -> None:
        self.assertEqual(drift_lines([]), [])

    def test_each_row_gets_its_own_phrasing_plus_the_remedy(self) -> None:
        lines = drift_lines([
            {"name": "docs-site", "stored": "present", "observed": "missing"},
            {"name": "gone", "stored": "present", "observed": None},
            {"name": "new-svc", "stored": None, "observed": "present"},
        ])
        self.assertIn("Ledger drift (3)", lines[0])
        self.assertIn("from disk", lines[0])
        self.assertEqual(
            lines[1:4],
            [
                "  docs-site: ledger says present, disk says missing",
                "  gone: ledger says present, no longer registered",
                "  new-svc: ledger has no entry, disk says present",
            ],
        )
        self.assertEqual(lines[-1], "  run: wd discover")


class StatusCommandTest(unittest.TestCase):
    """The two output surfaces, driven through the real CLI."""

    def _workspace(self, tmp: str, names: tuple[str, ...]) -> Path:
        root = Path(tmp) / "root"
        for name in names:
            _write_child_graph(_init_repo(root / name), at_head=True)
        _seed_root(root, [ChildEntry(name=n, path=n) for n in names])
        return root

    def test_json_shape_is_additive_when_nothing_drifted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp, ("svc-a", "svc-b"))
            stored = json.loads(
                (root / ".weld" / "workspace-state.json").read_text(encoding="utf-8"),
            )

            payload = json.loads(
                _run_cli("workspace", "status", "--root", str(root), "--json"),
            )

            self.assertEqual(payload["version"], stored["version"])
            self.assertEqual(sorted(payload["children"]), sorted(stored["children"]))
            for name, entry in stored["children"].items():
                served = payload["children"][name]
                # Every stored key keeps its name and its value; ADR 0066's
                # derived 'freshness' is the only other addition.
                self.assertEqual({k: served[k] for k in entry}, entry)
                self.assertEqual(
                    set(served) - set(entry) - {"freshness"}, set(),
                )
            # Always present, so a machine consumer reads one key rather than
            # branching on its absence.
            self.assertEqual(payload["drift"], [])

    def test_json_reports_a_deleted_child_and_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp, ("svc-a", "docs-site"))
            state_path = root / ".weld" / "workspace-state.json"
            before = state_path.read_bytes()
            shutil.rmtree(root / "docs-site")

            payload = json.loads(
                _run_cli("workspace", "status", "--root", str(root), "--json"),
            )

            gone = payload["children"]["docs-site"]
            self.assertEqual(gone["status"], "missing")
            # The oracle is not invited to vouch for a child that is not
            # there: without the overlay it probed this path as present, took
            # the ADR 0017 non-git branch, and answered 'fresh'.
            self.assertNotIn("freshness", gone)
            self.assertEqual(
                payload["drift"],
                [{"name": "docs-site", "stored": "present", "observed": "missing"}],
            )
            self.assertEqual(payload["children"]["svc-a"]["status"], "present")
            # The recorded git identity went with the recorded status: it
            # described a checkout that is not there.
            self.assertIsNone(gone["head_sha"])
            self.assertIsNone(gone["head_ref"])
            self.assertFalse(gone["is_dirty"])
            # Reported, not repaired (ADR 0138 §4).
            self.assertEqual(state_path.read_bytes(), before)

    def test_a_missing_child_shows_no_branch_sha_or_dirty_flag(self) -> None:
        """The human line for a child that is gone claims nothing about it."""
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp, ("svc-a", "docs-site"))
            head = json.loads(
                (root / ".weld" / "workspace-state.json").read_text(encoding="utf-8"),
            )["children"]["docs-site"]["head_sha"]
            shutil.rmtree(root / "docs-site")

            text = _run_cli("workspace", "status", "--root", str(root))

            self.assertIn("docs-site: missing", text)
            self.assertNotIn(f"docs-site: missing dirty ({head[:12]}", text)
            line = next(
                ln for ln in text.splitlines() if ln.startswith("docs-site:")
            )
            self.assertNotIn(head[:12], line)
            self.assertNotIn("dirty", line)
            self.assertNotIn("refs/heads/", line)

    def test_human_output_is_silent_when_the_ledger_agrees(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp, ("svc-a",))
            text = _run_cli("workspace", "status", "--root", str(root))
            self.assertIn("present=1", text)
            self.assertNotIn("Ledger drift", text)
            self.assertNotIn("wd discover", text)

    def test_child_registered_since_the_last_discover(self) -> None:
        """The symmetric case: the ledger under-reports rather than over-reports."""
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp, ("svc-a",))
            state_path = root / ".weld" / "workspace-state.json"
            stored = json.loads(state_path.read_text(encoding="utf-8"))
            del stored["children"]["svc-a"]
            state_path.write_text(
                json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            )

            text = _run_cli("workspace", "status", "--root", str(root))

            self.assertIn("present=1", text)
            self.assertIn("svc-a: ledger has no entry, disk says present", text)
            self.assertIn("run: wd discover", text)

    def test_graph_drift_survives_the_status_overlay(self) -> None:
        """ADR 0066 tier 2 still has a baseline to compare against.

        The child's HEAD has not moved, so tier 1 is quiet; only the recorded
        digest can prove the graph changed. Replacing it with a live one
        during the overlay would compare the file against itself and report
        this child fresh.
        """
        with TemporaryDirectory() as tmp:
            root = self._workspace(tmp, ("svc-a",))
            graph = root / "svc-a" / ".weld" / "graph.json"
            payload = json.loads(graph.read_text(encoding="utf-8"))
            payload["meta"]["updated_at"] = "2026-01-01T00:00:00Z"
            graph.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            )

            text = _run_cli("workspace", "status", "--root", str(root))

            self.assertIn("stale=1", text)
            self.assertIn("svc-a: stale", text)
            # A drifted graph is not a lifecycle change, so nothing to report.
            self.assertNotIn("Ledger drift", text)


class FallbackTest(unittest.TestCase):
    """No registry, or an unprobeable one: report the ledger, do not crash."""

    def _ledger_only_root(self, tmp: str) -> Path:
        root = Path(tmp) / "root"
        (root / ".weld").mkdir(parents=True)
        (root / ".weld" / "workspace-state.json").write_text(
            json.dumps(_state(svc=_entry("present")), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return root

    def test_observed_children_declines_without_a_registry(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsNone(observed_children(self._ledger_only_root(tmp)))

    def test_status_falls_back_to_the_stored_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._ledger_only_root(tmp)
            text = _run_cli("workspace", "status", "--root", str(root))
            self.assertIn("present=1", text)
            self.assertNotIn("Ledger drift", text)

    def test_the_fallback_says_where_its_numbers_came_from(self) -> None:
        """Falling back is correct; falling back quietly is this ADR's own bug."""
        with TemporaryDirectory() as tmp:
            root = self._ledger_only_root(tmp)
            err = io.StringIO()
            with patch("sys.stderr", err):
                text = _run_cli("workspace", "status", "--root", str(root))
            self.assertIn("present=1", text)
            notice = err.getvalue()
            self.assertIn("stored ledger's last claim", notice)
            self.assertIn("wd discover", notice)
            # stdout stays a clean payload: the notice is on stderr.
            self.assertNotIn("notice:", text)

    def test_the_notice_never_reaches_the_json_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._ledger_only_root(tmp)
            err = io.StringIO()
            with patch("sys.stderr", err):
                raw = _run_cli(
                    "workspace", "status", "--root", str(root), "--json",
                )
            json.loads(raw)  # would raise if the notice preceded the payload
            self.assertIn("could not re-read", err.getvalue())

    def test_json_fallback_still_carries_the_drift_key(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._ledger_only_root(tmp)
            payload = json.loads(
                _run_cli("workspace", "status", "--root", str(root), "--json"),
            )
            self.assertEqual(payload["children"]["svc"]["status"], "present")
            self.assertEqual(payload["drift"], [])


if __name__ == "__main__":
    unittest.main()
