"""``wd init`` fails only when it could not deliver what was asked for.

``wd init`` writes two independent things -- ``.weld/discover.yaml`` and the
managed git policy under ``.weld/`` -- and both are write-once. The exit code
used to answer about the config alone, which broke the *documented* Mode A ->
Mode B upgrade path: ``wd init --track-graphs`` on an initialised repository
wrote the ``.gitattributes``, registered the merge driver, announced both on
stderr, and then exited 1 because ``discover.yaml`` already existed. Under
``set -e`` that aborts the setup script that just succeeded (bd ilax).

Fixing only the exit code would have made a second, quieter bug louder-looking
without fixing it: because the ignore writer is *also* skip-if-exists,
``wd init --track-graphs`` in a repository whose config-only ignore file
survives writes a ``.gitattributes`` declaring a merge policy for artifacts the
ignore file still hides, and says nothing at all. Its exit 1 was right for the
wrong reason, and would have become exit 0 for a broken repository.

So both halves are pinned here: the request that succeeded must exit 0, and
the request that silently did not happen must exit 1 saying which file blocked
it. ``docs/graph-tracking-policy.md`` "Switching" is the procedure both
messages point at.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._gitignore_writer import (
    CONFIG_ONLY_GITIGNORE,
    IGNORE_ALL_GITIGNORE,
    TRACK_GRAPHS_GITIGNORE,
    ignore_expresses_mode,
)
from weld.init import main as init_main


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(root),
        capture_output=True,
        check=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
    )
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")


def _run_init(*argv: str) -> tuple[int, str]:
    """``wd init`` with *argv*: its exit status and everything it said.

    ``main`` exits through ``SystemExit`` rather than returning a code, and a
    clean return is exit 0 -- the distinction this whole test file is about,
    so it is resolved here once.
    """
    stderr = io.StringIO()
    code = 0
    with contextlib.redirect_stderr(stderr):
        try:
            init_main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, stderr.getvalue()


class InitExitStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        _init_repo(self.root)

    def _ignore(self) -> Path:
        return self.root / ".weld" / ".gitignore"

    def test_first_track_graphs_init_succeeds(self) -> None:
        code, _ = _run_init(str(self.root), "--track-graphs")
        self.assertEqual(code, 0)

    def test_re_running_track_graphs_succeeds_and_says_why(self) -> None:
        """The reported bug: everything asked for landed, yet exit 1."""
        _run_init(str(self.root), "--track-graphs")
        code, err = _run_init(str(self.root), "--track-graphs")
        self.assertEqual(
            code, 0,
            "re-running the documented upgrade path must not report failure "
            f"when the policy it was asked for is in effect. stderr:\n{err}",
        )
        self.assertIn("Left discover.yaml as it is", err)

    def test_bare_re_init_still_fails(self) -> None:
        """Unchanged: the config was the whole request, and it was declined."""
        _run_init(str(self.root))
        code, err = _run_init(str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_track_graphs_over_a_surviving_mode_a_ignore_fails_loudly(self) -> None:
        """The silent half: the mode did not happen, so the run must not pass."""
        _run_init(str(self.root))
        self.assertEqual(self._ignore().read_text(encoding="utf-8"),
                         CONFIG_ONLY_GITIGNORE)
        code, err = _run_init(str(self.root), "--track-graphs")
        self.assertEqual(code, 1)
        self.assertIn(str(self._ignore()), err)
        self.assertIn("--track-graphs", err)
        self.assertIn("graph-tracking-policy.md", err)
        # And the diagnostic must be about the ignore file, not the config --
        # naming the wrong obstacle is what sent people to `--force`.
        self.assertIn("still ignores the artifacts", err)

    def test_ignore_all_over_a_surviving_ignore_fails_loudly(self) -> None:
        _run_init(str(self.root))
        code, err = _run_init(str(self.root), "--ignore-all")
        self.assertEqual(code, 1)
        self.assertIn("--ignore-all", err)

    def test_documented_upgrade_path_succeeds(self) -> None:
        """``rm .weld/.gitignore && wd init --track-graphs``, end to end."""
        _run_init(str(self.root))
        self._ignore().unlink()
        code, _ = _run_init(str(self.root), "--track-graphs")
        self.assertEqual(code, 0)
        self.assertEqual(self._ignore().read_text(encoding="utf-8"),
                         TRACK_GRAPHS_GITIGNORE)
        self.assertTrue((self.root / ".weld" / ".gitattributes").is_file())


class IgnoreExpressesModeTest(unittest.TestCase):
    """The predicate the exit status rests on, judged on behaviour."""

    def test_managed_files_express_their_own_modes(self) -> None:
        self.assertTrue(ignore_expresses_mode(CONFIG_ONLY_GITIGNORE))
        self.assertTrue(
            ignore_expresses_mode(TRACK_GRAPHS_GITIGNORE, track_graphs=True))
        self.assertTrue(
            ignore_expresses_mode(IGNORE_ALL_GITIGNORE, ignore_all=True))

    def test_a_mode_a_file_is_not_mode_b(self) -> None:
        self.assertFalse(
            ignore_expresses_mode(CONFIG_ONLY_GITIGNORE, track_graphs=True))
        self.assertFalse(
            ignore_expresses_mode(CONFIG_ONLY_GITIGNORE, ignore_all=True))

    def test_a_blanket_ignore_is_neither_of_the_others(self) -> None:
        self.assertFalse(
            ignore_expresses_mode(IGNORE_ALL_GITIGNORE, track_graphs=True))
        self.assertFalse(ignore_expresses_mode(IGNORE_ALL_GITIGNORE))

    def test_a_customised_file_is_judged_on_what_it_does(self) -> None:
        """The managed headers invite editing, so equality is the wrong test."""
        custom_mode_b = "# ours\ngraph-meta.json\n*.lock\n"
        self.assertTrue(ignore_expresses_mode(custom_mode_b, track_graphs=True))
        custom_mode_a = "# ours\ngraph.json\nagent-graph.json\n"
        self.assertTrue(ignore_expresses_mode(custom_mode_a))
        self.assertFalse(ignore_expresses_mode(custom_mode_a, track_graphs=True))

    def test_a_negation_un_ignores_the_graph(self) -> None:
        """``*`` then ``!graph.json`` tracks the graph, whatever the blanket says."""
        text = "*\n!.gitignore\n!graph.json\n"
        self.assertTrue(ignore_expresses_mode(text, track_graphs=True))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
