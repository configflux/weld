"""A command-only source's failure is retried, not silently absent (bd um00).

hch4 records a strategy failure by repo-relative path
(``files_with_failed_strategy``), which keeps the ADR 0008 per-file repair
armed until a later, more capable pass repairs the file. A source entry with
no ``glob``/``path``/``files`` key -- a command-only ``external_json``
adapter is the shipped example -- resolves no files at all, so that channel
never fires for it: the fragment is simply absent from the graph, with only a
per-run stderr warning as a trace, until the config itself changes.

Confirmed empirically before this fix (see the bd um00 mini-spec comment):
such a source runs exactly once, on the first full discovery, and never again
on any subsequent ``wd discover`` while the incremental basis holds --
success or failure alike. Recording the failure alone would therefore not be
enough; ``sources_needing_retry`` forces the entry to run again on the next
incremental pass while a failure is outstanding, the source-entry analogue of
how a recorded file failure rides ``files_missing_from_graph`` to the same
effect.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from weld._discover_basis import entry_fingerprint
from weld._discover_state_check import mark_state_published, state_vouches_for_graph
from weld._graph_meta_sidecar import write_graph_with_meta
from weld.discover import discover
from weld.discovery_state import load_state
from weld.doctor import doctor

FAILING_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env python3
    import sys
    print("boom", file=sys.stderr)
    sys.exit(1)
""")

SUCCEEDING_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys
    json.dump({
        "nodes": {"tool:custom-lint": {"type": "tool", "label": "Custom Lint",
                                        "props": {"source_strategy": "external_json"}}},
        "edges": [], "discovered_from": ["lint-config.json"]
    }, sys.stdout)
""")

BROKEN_PYTHON_MODULE_CONFIG = """sources:
  - glob: "pkg/**/*.py"
    type: file
    strategy: python_module
  - strategy: external_json
    command: "{command}"
"""


class _RepoCase(unittest.TestCase):
    """A git-backed temp root with a ``.weld`` config, published like the CLI."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.graph = self.root / ".weld" / "graph.json"
        (self.root / ".weld").mkdir(parents=True)
        self.script = self.root / "adapter.py"
        self._write_script(FAILING_SCRIPT)
        (self.root / ".weld" / "discover.yaml").write_text(
            self._config(), encoding="utf-8",
        )
        for cmd in (
            ["git", "init", "--quiet"],
            ["git", "config", "user.email", "t@test.com"],
            ["git", "config", "user.name", "T"],
            ["git", "config", "commit.gpgsign", "false"],
        ):
            self._git(cmd)
        self._commit("initial")

    def _config(self) -> str:
        return (
            "sources:\n"
            "  - strategy: external_json\n"
            f'    command: "{self.script}"\n'
        )

    def _git(self, cmd: list[str]) -> None:
        subprocess.run(
            cmd, cwd=str(self.root), capture_output=True, text=True,
            timeout=30, check=True, env={**os.environ, "LC_ALL": "C"},
        )

    def _write_script(self, body: str) -> None:
        self.script.write_text(body, encoding="utf-8")
        self.script.chmod(self.script.stat().st_mode | stat.S_IEXEC)

    def _commit(self, msg: str) -> None:
        self._git(["git", "add", "-A"])
        self._git(["git", "commit", "-m", msg, "--quiet"])

    def _publish(self) -> dict:
        """The real ``wd discover`` tail: build, land the body, stamp the state."""
        graph = discover(self.root, with_sqlite=False)
        write_graph_with_meta(self.graph, graph)
        mark_state_published(self.root, self.graph)
        return graph

    def _state(self):
        state = load_state(self.root)
        assert state is not None
        return state

    def _entry_id(self, **override) -> str:
        source = {"strategy": "external_json", "command": str(self.script), **override}
        return entry_fingerprint(source)


