"""A strategy failure is retried, not exempted for good (bd hch4).

``files_with_no_nodes`` was the unconditional complement of the graph's
anchors, so the run that *failed* a file wrote the record saying its strategy
had *decided* nothing belongs there. The record is keyed on the path alone, so
a failure whose cause lies outside the file -- ``--safe`` refusing a
project-local strategy, an absent optional dependency, an ``external_json``
command that is not installed -- never re-armed: only a content change
re-dirties a file, and the content never changes. The file stayed absent from
the graph while every freshness signal read clean.

These are the reader-visible halves, over real ``discover()`` runs: that a
refused strategy repairs on the first pass that can run it, that a file the
strategy genuinely declines is still exempt (or the fix would trade a silent
hole for a permanent slow path), and that a recorded failure does not cost the
root its incremental basis.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._discover_state_check import mark_state_published, state_vouches_for_graph
from weld._graph_anchors import files_missing_from_graph, graph_files_with_nodes
from weld._graph_meta_sidecar import write_graph_with_meta
from weld.discover import discover
from weld.discovery_state import load_state

LOCAL_ONLY_CONFIG = """sources:
  - glob: "pkg/**/*.py"
    type: file
    strategy: house_style
"""

BUNDLED_CONFIG = """sources:
  - glob: "pkg/**/*.py"
    type: file
    strategy: python_module
"""

# A project-local strategy with no bundled counterpart: ``--safe`` refuses it
# outright (ADR 0024) and no fallback runs, which is the "no strategy spoke for
# these files" shape at source granularity.
LOCAL_STRATEGY = '''
from pathlib import Path

from weld.strategies._helpers import StrategyResult


def extract(root, source, context):
    nodes = {}
    for py in sorted(Path(root).glob("pkg/**/*.py")):
        rel = str(py.relative_to(root))
        nodes["file:" + rel] = {
            "type": "file",
            "label": py.stem,
            "props": {"file": rel, "source_strategy": "house_style"},
        }
    return StrategyResult(nodes, [], ["pkg/"])
'''


# bd pt38: two sources over one glob, the first of which removes a file the
# second is about to read. Ordered, so the vanish lands inside the run rather
# than between runs -- the shape a concurrent editor or CI checkout produces.
VANISHER_CONFIG = """sources:
  - glob: "pkg/**/*.py"
    type: file
    strategy: vanisher
  - glob: "pkg/**/*.py"
    type: file
    strategy: python_module
