"""A file that vanishes between the walk and the read (bd pt38).

``python_module`` guarded its read with ``except SyntaxError`` alone, but
``read_text`` raises ``OSError`` -- never ``SyntaxError`` -- so a source file
removed after the glob walk and before the read propagated out of
``extract`` and took the whole discovery run down with it.

The window is never zero and is not hypothetical. A run walks once and reads
later (``build_file_hashes`` at run start, strategies after), and the per-run
glob memo (bd cjij) widens it to the length of the entire run: every strategy
re-resolving a glob is served the listing observed when the run began. A
concurrent editor, a worktree switch, or a CI checkout landing mid-run is
enough. These tests reproduce it through that memo rather than through a
race, so the window is exact and the test is deterministic.

``_python_anchor`` is here for the same reason and in the same breath: its
``path_yields_file_anchor`` predicate reads the same files for
``python_package``, over the same glob, in the same run. Guarding only
``python_module`` would have moved the crash one strategy over rather than
fixing it.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.glob_match import glob_scope
from weld.strategies._python_anchor import path_yields_file_anchor
from weld.strategies._strategy_failure import drain_strategy_failures
from weld.strategies._glob_resolve import resolve_glob_with_provenance
from weld.strategies.python_module import extract

_GLOB = "pkg/**/*.py"
_SOURCE = {"glob": _GLOB, "type": "file"}


class _VanishCase(unittest.TestCase):
    """A git-backed tree whose files can be removed after the walk."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "pkg").mkdir()
        self._write("pkg/keeper.py", "def keeper():\n    return 1\n")
        self._write("pkg/doomed.py", "def doomed():\n    return 2\n")
        for cmd in (
            ["git", "init", "--quiet"],
            ["git", "config", "user.email", "t@test.com"],
            ["git", "config", "user.name", "T"],
            ["git", "config", "commit.gpgsign", "false"],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "initial", "--quiet"],
        ):
            subprocess.run(
                cmd, cwd=str(self.root), check=True, capture_output=True,
                text=True, timeout=30,
            )

    def _write(self, rel: str, body: str) -> None:
        (self.root / rel).write_text(body, encoding="utf-8")

    def _extract_after_removing(self, *rels: str) -> tuple[dict, dict]:
        """Walk the glob, remove *rels*, then extract off the memoized walk.

        This is the production window exactly: ``discover`` is decorated with
        ``@glob_scope()`` (bd cjij), so the resolver ``extract`` itself calls is
        answered from the listing taken when the run began -- naming files
        that need not still exist. Priming the memo through that same shared
        resolver rather than ``walk_glob`` keeps the memo key identical by
        construction (ADR 0112).
        """
        context: dict = {}
        with glob_scope():
            matched, _ = resolve_glob_with_provenance(self.root, _GLOB, [])
            self.assertTrue(matched, "fixture walked to nothing")
            for rel in rels:
                (self.root / rel).unlink()
            result = extract(self.root, _SOURCE, context)
        return result.nodes, context

    @staticmethod
    def _files(nodes: dict) -> set[str]:
        return {n["props"]["file"] for n in nodes.values()}


class VanishedFileTest(_VanishCase):
    """The reader-visible half: the run survives and says what it lost."""

    def test_vanished_file_does_not_abort_the_glob(self) -> None:
        """The whole point: one missing file is not a failed run.

        Asserting the *keeper* anchor is what makes this a regression test.
        Before the fix the ``FileNotFoundError`` escaped ``extract``, so
        there were no nodes at all to inspect -- and a bare "doomed is
        absent" assertion would have passed on a strategy that crashed.
        """
        nodes, _ = self._extract_after_removing("pkg/doomed.py")

        self.assertIn("pkg/keeper.py", self._files(nodes))
        self.assertNotIn("pkg/doomed.py", self._files(nodes))

    def test_vanished_file_is_recorded_as_a_repairable_failure(self) -> None:
        """A file we could not read is a failure, not a decision (bd hch4).

        Recorded as a *decision* it would be exempted from the ADR 0008
        per-file repair for good: the exemption keys on the path alone and
        only a content change re-dirties a file, so a file that came back
        unchanged would never be re-read.
        """
        _, context = self._extract_after_removing("pkg/doomed.py")

        self.assertEqual({"pkg/doomed.py"}, drain_strategy_failures(context))

    def test_every_file_vanishing_is_still_not_a_crash(self) -> None:
        """The degenerate case: a whole directory removed mid-run."""
        nodes, context = self._extract_after_removing(
            "pkg/keeper.py", "pkg/doomed.py",
        )

        self.assertEqual(set(), self._files(nodes))
        self.assertEqual(
            {"pkg/doomed.py", "pkg/keeper.py"}, drain_strategy_failures(context),
        )

    def test_undecodable_bytes_are_a_failure_not_a_crash(self) -> None:
        """``UnicodeDecodeError`` is not an ``OSError`` and needs its own arm.

        It is a ``ValueError``, so widening to ``OSError`` alone would still
        abort the run on a ``.py`` path holding non-UTF-8 bytes. This is the
        arm ``python_callgraph`` has always carried.
        """
        (self.root / "pkg" / "binary.py").write_bytes(b"\xff\xfe\x00def f():\n")

        context: dict = {}
        with glob_scope():
            nodes = extract(self.root, _SOURCE, context).nodes

        self.assertIn("pkg/keeper.py", self._files(nodes))
        self.assertNotIn("pkg/binary.py", self._files(nodes))
        self.assertEqual({"pkg/binary.py"}, drain_strategy_failures(context))


class AnchorPredicateTest(_VanishCase):
    """``python_package`` asks about the same files, over the same glob."""

    def test_predicate_is_false_for_a_vanished_file(self) -> None:
        """A file that is gone anchors nothing -- and must not raise.

        ``python_package`` reaches this predicate for a directory whose only
        members are ``__init__.py`` files, so a vanished ``__init__.py`` used
        to abort the run here even once ``python_module`` was guarded.
        """
        doomed = self.root / "pkg" / "doomed.py"
        doomed.unlink()

        self.assertFalse(path_yields_file_anchor(doomed))

    def test_predicate_is_false_for_undecodable_bytes(self) -> None:
        binary = self.root / "pkg" / "binary.py"
        binary.write_bytes(b"\xff\xfe\x00def f():\n")

        self.assertFalse(path_yields_file_anchor(binary))

    def test_predicate_still_answers_for_a_readable_file(self) -> None:
        """The guard must not have swallowed the real answer."""
        self.assertTrue(path_yields_file_anchor(self.root / "pkg" / "keeper.py"))


if __name__ == "__main__":
    unittest.main()
