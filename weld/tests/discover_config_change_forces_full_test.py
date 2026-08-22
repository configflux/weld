"""Regression: a ``.weld/discover.yaml`` change must invalidate the
incremental basis (bd 4fpj).

Symptom that motivated this. Add a source entry over files that did not
themselves change -- the ``tools/*.py`` python_callgraph entry, say, where
``tools/*.py`` already carried python_module nodes. Then run ``wd discover``.
The entry does not run: its nodes and edges are simply absent, and the graph
reports itself fresh the whole time. ``wd discover --full`` produces them.

Two distinct skips, one cause. ``discovery-state.json``'s ``files`` inventory
records which files were seen, never which strategies were pointed at them, so
a config edit is invisible to a delta computed from file content:

* no file changed at all -- ``state_diff.has_changes`` is False, so the run
  takes the no-change fast path and never reaches the per-entry loop;
* some unrelated file is dirty -- the per-entry filter
  ``set(source_file_map[i]).intersection(dirty)`` finds the new entry's files
  clean and skips it.

Neither is caught by ``files_missing_strategy_outputs``: it asks whether a
source's files have *any* node, and files covered by a pre-existing entry
already do.

The fix fingerprints the parsed config into the state and refuses the
inventory as an incremental basis when the fingerprint does not match (ADR
0008 section 7, fifth fallback). These cases pin the four behaviours that
makes: the changed config re-runs, the *unchanged* config still takes the fast
path, a cosmetic edit is not a change, and a state predating the field heals
in exactly one run.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from weld import discover as discover_mod
from weld._discover_basis import config_fingerprint
from weld.discover import _discover_single_repo
from weld._yaml import parse_yaml

_ENTRY_MODULE = (
    "sources:\n"
    "  - glob: src/*.py\n"
    "    type: file\n"
    "    strategy: python_module\n"
)

_ENTRY_MODULE_AND_CALLGRAPH = _ENTRY_MODULE + (
    "  - glob: src/*.py\n"
    "    type: symbol\n"
    "    strategy: python_callgraph\n"
)

#: Same mapping as ``_ENTRY_MODULE``, written differently: a comment, a blank
#: line, and the two keys of the entry in the other order. Parses to an equal
#: dict, so it must not read as a config change.
_ENTRY_MODULE_COSMETIC = (
    "# curated for the fixture -- comments must not force a re-discovery\n"
    "\n"
    "sources:\n"
    "  - strategy: python_module\n"
    "    glob: src/*.py\n"
    "    type: file\n"
)


def _write_config(root: Path, body: str) -> None:
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(body, encoding="utf-8")


def _build_fixture(root: Path) -> None:
    """One Python file with a call in it, under a single-entry config.

    ``python_module`` anchors ``src/mod.py`` with a ``file:`` node, which is
    what makes the bug reachable: the file already has a node, so the
    source-level audit is satisfied and a later entry added over the same glob
    is skipped rather than repaired.
    """
    src = root / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "def helper():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def caller():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    _write_config(root, _ENTRY_MODULE)


def _run(root: Path, *, incremental: bool | None) -> dict:
    """Discover at *root*, landing ``graph.json`` and its state together.

    ``write_graph=True`` is the auto-refresh shape: the finalizer writes the
    canonical graph and stamps the inventory for it in one call, so the
    fixture never has to hand-stamp state the way a ``--output`` caller does.
    """
    return _discover_single_repo(
        root, incremental=incremental, with_sqlite=False, write_graph=True,
    )


def _symbol_ids(graph: dict) -> set[str]:
    return {nid for nid in graph.get("nodes", {}) if nid.startswith("symbol:")}


def _state(root: Path) -> dict:
    return json.loads(
        (root / ".weld" / "discovery-state.json").read_text(encoding="utf-8")
    )


class ConfigChangeInvalidatesIncrementalBasisTest(unittest.TestCase):
    """Pin "a config change is not a no-op" and its three boundaries."""

    def test_added_source_entry_runs_on_the_next_incremental_discover(
        self,
    ) -> None:
        """The reported bug, end to end.

        Seed under a one-entry config, add a second entry over the *same*
        glob, touch no source file, then run incremental. Before the fix the
        second entry never ran and the graph kept zero symbol nodes.
        """
        with tempfile.TemporaryDirectory(prefix="cfg-change-add-") as td:
            root = Path(td)
            _build_fixture(root)

            seeded = _run(root, incremental=None)
            self.assertEqual(
                _symbol_ids(seeded), set(),
                "fixture invariant: the one-entry config must not emit "
                "symbol nodes, or the case proves nothing",
            )

            _write_config(root, _ENTRY_MODULE_AND_CALLGRAPH)
            incremental = _run(root, incremental=True)

            self.assertTrue(
                _symbol_ids(incremental),
                "a source entry added to discover.yaml must run on the next "
                "incremental discover; skipping it makes a config change "
                "silently a no-op while the graph reports itself fresh",
            )

            # The whole point of falling back to a full run rather than
            # marking the new entry's files dirty: the two results must not
            # merely both be non-empty, they must agree.
            full = _run(root, incremental=False)
            self.assertEqual(
                set(incremental.get("nodes", {})), set(full.get("nodes", {})),
                "incremental-after-config-change must produce the same node "
                "set as a full run at the same tree and config",
            )

    def test_removed_source_entry_drops_its_nodes(self) -> None:
        """The other half of "config changed" -- and why per-entry
        dirty-marking would not have been enough.

        Removing an entry leaves nodes behind that no entry now claims. There
        is no file to mark dirty for them: the entry and its file list are
        gone from the config the run can see. Only re-deriving the graph from
        the current config drops them.
        """
        with tempfile.TemporaryDirectory(prefix="cfg-change-drop-") as td:
            root = Path(td)
            _build_fixture(root)
            _write_config(root, _ENTRY_MODULE_AND_CALLGRAPH)

            seeded = _run(root, incremental=None)
            self.assertTrue(
                _symbol_ids(seeded),
                "fixture invariant: the two-entry config must emit symbols",
            )

            _write_config(root, _ENTRY_MODULE)
            after = _run(root, incremental=True)

            self.assertEqual(
                _symbol_ids(after), set(),
                "nodes from a removed source entry must not survive an "
                "incremental run; nothing else in the pipeline can find them",
            )

    def test_unchanged_config_keeps_the_no_change_fast_path(self) -> None:
        """Guard the hot path: the fingerprint must cost nothing when it
        matches.

        A check that forced a full run on every invocation would "fix" the
        bug and destroy incremental discovery. Spying on ``_run_source`` is
        the same instrument the no-op case in
        ``discover_incremental_missing_outputs_test`` uses: zero calls means
        no strategy was invoked at all.
        """
        with tempfile.TemporaryDirectory(prefix="cfg-change-noop-") as td:
            root = Path(td)
            _build_fixture(root)
            _run(root, incremental=None)

            with mock.patch.object(
                discover_mod, "_run_source", wraps=discover_mod._run_source,
            ) as spy:
                _run(root, incremental=True)

            self.assertEqual(
                spy.call_count, 0,
                "no-change fast path regressed: an unchanged discover.yaml "
                "must not invalidate the incremental basis",
            )

    def test_cosmetic_config_edit_is_not_a_config_change(self) -> None:
        """A comment, a blank line, or a reordered key is not a change.

        The fingerprint is taken over the *parsed* mapping precisely so that
        editing prose in discover.yaml does not cost a full re-discovery --
        a signal that fires on formatting teaches its reader to ignore it.
        """
        with tempfile.TemporaryDirectory(prefix="cfg-change-cosmetic-") as td:
            root = Path(td)
            _build_fixture(root)
            _run(root, incremental=None)

            # State the invariant directly at the fingerprint, so a failure
            # names the canonicalization rather than the run that used it.
            self.assertEqual(
                config_fingerprint(parse_yaml(_ENTRY_MODULE)),
                config_fingerprint(parse_yaml(_ENTRY_MODULE_COSMETIC)),
                "fingerprint must be taken over the parsed mapping, not the "
                "file bytes",
            )

            _write_config(root, _ENTRY_MODULE_COSMETIC)
            with mock.patch.object(
                discover_mod, "_run_source", wraps=discover_mod._run_source,
            ) as spy:
                _run(root, incremental=True)

            self.assertEqual(
                spy.call_count, 0,
                "a cosmetic discover.yaml edit must not force a full run",
            )

    def test_state_without_a_fingerprint_forces_one_full_run_then_resumes(
        self,
    ) -> None:
        """Upgrade path: a state written before this field heals in one run.

        An absent fingerprint names no config, so it vouches for none and the
        run falls back -- the same reading ``published_graph`` gets, and the
        reason this is not a ``STATE_VERSION`` bump (which would also discard
        ``files_with_no_nodes``). The second half is what makes it a heal
        rather than a permanent penalty: the fallback re-stamps the state, so
        the run after it is incremental again.
        """
        with tempfile.TemporaryDirectory(prefix="cfg-change-upgrade-") as td:
            root = Path(td)
            _build_fixture(root)
            _run(root, incremental=None)

            state_path = root / ".weld" / "discovery-state.json"
            state = _state(root)
            self.assertIsInstance(
                state.get("config_fingerprint"), str,
                "a run must record the config it ran under",
            )
            state.pop("config_fingerprint")
            state_path.write_text(json.dumps(state), encoding="utf-8")

            err = io.StringIO()
            with redirect_stderr(err):
                with mock.patch.object(
                    discover_mod, "_run_source", wraps=discover_mod._run_source,
                ) as first:
                    _run(root, incremental=True)

            self.assertGreater(
                first.call_count, 0,
                "a state recording no config fingerprint must not be trusted "
                "as an incremental basis",
            )
            self.assertIn(
                "records no config fingerprint", err.getvalue(),
                "the notice must name the real reason -- reporting an "
                "upgrade as a discover.yaml edit the user did not make "
                "sends them looking for a change that is not there",
            )

            with mock.patch.object(
                discover_mod, "_run_source", wraps=discover_mod._run_source,
            ) as second:
                _run(root, incremental=True)

            self.assertEqual(
                second.call_count, 0,
                "the fallback must re-stamp the fingerprint, or every run "
                "after an upgrade pays for a full discovery forever",
            )


if __name__ == "__main__":
    unittest.main()