"""

VANISHER_STRATEGY = '''
from pathlib import Path

from weld.strategies._helpers import StrategyResult


def extract(root, source, context):
    """Remove one walked file mid-run and emit nothing of its own."""
    doomed = Path(root) / "pkg" / "doomed.py"
    if doomed.exists():
        doomed.unlink()
    return StrategyResult({}, [], [])
'''


class _RepoCase(unittest.TestCase):
    """A git-backed temp root with a ``.weld`` config, published like the CLI."""

    config = BUNDLED_CONFIG

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.graph = self.root / ".weld" / "graph.json"
        (self.root / ".weld" / "strategies").mkdir(parents=True)
        (self.root / ".weld" / "discover.yaml").write_text(
            self.config, encoding="utf-8",
        )
        (self.root / "pkg").mkdir()
        for cmd in (
            ["git", "init", "--quiet"],
            ["git", "config", "user.email", "t@test.com"],
            ["git", "config", "user.name", "T"],
            ["git", "config", "commit.gpgsign", "false"],
        ):
            self._git(cmd)

    def _git(self, cmd: list[str]) -> None:
        subprocess.run(
            cmd, cwd=str(self.root), capture_output=True, text=True,
            timeout=30, check=True, env={**os.environ, "LC_ALL": "C"},
        )

    def _write(self, rel: str, body: str) -> None:
        (self.root / rel).write_text(body, encoding="utf-8")

    def _commit(self, msg: str) -> None:
        self._git(["git", "add", "-A"])
        self._git(["git", "commit", "-m", msg, "--quiet"])

    def _publish(self, *, safe: bool = False) -> dict:
        """The real ``wd discover`` tail: build, land the body, stamp the state."""
        graph = discover(self.root, safe=safe, with_sqlite=False)
        write_graph_with_meta(self.graph, graph)
        mark_state_published(self.root, self.graph)
        return graph

    def _anchors(self) -> set[str]:
        raw = self.graph.read_text(encoding="utf-8")
        return graph_files_with_nodes(json.loads(raw))

    def _state(self):
        state = load_state(self.root)
        assert state is not None
        return state


class RefusedStrategyRepairsTest(_RepoCase):
    config = LOCAL_ONLY_CONFIG

    def setUp(self) -> None:
        super().setUp()
        (self.root / ".weld" / "strategies" / "house_style.py").write_text(
            LOCAL_STRATEGY, encoding="utf-8",
        )
        self._write("pkg/mod.py", "def f():\n    return 1\n")
        self._commit("initial")

    def test_refused_strategy_repairs_on_the_first_pass_that_can_run_it(self) -> None:
        self._publish(safe=True)

        state = self._state()
        self.assertIn("pkg/mod.py", state.files)
        self.assertNotIn(
            "pkg/mod.py", state.files_with_no_nodes,
            "a strategy that was refused decided nothing about this file",
        )
        self.assertEqual({"pkg/mod.py"}, state.files_with_failed_strategy)
        self.assertNotIn("pkg/mod.py", self._anchors())

        # Nothing on disk changed -- only the run's capability did. The repair
        # must fire off the recorded failure, not off a content hash.
        self._publish()
        self.assertIn("pkg/mod.py", self._anchors())
        self.assertEqual(set(), self._state().files_with_failed_strategy)

    def test_the_failure_is_re_derived_rather_than_spent_on_one_pass(self) -> None:
        """Two refusals in a row must leave the third pass able to repair."""
        self._publish(safe=True)
        self._publish(safe=True)

        self.assertEqual(
            {"pkg/mod.py"}, self._state().files_with_failed_strategy,
            "the second refusal must re-report what the first one did",
        )
        self.assertNotIn("pkg/mod.py", self._anchors())

        self._publish()
        self.assertIn("pkg/mod.py", self._anchors())

    def test_a_recorded_failure_still_leaves_an_incremental_basis(self) -> None:
        """A hole the inventory names is not an inventory that misdescribes.

        Failing the vouching audit here would cost every permanently degraded
        environment -- no optional dependency, a habitual ``--safe`` -- a full
        re-discovery on every single run, forever.
        """
        self._publish(safe=True)
        self.assertTrue(state_vouches_for_graph(self._state(), self.graph))


class DeclinedFileStaysExemptTest(_RepoCase):
    """The control: a decision is still a decision, and still exempts."""

    def setUp(self) -> None:
        super().setUp()
        self._write("pkg/__init__.py", "")
        self._write("pkg/mod.py", "def f():\n    return 1\n")
        self._commit("initial")

    def test_empty_init_is_recorded_as_intent_and_never_re_runs(self) -> None:
        self._publish()

        state = self._state()
        self.assertIn("pkg/__init__.py", state.files_with_no_nodes)
        self.assertEqual(set(), state.files_with_failed_strategy)
        self.assertNotIn("pkg/__init__.py", self._anchors())

        # Exempt means exempt: the per-file repair must not schedule it, or
        # this root would re-run its strategies on every refresh forever.
        self.assertEqual(
            set(),
            files_missing_from_graph(
                state, set(state.files), self._anchors(),
            ),
        )


class UnparseableFileTest(_RepoCase):
    """``python_module`` swallowing a ``SyntaxError`` is a failure, not a decision."""

    def setUp(self) -> None:
        super().setUp()
        self._write("pkg/ok.py", "def ok():\n    return 1\n")
        self._write("pkg/broken.py", "def broken(:\n")
        self._commit("initial")

    def test_unparseable_file_stays_in_the_repair_queue(self) -> None:
        self._publish()

        state = self._state()
        self.assertEqual({"pkg/broken.py"}, state.files_with_failed_strategy)
        self.assertNotIn("pkg/broken.py", state.files_with_no_nodes)
        self.assertIn("pkg/ok.py", self._anchors())
        self.assertNotIn("pkg/broken.py", self._anchors())

        # Armed: the next pass re-reads it, so a parser that later accepts the
        # file repairs it without anyone touching the bytes.
        self.assertEqual(
            {"pkg/broken.py"},
            files_missing_from_graph(state, set(state.files), self._anchors()),
        )

    def test_fixing_the_syntax_repairs_the_file(self) -> None:
        self._publish()
        self._write("pkg/broken.py", "def fixed():\n    return 2\n")
        self._commit("fix syntax")
        self._publish()

        self.assertIn("pkg/broken.py", self._anchors())
        self.assertEqual(set(), self._state().files_with_failed_strategy)


class VanishedFileTest(_RepoCase):
    """A file removed mid-run is survived and named (bd pt38)."""

    config = VANISHER_CONFIG

    def setUp(self) -> None:
        super().setUp()
        (self.root / ".weld" / "strategies" / "vanisher.py").write_text(
            VANISHER_STRATEGY, encoding="utf-8",
        )
        self._write("pkg/keeper.py", "def keeper():\n    return 1\n")
        self._write("pkg/doomed.py", "def doomed():\n    return 2\n")
        self._commit("initial")

    def test_discovery_completes_and_records_the_vanished_file(self) -> None:
        """``read_text`` raises ``OSError``, which ``SyntaxError`` never caught.

        The run walks the glob once at the start (bd cjij memo) and reads
        later, so ``python_module`` is handed a path that no longer exists and
        used to propagate ``FileNotFoundError`` out of ``discover`` entirely.
        Reaching the assertions at all is the primary claim; that the keeper
        is still anchored proves the run finished its work rather than merely
        failing quietly.
        """
        self._publish()

        state = self._state()
        self.assertIn("pkg/keeper.py", self._anchors())
        self.assertNotIn("pkg/doomed.py", self._anchors())

        # A failure, not a decision: exempt from the vouching audit so the
        # root keeps its incremental basis, but still armed for the ADR 0008
        # per-file repair if the file comes back.
        self.assertIn("pkg/doomed.py", state.files_with_failed_strategy)
        self.assertNotIn("pkg/doomed.py", state.files_with_no_nodes)
        self.assertTrue(state_vouches_for_graph(state, self.graph))

    def test_restoring_the_file_repairs_it(self) -> None:
        """The repairable half: the record must not outlive the failure."""
        self._publish()

        (self.root / ".weld" / "strategies" / "vanisher.py").write_text(
            "from weld.strategies._helpers import StrategyResult\n\n\n"
            "def extract(root, source, context):\n"
            "    return StrategyResult({}, [], [])\n",
            encoding="utf-8",
        )
        self._write("pkg/doomed.py", "def doomed():\n    return 2\n")
        self._commit("restore")
        self._publish()

        self.assertIn("pkg/doomed.py", self._anchors())
        self.assertEqual(set(), self._state().files_with_failed_strategy)


if __name__ == "__main__":
    unittest.main()
