"""Tests for :func:`weld._gitignore_writer.resync_weld_gitignore`.

`write_weld_gitignore` is skip-if-exists by design: a `.weld/.gitignore`
written under an older template never picks up a line the template gained
later, which has now happened five times over (`file-index-state.json`,
`auto-refresh.jsonl`, `graph.write.lock`, `telemetry.jsonl`,
`.enrichment-prompted`) and left the documented remedy manual
(`rm .weld/.gitignore && wd init`). `resync_weld_gitignore` is the
counterpart that runs when the file *does* exist: recognize its content as
one of the three canonical templates (by strict subset -- every line already
present must be a line that template ships today) and append whichever of
that template's current lines are missing.

The load-bearing property across every test below is the same one stated in
the module docstring: recognition is all-or-nothing. A file carrying even
one line resync cannot account for -- a hand-added pattern, a fully foreign
`.gitignore`, an empty/near-empty file -- is left completely untouched
rather than partially completed. That is what makes it safe to run
unattended from `wd init` / `wd workspace bootstrap` on every checkout that
already has a file, including ones a user customised by hand.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._gitattributes_writer import write_repo_git_policy
from weld._gitignore_writer import (
    CONFIG_ONLY_GITIGNORE,
    TRACK_GRAPHS_GITIGNORE,
    missing_gitignore_lines,
    resync_weld_gitignore,
)


def _drop_lines(template: str, *lines_to_drop: str) -> str:
    """*template* with each of *lines_to_drop* removed -- simulates staleness.

    Comment lines are kept verbatim so the fixture still reads like a real
    checkout's file: only the pattern lines a later template revision added
    are missing, exactly the shape a stale `.weld/.gitignore` actually has.
    """
    drop = set(lines_to_drop)
    return "".join(
        line for line in template.splitlines(keepends=True)
        if line.rstrip("\n") not in drop
    )


class ResyncConvergesStaleTemplatesTest(unittest.TestCase):
    """The core promise: a checkout that predates a template line catches up."""

    def test_stale_config_only_checkout_converges(self) -> None:
        stale = _drop_lines(
            CONFIG_ONLY_GITIGNORE, ".enrichment-prompted", "auto-refresh.jsonl",
        )
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            added = resync_weld_gitignore(weld_dir)
            self.assertEqual(
                sorted(added), sorted([".enrichment-prompted", "auto-refresh.jsonl"])
            )
            final = (weld_dir / ".gitignore").read_text(encoding="utf-8")
            # Every originally-present line survives, verbatim, in its
            # original position -- resync only ever appends.
            self.assertTrue(final.startswith(stale))
            # And the missing lines are now present, after it.
            self.assertIn("\n.enrichment-prompted\n", "\n" + final)
            self.assertIn("\nauto-refresh.jsonl\n", "\n" + final)

    def test_stale_track_graphs_checkout_converges(self) -> None:
        stale = _drop_lines(TRACK_GRAPHS_GITIGNORE, ".enrichment-prompted")
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            added = resync_weld_gitignore(weld_dir)
            self.assertEqual(added, [".enrichment-prompted"])
            final = (weld_dir / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("\n.enrichment-prompted\n", "\n" + final)
            # track-graphs mode must still NOT ignore the tracked artifacts.
            self.assertNotIn("\ngraph.json\n", "\n" + final)

    def test_appended_lines_are_exactly_the_missing_set(self) -> None:
        """No over-appending: a line already present is never duplicated."""
        stale = _drop_lines(CONFIG_ONLY_GITIGNORE, "telemetry.jsonl")
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            resync_weld_gitignore(weld_dir)
            final = (weld_dir / ".gitignore").read_text(encoding="utf-8")
            self.assertEqual(final.count("telemetry.jsonl"), 1)


class ResyncNeverTouchesUnrecognizedContentTest(unittest.TestCase):
    """The safety half: anything resync cannot fully account for is untouched."""

    def test_already_current_file_is_not_rewritten(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            target = weld_dir / ".gitignore"
            target.write_text(CONFIG_ONLY_GITIGNORE, encoding="utf-8")
            before = target.stat().st_mtime_ns
            added = resync_weld_gitignore(weld_dir)
            self.assertEqual(added, [])
            self.assertEqual(
                target.read_text(encoding="utf-8"), CONFIG_ONLY_GITIGNORE,
            )
            # No write at all, not even an identical-bytes one.
            self.assertEqual(target.stat().st_mtime_ns, before)

    def test_foreign_content_is_left_completely_untouched(self) -> None:
        custom = "# my own rules\n*.tmp\nnode_modules/\n"
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(custom, encoding="utf-8")
            added = resync_weld_gitignore(weld_dir)
            self.assertEqual(added, [])
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"), custom,
            )

    def test_recognized_lines_plus_one_custom_line_is_untouched(self) -> None:
        """User-added lines are preserved: one foreign line blocks the whole file.

        A file that is *mostly* a recognizable template plus one hand-added
        pattern is exactly the case that must never be partially completed --
        completing it would mean deciding the custom line does not belong
        near the lines resync appends, which is not resync's call to make.
        """
        custom = CONFIG_ONLY_GITIGNORE + "my-private-cache/\n"
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(custom, encoding="utf-8")
            added = resync_weld_gitignore(weld_dir)
            self.assertEqual(added, [])
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"), custom,
            )

    def test_missing_file_is_a_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            self.assertEqual(resync_weld_gitignore(weld_dir), [])
            self.assertFalse((weld_dir / ".gitignore").exists())

    def test_missing_weld_dir_is_a_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / "does" / "not" / "exist" / ".weld"
            self.assertEqual(resync_weld_gitignore(weld_dir), [])
            self.assertFalse(weld_dir.exists())

    def test_near_empty_file_is_not_misread_as_track_graphs(self) -> None:
        """A lone comment vacuously satisfies 'graph.json is not ignored'.

        Regression guard for the classifier: zero pattern lines must never
        be treated as evidence for any mode, or a file like this would be
        completed into a full track-graphs template out of nowhere.
        """
        custom = "# do not touch\n"
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(custom, encoding="utf-8")
            added = resync_weld_gitignore(weld_dir)
            self.assertEqual(added, [])
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"), custom,
            )

    def test_non_utf8_content_is_left_untouched_and_does_not_raise(self) -> None:
        """A file resync cannot even decode must fail closed, not crash `wd init`.

        Skip-if-exists never read the file at all, so a non-UTF-8
        `.weld/.gitignore` was harmless before this mechanism existed.
        Reading it to recognize it must not turn that into a crash.
        """
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            raw = b"\xff\xfe# not valid utf-8\n"
            (weld_dir / ".gitignore").write_bytes(raw)
            added = resync_weld_gitignore(weld_dir)
            self.assertEqual(added, [])
            self.assertEqual((weld_dir / ".gitignore").read_bytes(), raw)


class ResyncEdgeCasesTest(unittest.TestCase):
    def test_idempotent_second_call_is_a_noop(self) -> None:
        stale = _drop_lines(CONFIG_ONLY_GITIGNORE, ".enrichment-prompted")
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            first = resync_weld_gitignore(weld_dir)
            self.assertEqual(first, [".enrichment-prompted"])
            converged = (weld_dir / ".gitignore").read_text(encoding="utf-8")
            second = resync_weld_gitignore(weld_dir)
            self.assertEqual(second, [])
            self.assertEqual(
                (weld_dir / ".gitignore").read_text(encoding="utf-8"), converged,
            )

    def test_missing_trailing_newline_is_handled(self) -> None:
        stale = _drop_lines(
            CONFIG_ONLY_GITIGNORE, ".enrichment-prompted",
        ).rstrip("\n")
        self.assertFalse(stale.endswith("\n"))
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            added = resync_weld_gitignore(weld_dir)
            self.assertEqual(added, [".enrichment-prompted"])
            final = (weld_dir / ".gitignore").read_text(encoding="utf-8")
            # The last original line must not have been glued to what follows.
            self.assertIn("\nreview-state.json\n", "\n" + final)
            self.assertIn("\n.enrichment-prompted\n", "\n" + final)


class MissingGitignoreLinesTest(unittest.TestCase):
    """Direct coverage for the shared, pure computation both
    :func:`resync_weld_gitignore` above and the read-only `wd doctor` check
    in :mod:`weld._doctor_gitignore` call. Takes text, not a path, and
    touches no filesystem -- the two callers differ only in what they do
    with the answer (one writes it, one reports it).
    """

    def test_returns_the_missing_set_in_template_order(self) -> None:
        stale = _drop_lines(
            CONFIG_ONLY_GITIGNORE, ".enrichment-prompted", "auto-refresh.jsonl",
        )
        # Exact order, not just set equality: CONFIG_ONLY_GITIGNORE ships
        # `auto-refresh.jsonl` ahead of `.enrichment-prompted`, and the
        # docstring promises the recognized template's own order.
        self.assertEqual(
            missing_gitignore_lines(stale),
            ["auto-refresh.jsonl", ".enrichment-prompted"],
        )

    def test_current_template_has_nothing_missing(self) -> None:
        self.assertEqual(missing_gitignore_lines(CONFIG_ONLY_GITIGNORE), [])

    def test_foreign_content_is_unrecognized(self) -> None:
        self.assertEqual(
            missing_gitignore_lines("# my own rules\n*.tmp\n"), [],
        )

    def test_empty_text_is_unrecognized(self) -> None:
        self.assertEqual(missing_gitignore_lines(""), [])


class WriteRepoGitPolicyResyncsExistingFilesTest(unittest.TestCase):
    """The choke point every caller goes through: `wd init` / bootstrap."""

    def test_resyncs_an_existing_stale_config_only_file(self) -> None:
        stale = _drop_lines(CONFIG_ONLY_GITIGNORE, ".enrichment-prompted")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            write_repo_git_policy(root, weld_dir, announce=False)
            final = (weld_dir / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("\n.enrichment-prompted\n", "\n" + final)

    def test_announces_the_resync_on_stderr(self) -> None:
        stale = _drop_lines(CONFIG_ONLY_GITIGNORE, ".enrichment-prompted")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                write_repo_git_policy(root, weld_dir, announce=True)
            self.assertIn("Resynced", stderr.getvalue())
            self.assertIn(str(weld_dir / ".gitignore"), stderr.getvalue())

    def test_silent_when_announce_is_false(self) -> None:
        stale = _drop_lines(CONFIG_ONLY_GITIGNORE, ".enrichment-prompted")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / ".gitignore").write_text(stale, encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                write_repo_git_policy(root, weld_dir, announce=False)
            self.assertEqual(stderr.getvalue(), "")

    def test_does_not_resync_a_freshly_created_file(self) -> None:
        """A brand-new file is already current: nothing to announce."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                write_repo_git_policy(root, weld_dir, announce=True)
            self.assertNotIn("Resynced", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
