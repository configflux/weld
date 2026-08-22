"""``--track-graphs`` is judged by git, not by weld's own ignore file (bd jya6).

bd ilax made ``wd init --track-graphs`` refuse when the *managed*
``.weld/.gitignore`` contradicts the requested mode. That check reads one
file, and a repository's ignore stack has several. A repo whose **root**
``.gitignore`` carries ``.weld/`` -- the line people write before they learn
weld ships its own policy file -- got Mode B declared and none of it applied:
git never saw ``graph.json``, ``discovery-state.json`` or ``file-index.json``,
so the clone that was supposed to arrive warm arrived with nothing, and weld
reported success. Same class as ilax, one layer out.

The fix asks git instead of parsing. ``git check-ignore`` answers for the
whole stack at once -- root ``.gitignore``, ``.git/info/exclude``, the global
``core.excludesFile``, and weld's managed file -- with git's real precedence
rules, on paths that do not exist yet, and it names the offending rule with a
line number no hand parser could supply.

Three properties are pinned here, and the third is the one that keeps the fix
from being worse than the bug:

1. A repository that hides ``.weld/`` from outside the managed file fails
   ``--track-graphs``, exits 1, and names the file, line and pattern.
2. The managed-file case still fails, still names the managed file, and still
   gets the "delete it and re-run" remedy -- a foreign rule gets the opposite
   advice, because it is the user's rule and not weld's to rewrite.
3. Nothing that worked before starts failing: a clean repo, a directory that
   is not a git checkout at all (``wd init`` is supported there), and a repo
   where ``graph.json`` is *already tracked* under a ``.weld/`` rule -- git
   keeps committing a tracked file whatever the ignore rules say, so that
   repository is in Mode B and must not be refused.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._gitattributes_writer import write_repo_git_policy
from weld._gitignore_probe import UNANSWERED, check_ignore
from weld.init import main as init_main

_GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True,
        check=True, env=_GIT_ENV,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")


def _run_init(*argv: str) -> tuple[int, str]:
    """``wd init`` with *argv*: its exit status and everything it said."""
    stderr = io.StringIO()
    code = 0
    with contextlib.redirect_stderr(stderr):
        try:
            init_main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, stderr.getvalue()


class CheckIgnoreProbeTest(unittest.TestCase):
    """The probe itself: what git says, and how it is reported."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        _init_repo(self.root)
        self.graph = self.root / ".weld" / "graph.json"

    def test_nothing_ignores_it(self) -> None:
        verdict = check_ignore(self.root, self.graph)
        self.assertTrue(verdict.answered)
        self.assertFalse(verdict.ignored)
        self.assertIsNone(verdict.rule)

    def test_a_root_gitignore_rule_is_found_and_named(self) -> None:
        """The reported bug, at the layer that detects it."""
        (self.root / ".gitignore").write_text(
            "build/\n.weld/\n*.pyc\n", encoding="utf-8")
        verdict = check_ignore(self.root, self.graph)
        self.assertTrue(verdict.answered)
        self.assertTrue(verdict.ignored)
        # File, line and pattern -- the line number is why git is asked rather
        # than parsed, and the absolute path is what makes it actionable in a
        # repo with several .gitignore files.
        self.assertEqual(
            verdict.rule, f"{self.root / '.gitignore'}:2:.weld/")

    def test_the_managed_file_is_named_the_same_way(self) -> None:
        (self.root / ".weld").mkdir()
        (self.root / ".weld" / ".gitignore").write_text(
            "graph-meta.json\ngraph.json\n", encoding="utf-8")
        verdict = check_ignore(self.root, self.graph)
        self.assertEqual(
            verdict.rule,
            f"{self.root / '.weld' / '.gitignore'}:2:graph.json")

    def test_the_info_exclude_layer_is_covered(self) -> None:
        """No ``.gitignore`` involved at all -- the layer a parser cannot see."""
        (self.root / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (self.root / ".git" / "info" / "exclude").write_text(
            ".weld/\n", encoding="utf-8")
        verdict = check_ignore(self.root, self.graph)
        self.assertTrue(verdict.ignored)
        self.assertIn(".weld/", verdict.rule or "")

    def test_a_negation_under_an_excluded_directory_does_not_re_include(self) -> None:
        """Git's rule, which is why the parser was the wrong tool.

        ``!.weld/graph.json`` reads like an exemption and is not one: a
        negation cannot re-include a file whose parent directory is excluded.
        The text predicate reads ``!graph.json`` as un-ignoring the graph;
        git knows better.
        """
        (self.root / ".gitignore").write_text(
            ".weld/\n!.weld/graph.json\n", encoding="utf-8")
        self.assertTrue(check_ignore(self.root, self.graph).ignored)

    def test_an_already_tracked_graph_is_not_ignored(self) -> None:
        """The index is consulted on purpose.

        Git keeps committing a tracked file whatever the ignore rules say, so
        a repository that already tracks ``graph.json`` *is* in Mode B and
        must not be refused. ``--no-index`` would answer a different,
        hypothetical question.
        """
        (self.root / ".gitignore").write_text(".weld/\n", encoding="utf-8")
        self.graph.parent.mkdir(parents=True, exist_ok=True)
        self.graph.write_text("{}\n", encoding="utf-8")
        _git(self.root, "add", "-f", ".weld/graph.json")
        self.assertFalse(check_ignore(self.root, self.graph).ignored)

    def test_outside_a_checkout_git_is_not_asked_to_answer(self) -> None:
        """``wd init`` is supported outside a repo and must not start failing."""
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        self.assertEqual(
            check_ignore(plain, plain / ".weld" / "graph.json"), UNANSWERED)


class TrackGraphsIsJudgedByTheWholeIgnoreStackTest(unittest.TestCase):
    """``wd init --track-graphs`` end to end, through every layer."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        _init_repo(self.root)

    def test_a_root_rule_hiding_weld_fails_and_names_the_rule(self) -> None:
        """The acceptance criterion: exit non-zero, naming what hides them."""
        (self.root / ".gitignore").write_text(
            "build/\n.weld/\n", encoding="utf-8")
        code, err = _run_init(str(self.root), "--track-graphs")
        self.assertEqual(
            code, 1,
            f"a repository that hides .weld/ reported Mode B success:\n{err}")
        self.assertIn(f"{self.root / '.gitignore'}:2:.weld/", err)
        self.assertIn("--track-graphs", err)
        self.assertIn("graph-tracking-policy.md", err)

    def test_a_foreign_rule_is_not_something_weld_offers_to_delete(self) -> None:
        """The remedy differs from the managed file's, and must.

        ``rm .weld/.gitignore`` is weld's own documented switch procedure.
        Telling somebody to delete their repository's ``.gitignore`` is not.
        """
        (self.root / ".gitignore").write_text(".weld/\n", encoding="utf-8")
        _, err = _run_init(str(self.root), "--track-graphs")
        self.assertIn("Remove or narrow that rule", err)
        self.assertNotIn(f"Delete {self.root / '.gitignore'}", err)

    def test_the_managed_file_keeps_its_own_remedy(self) -> None:
        """bd ilax's case, unchanged, now with the line number."""
        _run_init(str(self.root))
        managed = self.root / ".weld" / ".gitignore"
        code, err = _run_init(str(self.root), "--track-graphs")
        self.assertEqual(code, 1)
        self.assertIn(str(managed), err)
        self.assertIn(f"Delete {managed}", err)
        self.assertNotIn("Remove or narrow that rule", err)

    def test_a_clean_repository_still_succeeds(self) -> None:
        code, err = _run_init(str(self.root), "--track-graphs")
        self.assertEqual(code, 0, err)
        self.assertFalse(
            check_ignore(self.root, self.root / ".weld" / "graph.json").ignored)

    def test_a_non_git_directory_still_succeeds(self) -> None:
        """Falls back to the managed-file predicate rather than refusing."""
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        (plain / "a.py").write_text("x = 1\n", encoding="utf-8")
        code, err = _run_init(str(plain), "--track-graphs")
        self.assertEqual(code, 0, err)

    def test_an_already_tracked_graph_still_succeeds(self) -> None:
        (self.root / ".gitignore").write_text(".weld/\n", encoding="utf-8")
        graph = self.root / ".weld" / "graph.json"
        graph.parent.mkdir(parents=True, exist_ok=True)
        graph.write_text("{}\n", encoding="utf-8")
        _git(self.root, "add", "-f", ".weld/graph.json")
        code, err = _run_init(str(self.root), "--track-graphs")
        self.assertEqual(
            code, 0,
            "a repository already committing graph.json was refused Mode B, "
            f"which it is already in:\n{err}")

    def test_bare_init_under_the_same_rule_is_unaffected(self) -> None:
        """No mode flag is no request, so there is nothing to be refused.

        Somebody who wrote ``.weld/`` in their root ``.gitignore`` has asked
        for exactly what the default policy gives them. Widening the refusal
        to the default would fail an init that is doing what was wanted.
        """
        (self.root / ".gitignore").write_text(".weld/\n", encoding="utf-8")
        code, err = _run_init(str(self.root))
        self.assertEqual(code, 0, err)

    def test_ignore_all_is_not_re_judged_by_git(self) -> None:
        """An outer rule that hides more of ``.weld/`` delivers that request."""
        (self.root / ".gitignore").write_text(".weld/\n", encoding="utf-8")
        code, err = _run_init(str(self.root), "--ignore-all")
        self.assertEqual(code, 0, err)

    def test_a_hostile_pattern_reaches_the_terminal_escaped(self) -> None:
        """The rule is *file content*, so it is escaped before it is printed.

        Naming the culprit means putting a line somebody else wrote onto the
        operator's terminal, and a ``.gitignore`` pattern can carry live
        control bytes while still matching: a character class is enough
        (``.weld/[g<ESC>]raph.json`` matches ``graph.json``). Unescaped, the
        failure diagnostic would clear the screen or overwrite itself with
        text that misrepresents what init found -- exactly the boundary
        :mod:`weld._safe_text` exists to hold.
        """
        (self.root / ".gitignore").write_text(
            ".weld/[g\x1b[2Jr]raph.json\n", encoding="utf-8")
        verdict = check_ignore(self.root, self.root / ".weld" / "graph.json")
        self.assertTrue(
            verdict.ignored,
            "fixture drifted: the hostile pattern no longer matches, so this "
            "test would pass without proving anything",
        )
        self.assertIn("\x1b", verdict.rule or "",
                      "fixture drifted: the rule carries no control byte")
        _, err = _run_init(str(self.root), "--track-graphs")
        self.assertNotIn(
            "\x1b", err,
            "a raw ESC from a .gitignore pattern reached stderr",
        )
        self.assertIn("\\x1b", err, "the escape should be shown, not stripped")


class ManagedPolicyCarriesItsReasonTest(unittest.TestCase):
    """The verdict and its cause travel together, from one probe."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        _init_repo(self.root)

    def test_blocked_policy_reports_the_rule(self) -> None:
        (self.root / ".gitignore").write_text(".weld/\n", encoding="utf-8")
        policy = write_repo_git_policy(
            self.root, self.root / ".weld", track_graphs=True, announce=False,
        )
        self.assertFalse(policy.in_effect)
        self.assertEqual(policy.blocking_rule, f"{self.root / '.gitignore'}:1:.weld/")

    def test_an_effective_policy_reports_no_rule(self) -> None:
        policy = write_repo_git_policy(
            self.root, self.root / ".weld", track_graphs=True, announce=False,
        )
        self.assertTrue(policy.in_effect)
        self.assertIsNone(policy.blocking_rule)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