class CommandOnlySourceRepairsTest(_RepoCase):
    def test_failure_is_recorded_entry_keyed_with_kind_and_reason(self) -> None:
        graph = self._publish()

        state = self._state()
        self.assertEqual(set(), state.files_with_failed_strategy)
        self.assertEqual({self._entry_id()}, set(state.sources_with_failed_strategy))
        record = state.sources_with_failed_strategy[self._entry_id()]
        self.assertEqual("nonzero_exit", record["kind"])
        self.assertIn("boom", record["reason"])
        self.assertNotIn("tool:custom-lint", graph.get("nodes", {}))

    def test_doctor_reports_the_failure_actionably(self) -> None:
        self._publish()

        results = doctor(self.root)
        warnings = [
            r for r in results
            if r.level == "warn" and "source entry failed" in r.message
        ]
        self.assertTrue(warnings, [r.message for r in results])
        self.assertIn("nonzero_exit", warnings[0].message)
        self.assertIn("boom", warnings[0].message)

    def test_nothing_tracked_changes_yet_the_next_discover_retries_the_command(
        self,
    ) -> None:
        """The re-arm proof: no glob covers ``adapter.py``, so only the
        command's own behaviour differs between the two publishes -- proving
        the retry is driven by the recorded failure, not by file content."""
        self._publish()
        self.assertEqual({self._entry_id()}, set(self._state().sources_with_failed_strategy))

        self._write_script(SUCCEEDING_SCRIPT)
        graph = self._publish()

        self.assertIn("tool:custom-lint", graph.get("nodes", {}))
        self.assertEqual({}, self._state().sources_with_failed_strategy)

    def test_discovered_from_survives_the_retry(self) -> None:
        """bd 8084: a footprint-less source's ``discovered_from`` must land
        in ``meta.discovered_from`` on the SAME incremental pass its nodes
        land in, not only on a later ``--full``.

        Before the fix, the incremental path re-derived ``discovered_from``
        from ``source_file_map`` -- ``[]`` for this entry, structurally,
        forever, since it has no ``glob``/``path``/``files`` key -- instead
        of collecting what the retried strategy actually reported. Nodes
        landed correctly (bd um00's fix); the provenance entry did not."""
        self._publish()

        (self.root / "lint-config.json").write_text("{}", encoding="utf-8")
        self._write_script(SUCCEEDING_SCRIPT)
        graph = self._publish()

        self.assertIn("tool:custom-lint", graph.get("nodes", {}))
        self.assertIn(
            "lint-config.json", graph.get("meta", {}).get("discovered_from", []),
            "a footprint-less source's discovered_from must survive an "
            "incremental retry, not only a full discovery",
        )

    def test_the_failure_is_re_derived_rather_than_spent_on_one_pass(self) -> None:
        """Two failures in a row must leave the third pass able to repair,
        mirroring the file-keyed sibling's re-derivation guarantee."""
        self._publish()
        self._publish()

        self.assertEqual(
            {self._entry_id()}, set(self._state().sources_with_failed_strategy),
            "the second failure must re-report what the first one did",
        )

        self._write_script(SUCCEEDING_SCRIPT)
        graph = self._publish()
        self.assertIn("tool:custom-lint", graph.get("nodes", {}))

    def test_a_recorded_failure_still_leaves_an_incremental_basis(self) -> None:
        """A hole the inventory cannot even see is not a hole the vouching
        audit is asked about -- entry-level failures never touch it."""
        self._publish()
        self.assertTrue(state_vouches_for_graph(self._state(), self.graph))


class FootprintedFailureUnaffectedTest(_RepoCase):
    """Control: a source WITH a file footprint keeps using the file-keyed
    channel only -- no double bookkeeping into the entry-keyed one."""

    def _config(self) -> str:
        return BROKEN_PYTHON_MODULE_CONFIG.format(command=self.script)

    def setUp(self) -> None:
        super().setUp()
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
        self._write_script(SUCCEEDING_SCRIPT)
        self._commit("add broken python + working adapter")

    def test_file_shaped_failure_stays_file_keyed(self) -> None:
        self._publish()

        state = self._state()
        self.assertEqual({"pkg/broken.py"}, state.files_with_failed_strategy)
        self.assertEqual(
            {}, state.sources_with_failed_strategy,
            "a footprinted source's failure must not also land in the "
            "entry-keyed channel",
        )


if __name__ == "__main__":
    unittest.main()
